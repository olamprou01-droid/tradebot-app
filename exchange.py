"""
Every Kraken call in one place. Public endpoints need no key.
"""
import base64, hashlib, hmac, time, urllib.parse
import requests
import config

BASE = "https://api.kraken.com"
_s = requests.Session()
_s.headers["User-Agent"] = "tradebot/0.1"


def _public(method: str, **params) -> dict:
    r = _s.get(f"{BASE}/0/public/{method}", params=params, timeout=15)
    r.raise_for_status()
    j = r.json()
    if j.get("error"):
        raise RuntimeError(f"Kraken {method}: {j['error']}")
    return j["result"]


def _private(method: str, **data) -> dict:
    if not config.API_KEY:
        raise RuntimeError("private call without API key")
    path = f"/0/private/{method}"
    data["nonce"] = str(int(time.time() * 1000))
    post = urllib.parse.urlencode(data)
    sha = hashlib.sha256((data["nonce"] + post).encode()).digest()
    sig = hmac.new(base64.b64decode(config.API_SECRET), path.encode() + sha, hashlib.sha512).digest()
    r = _s.post(BASE + path, data=data, timeout=15, headers={
        "API-Key": config.API_KEY, "API-Sign": base64.b64encode(sig).decode()})
    r.raise_for_status()
    j = r.json()
    if j.get("error"):
        raise RuntimeError(f"Kraken {method}: {j['error']}")
    return j["result"]


# ---- public ----
def server_time() -> int:
    return int(_public("Time")["unixtime"])


def ticker(pair: str = None) -> dict:
    res = _public("Ticker", pair=pair or config.PAIR)
    k, t = next(iter(res.items()))
    return {"pair": k, "last": float(t["c"][0]), "bid": float(t["b"][0]), "ask": float(t["a"][0]),
            "vol24h": float(t["v"][1])}


def ohlc(pair: str, interval: int = 60, since: int = None) -> list:
    """interval minutes: 1,5,15,30,60,240,1440,10080. Returns oldest→newest
    [ts, open, high, low, close, vwap, volume, count]. Kraken caps at 720 rows per call."""
    kw = {"pair": pair, "interval": interval}
    if since:
        kw["since"] = since
    res = _public("OHLC", **kw)
    rows = next(v for k, v in res.items() if k != "last")
    return [[int(r[0])] + [float(x) for x in r[1:7]] + [int(r[7])] for r in rows]


def asset_pair(pair: str) -> dict:
    res = _public("AssetPairs", pair=pair)
    k, p = next(iter(res.items()))
    return {"pair": k, "altname": p["altname"], "ordermin": float(p["ordermin"]),
            "lot_decimals": p["lot_decimals"], "pair_decimals": p["pair_decimals"]}


# ---- private (live only) ----
def balance() -> dict:
    return {k: float(v) for k, v in _private("Balance").items()}


def open_orders() -> dict:
    return _private("OpenOrders")["open"]
