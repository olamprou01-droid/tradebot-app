"""
Builds <HISTORY_DIR>/<PAIR>_{60,240,1440}.csv for backtesting.

History source: Binance monthly kline archives (real exchange data, public, no key).
Live/paper source: Kraken. The script measures the Kraken-vs-history close gap on the
overlapping last 30 days so the assumption is checked, not assumed (USD vs USDT basis ≈ 10 bps).
Kraken's own API only returns 720 candles per interval, too short for a backtest.

Env: HISTORY_DIR (default ./data), HISTORY_START (default 2022-01-01). Existing pairs are skipped
unless --refresh is given.
"""
import csv, io, os, sys, zipfile, datetime as dt, pathlib, statistics
import requests
sys.stdout.reconfigure(encoding="utf-8")

OUT = pathlib.Path(os.getenv("HISTORY_DIR", pathlib.Path(__file__).parent / "data"))
OUT.mkdir(parents=True, exist_ok=True)
START = dt.date.fromisoformat(os.getenv("HISTORY_START", "2022-01-01"))
PAIRS = {"XBTUSD": "BTCUSDT", "ETHUSD": "ETHUSDT", "SOLUSD": "SOLUSDT", "XRPUSD": "XRPUSDT",
         "ADAUSD": "ADAUSDT", "LINKUSD": "LINKUSDT", "DOTUSD": "DOTUSDT", "AVAXUSD": "AVAXUSDT",
         "LTCUSD": "LTCUSDT", "XDGUSD": "DOGEUSDT"}   # XDGUSD = Kraken's code for DOGE
BASE = "https://data.binance.vision/data/spot/monthly/klines/{sym}/1h/{sym}-1h-{y}-{m:02d}.zip"


def months(start: dt.date):
    d = start.replace(day=1)
    last = dt.date.today().replace(day=1)
    while d < last:
        yield d.year, d.month
        d = (d.replace(day=28) + dt.timedelta(days=4)).replace(day=1)


def fetch_1h(sym: str) -> list:
    rows, missing = [], 0
    for y, m in months(START):
        url = BASE.format(sym=sym, y=y, m=m)
        try:
            r = requests.get(url, timeout=60)
        except Exception as e:
            print(f"  {y}-{m:02d} failed: {e}"); continue
        if r.status_code != 200:
            missing += 1
            if missing <= 3 or missing % 12 == 0: print(f"  missing {y}-{m:02d} ({r.status_code})")
            continue
        with zipfile.ZipFile(io.BytesIO(r.content)) as z:
            with z.open(z.namelist()[0]) as f:
                for line in io.TextIOWrapper(f):
                    c = line.split(",")
                    ts = int(c[0])
                    while ts > 10**10: ts //= 1000          # ms / us timestamps in newer files
                    rows.append([ts, float(c[1]), float(c[2]), float(c[3]), float(c[4]), float(c[5])])
        if m == 12 or (y, m) == list(months(START))[-1]: print(f"  {sym} {y}-{m:02d} ok ({len(rows)} rows)")
    rows.sort()
    return rows


def resample(rows: list, minutes: int) -> list:
    step = minutes * 60
    out, cur = [], None
    for ts, o, h, l, c, v in rows:
        b = ts - ts % step
        if cur is None or cur[0] != b:
            if cur: out.append(cur)
            cur = [b, o, h, l, c, v]
        else:
            cur[2] = max(cur[2], h); cur[3] = min(cur[3], l); cur[4] = c; cur[5] += v
    if cur: out.append(cur)
    return out


def write(pair: str, minutes: int, rows: list):
    p = OUT / f"{pair}_{minutes}.csv"
    with open(p, "w", newline="") as f:
        w = csv.writer(f); w.writerow(["ts", "open", "high", "low", "close", "volume"]); w.writerows(rows)
    print(f"  wrote {p.name}: {len(rows)} rows, {dt.datetime.fromtimestamp(rows[0][0], dt.timezone.utc):%Y-%m-%d} -> {dt.datetime.fromtimestamp(rows[-1][0], dt.timezone.utc):%Y-%m-%d}")


def kraken_overlap_check(pair: str, rows: list):
    import exchange
    k = {r[0]: r[4] for r in exchange.ohlc(pair, 60)}     # last 720 1H candles
    b = {r[0]: r[4] for r in rows}
    diffs = [abs(k[t] - b[t]) / k[t] * 1e4 for t in k if t in b]
    if diffs:
        print(f"  Kraken vs history close gap over {len(diffs)} bars: median {statistics.median(diffs):.1f} bps, max {max(diffs):.1f} bps")
    else:
        print("  WARNING no overlap with Kraken candles")


if __name__ == "__main__":
    for pair, sym in PAIRS.items():
        print(pair, flush=True)
        if (OUT / f"{pair}_60.csv").exists() and "--refresh" not in sys.argv:
            print("  already on disk, skipping (run with --refresh to redo)"); continue
        h1 = fetch_1h(sym)
        if not h1:
            print(f"  no data for {pair}"); continue
        write(pair, 60, h1)
        write(pair, 240, resample(h1, 240))
        write(pair, 1440, resample(h1, 1440))
        try: kraken_overlap_check(pair, h1)
        except Exception as e: print(f"  overlap check skipped: {e}")
    print("\nDATA READY")
