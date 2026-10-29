#!/usr/bin/env python3
"""Check Yamazaki 12 (700ml) stock and prices at a few Japanese shops and write data/status.json.

Runs in GitHub Actions. Standard library only. Every source is optional: when a fetch or
parse fails, the previous values are kept and the source is marked stale, never invented.
"""
import datetime as dt
import gzip
import json
import os
import re
import sys
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STATUS = os.path.join(ROOT, "data", "status.json")
HISTORY = os.path.join(ROOT, "data", "history.json")
JST = dt.timezone(dt.timedelta(hours=9))
NOW = dt.datetime.now(JST)
UA = ("Mozilla/5.0 (iPhone; CPU iPhone OS 17_5 like Mac OS X) AppleWebKit/605.1.15 "
      "(KHTML, like Gecko) Version/17.5 Mobile/15E148 Safari/604.1")
DEBUG = os.environ.get("DEBUG") == "1"


def log(*a):
    print(*a, flush=True)


def fetch(url, timeout=25):
    req = urllib.request.Request(url, headers={
        "User-Agent": UA,
        "Accept-Language": "ja,en;q=0.8",
        "Accept": "text/html,application/json;q=0.9,*/*;q=0.8",
        "Accept-Encoding": "gzip",
    })
    with urllib.request.urlopen(req, timeout=timeout) as r:
        raw = r.read()
        if r.headers.get("Content-Encoding") == "gzip":
            raw = gzip.decompress(raw)
        charset = r.headers.get_content_charset() or "utf-8"
    for enc in (charset, "utf-8", "cp932", "euc-jp"):
        try:
            return raw.decode(enc)
        except (LookupError, UnicodeDecodeError):
            continue
    return raw.decode("utf-8", "replace")


def yen(s):
    try:
        return int(str(s).replace(",", "").replace("¥", "").replace("￥", "").strip().split(".")[0])
    except ValueError:
        return None


def plausible(p):
    return p is not None and 12000 <= p <= 80000


def jsonld_offers(html):
    """Yield (price, availability) pairs from JSON-LD blocks."""
    for block in re.findall(r'<script[^>]+application/ld\+json[^>]*>(.*?)</script>', html, re.S | re.I):
        try:
            data = json.loads(block.strip())
        except json.JSONDecodeError:
            continue
        stack = [data]
        while stack:
            x = stack.pop()
            if isinstance(x, list):
                stack.extend(x)
            elif isinstance(x, dict):
                if "price" in x or "lowPrice" in x:
                    yield (yen(x.get("price") or x.get("lowPrice")), str(x.get("availability", "")), x)
                stack.extend(v for v in x.values() if isinstance(v, (dict, list)))


def snippet(html, words, width=160):
    out = []
    for w in words:
        i = html.find(w)
        if i >= 0:
            out.append(re.sub(r"\s+", " ", html[max(0, i - width // 2): i + width // 2]))
    return out


# ---------------------------------------------------------------- sources

def check_linxas():
    url = "https://linx-as.store/products/suntory-yamazaki-12y.js"
    p = json.loads(fetch(url))
    variants = []
    for v in p.get("variants", []):
        price = v.get("price")
        if isinstance(price, (int, float)) and price > 200000:  # Shopify .js prices are in 1/100 units
            price = int(price / 100)
        variants.append({"title": v.get("title") or v.get("public_title"), "price": price, "available": bool(v.get("available"))})
    def pick(pred):
        c = [v for v in variants if plausible(v["price"]) and pred(v)]
        return min(c, key=lambda v: v["price"]) if c else None
    nobox = pick(lambda v: v["title"] and ("なし" in v["title"] or "無" in v["title"])) or pick(lambda v: True)
    box = pick(lambda v: v["title"] and ("箱付" in v["title"] or "あり" in v["title"] or "カートン" in v["title"]))
    res = {
        "price": nobox["price"] if nobox else None,
        "priceBox": box["price"] if box else None,
        "available": any(v["available"] for v in variants),
        "variants": variants,
    }
    try:
        col = fetch("https://linx-as.store/collections/yamazaki")
        m = re.search(r"在庫あり\s*([0-9,]+)\s*本", col)
        if m:
            res["count"] = yen(m.group(1))
    except Exception as e:  # count is a bonus
        log("linxas count failed:", e)
    if not plausible(res["price"]):
        raise ValueError(f"no plausible price in {variants}")
    return res


def check_licasta():
    url = "https://store.shopping.yahoo.co.jp/licastatokyo/yamazaki-12yo.html"
    html = fetch(url)
    prices, avail = [], ""
    for price, availability, _ in jsonld_offers(html):
        if plausible(price):
            prices.append(price)
            avail = avail or availability
    if not prices:
        for m in re.finditer(r'"price"\s*:\s*"?([0-9,]{5,7})', html):
            if plausible(yen(m.group(1))):
                prices.append(yen(m.group(1)))
    if not prices:
        prices = [yen(m) for m in re.findall(r"([0-9]{2},[0-9]{3})\s*円", html) if plausible(yen(m))]
    if DEBUG:
        log("licasta snippets:", snippet(html, ["在庫", "price", "円"]))
    if not prices:
        raise ValueError("no price found")
    sold = bool(re.search(r"在庫切れ|売り切れ|品切れ|SoldOut|OutOfStock", avail + " " + html[:200000]))
    instock = "InStock" in avail or "在庫あり" in html
    return {"price": min(prices), "available": (not sold) if not instock else True}


def check_joylab():
    out = {}
    for key, url in (("main", "https://store-moment.jp/shopdetail/000000000064/"),
                     ("outlet", "https://store-moment.jp/view/item/000000004855")):
        try:
            html = fetch(url)
            prices = [p for p, _, _ in jsonld_offers(html) if plausible(p)]
            if not prices:
                for pat in (r"販売価格[^0-9]{0,40}([0-9]{2},[0-9]{3})", r"([0-9]{2},[0-9]{3})\s*円\s*[（(]税込"):
                    prices += [yen(m) for m in re.findall(pat, html) if plausible(yen(m))]
            if not prices:
                prices = [yen(m) for m in re.findall(r"([0-9]{2},[0-9]{3})\s*円", html) if plausible(yen(m))]
            cart = bool(re.search(r"カートに入れる|カートへ|cart_in|add_cart|btn_cart", html, re.I))
            sold = bool(re.search(r"SOLD\s*OUT|売り切れ|品切れ|在庫切れ|在庫がありません", html, re.I))
            if DEBUG:
                log("joylab", key, "cart", cart, "sold", sold, snippet(html, ["販売価格", "SOLD", "カート"]))
            out[key] = {"price": prices[0] if prices else None, "available": True if cart else (False if sold else None)}
        except Exception as e:
            log("joylab", key, "failed:", e)
    if not out or not any(plausible(v.get("price")) for v in out.values()):
        raise ValueError(f"joylab parse failed {out}")
    m, o = out.get("main", {}), out.get("outlet", {})
    return {"price": m.get("price"), "available": m.get("available"),
            "outletPrice": o.get("price"), "outletAvailable": o.get("available")}


def check_market():
    res = {}
    try:
        html = fetch("https://shopping.yahoo.co.jp/products/f566df80ac")
        lows = [p for p, _, x in jsonld_offers(html) if plausible(p)]
        if not lows:
            m = re.search(r'"lowPrice"\s*:\s*"?([0-9,]+)', html)
            if m and plausible(yen(m.group(1))):
                lows = [yen(m.group(1))]
        if lows:
            res["yahooLow"] = min(lows)
    except Exception as e:
        log("yahoo compare failed:", e)
    try:
        html = fetch("https://kakaku.com/item/K0000695153/")
        m = re.search(r'"lowPrice"\s*:\s*"?([0-9,]+)', html) or re.search(r"最安価格[^0-9]{0,40}([0-9,]{6,7})", html)
        if m and plausible(yen(m.group(1))):
            res["kakakuLow"] = yen(m.group(1))
    except Exception as e:
        log("kakaku failed:", e)
    try:
        html = fetch("https://amademeshi.info/price_yamazaki12/")
        m = re.search(r"現在[^0-9]{0,30}([0-9]{2},[0-9]{3})", html) or re.search(r"([0-9]{2},[0-9]{3})\s*円", html)
        if m and plausible(yen(m.group(1))):
            res["amazon"] = yen(m.group(1))
    except Exception as e:
        log("amademeshi failed:", e)
    if not res:
        raise ValueError("no market source answered")
    return res


SOURCES = {"linxas": check_linxas, "licasta": check_licasta, "joylab": check_joylab, "market": check_market}


def in_window():
    """Hourly schedule is wide; only act during the trip (JST 11/19–21, 07–23h)."""
    cron = os.environ.get("CRON", "")
    if os.environ.get("EVENT") != "schedule" or "* 18-21 11" not in cron:
        return True
    return NOW.month == 11 and 19 <= NOW.day <= 21 and 7 <= NOW.hour <= 23


def main():
    if not in_window():
        log("outside trip window, skipping", NOW.isoformat())
        return 0
    try:
        status = json.load(open(STATUS, encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        status = {"sources": {}}
    status.setdefault("sources", {})
    ok_any = False
    for name, fn in SOURCES.items():
        prev = status["sources"].get(name, {})
        try:
            data = fn()
            data.update({"ok": True, "checkedAt": NOW.isoformat(timespec="minutes"), "error": None})
            status["sources"][name] = data
            ok_any = True
            log("OK", name, json.dumps(data, ensure_ascii=False)[:400])
        except Exception as e:
            prev.update({"ok": False, "error": f"{type(e).__name__}: {e}"[:300], "failedAt": NOW.isoformat(timespec="minutes")})
            status["sources"][name] = prev
            log("FAIL", name, e)
    status["updatedAt"] = NOW.isoformat(timespec="minutes")
    with open(STATUS, "w", encoding="utf-8") as f:
        json.dump(status, f, ensure_ascii=False, indent=1)

    # one history row per JST day
    try:
        hist = json.load(open(HISTORY, encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        hist = []
    s = status["sources"]
    row = {"date": NOW.strftime("%Y-%m-%d")}
    for key, val in (("yahoo", s.get("market", {}).get("yahooLow")), ("kakaku", s.get("market", {}).get("kakakuLow")),
                     ("amazon", s.get("market", {}).get("amazon")), ("linxas", s.get("linxas", {}).get("price")),
                     ("licasta", s.get("licasta", {}).get("price")), ("joylab", s.get("joylab", {}).get("price"))):
        if plausible(val):
            row[key] = val
    hist = [h for h in hist if h.get("date") != row["date"]] + [row]
    hist.sort(key=lambda h: h["date"])
    with open(HISTORY, "w", encoding="utf-8") as f:
        json.dump(hist[-120:], f, ensure_ascii=False, indent=1)
    return 0 if ok_any else 1


if __name__ == "__main__":
    sys.exit(main())
