"""
Challenge-attempt replay: run the strategy through history as back-to-back Kraken Funded attempts.
Each attempt starts at $10,000 and ends at +12% (PASS) or -3% (KILL, static from start); the next
attempt starts right after. Portfolio rules as in production (max 2 open, 1 per coin, daily/weekly caps).

    python attempts.py [data_dir] [--out report.md]

Variants tested: base, parameter changes that looked better in the sensitivity table, a market-breadth
filter (only trade when >= k of the coins are in a daily uptrend), and a risk ratchet (risk grows only
once the account has a profit cushion above the fixed kill line).
"""
import sys, json, math, pathlib, datetime as dt
import numpy as np, pandas as pd
import backtest as b, strategies as S

DATA = pathlib.Path(sys.argv[1]) if len(sys.argv) > 1 and not sys.argv[1].startswith("--") else b.DATA
OUT = pathlib.Path(sys.argv[sys.argv.index("--out") + 1]) if "--out" in sys.argv else b.HERE / "attempts_report.md"
b.DATA = DATA
PAIRS = ["ETHUSD", "SOLUSD", "LINKUSD", "XRPUSD"]
START, TARGET, KILL = 10_000.0, 0.12, 0.03
FEE, SLIP, FUND = b.FEE, b.SLIP, 0.000055
OOS_FROM = pd.Timestamp("2025-01-01").timestamp()


def regime_table():
    """per day: how many of the basket coins are in a daily uptrend (breadth), BTC close for context."""
    cols = {}
    for p in PAIRS + ["XBTUSD"]:
        f = DATA / f"{p}_1440.csv"
        if not f.exists(): continue
        d = pd.read_csv(f); d["ema50"] = b.ema(d.close, 50)
        up = ((d.close > d.ema50) & (d.ema50 > d.ema50.shift(10))).astype(int)
        cols[p] = pd.Series(up.values, index=d.ts.values)
        if p == "XBTUSD": cols["btc"] = pd.Series(d.close.values, index=d.ts.values)
    df = pd.DataFrame(cols).sort_index().ffill().fillna(0)
    df["breadth"] = df[[p for p in PAIRS if p in df]].sum(axis=1)
    return df


def breadth_at(reg, ts):
    i = reg.index.searchsorted(ts, side="right") - 1
    return int(reg.breadth.iloc[max(i, 0)])


def replay(trades, risk_fn, reg, min_breadth=0, max_open=2):
    """sequential attempts over the real trade sequence. risk_fn(equity) -> risk fraction."""
    trades = sorted(trades, key=lambda t: t["ts_open"])
    attempts, cur = [], None
    eq, open_pos = START, {}
    day, day_pnl, week, week_pnl = None, 0.0, None, 0.0

    def new_attempt(ts):
        return dict(start=ts, eq=START, trades=0, wins=0, R=[], breadth=[], vol=[])

    def settle(t_now):
        nonlocal eq, day_pnl, week_pnl
        for k in [k for k, v in open_pos.items() if v["ts_close"] <= t_now]:
            v = open_pos.pop(k); eq += v["net"]; day_pnl += min(0, v["net"]); week_pnl += min(0, v["net"])
            cur["trades"] += 1; cur["wins"] += v["net"] > 0; cur["R"].append(v["net"] / v["risk_usd"])
            cur["eq"] = eq
            if eq >= START * (1 + TARGET): return "PASS", v["ts_close"]
            if eq <= START * (1 - KILL): return "KILL", v["ts_close"]
        return None, None

    for t in trades:
        if cur is None: cur = new_attempt(t["ts_open"])
        res, ts_end = settle(t["ts_open"])
        if res:
            cur.update(result=res, end=ts_end, final=eq); attempts.append(cur)
            cur, eq, open_pos = new_attempt(ts_end), START, {}          # positions of the ended attempt are dropped
            day_pnl = week_pnl = 0.0
        d = dt.datetime.fromtimestamp(t["ts_open"], dt.timezone.utc)
        if d.date() != day: day, day_pnl = d.date(), 0.0
        wk = d.strftime("%G-W%V")
        if wk != week: week, week_pnl = wk, 0.0
        if t["pair"] in open_pos or len(open_pos) >= max_open: continue
        if -day_pnl >= 0.01 * eq or -week_pnl >= 0.015 * eq: continue
        bth = breadth_at(reg, t["ts_open"])
        if bth < min_breadth: continue
        risk = risk_fn(eq)
        size = eq * risk / t["r"]
        gross = size * (t["exit"] - t["entry"])
        cost = size * (t["entry"] + t["exit"]) * FEE + size * t["entry"] * FUND * (t["hold_h"] / 4)
        open_pos[t["pair"]] = dict(t, net=gross - cost, risk_usd=size * t["r"])
        cur["breadth"].append(bth); cur["vol"].append(t.get("vol_ratio", np.nan))
    if cur and cur["trades"]:
        res, ts_end = settle(10**12)
        cur.update(result=res or "OPEN", end=ts_end or trades[-1]["ts_close"], final=eq); attempts.append(cur)
    return attempts


def summarize(name, attempts, reg):
    done = [a for a in attempts if a["result"] in ("PASS", "KILL")]
    passes = [a for a in done if a["result"] == "PASS"]; kills = [a for a in done if a["result"] == "KILL"]
    def days(a): return (a["end"] - a["start"]) / 86400
    def btc_ret(a):
        i0 = reg.index.searchsorted(a["start"]) ; i1 = reg.index.searchsorted(a["end"])
        try: return reg.btc.iloc[min(i1, len(reg) - 1)] / reg.btc.iloc[min(i0, len(reg) - 1)] - 1
        except Exception: return float("nan")
    row = dict(variant=name, attempts=len(done), passes=len(passes), kills=len(kills),
               pass_rate=len(passes) / len(done) if done else float("nan"),
               med_days_pass=float(np.median([days(a) for a in passes])) if passes else float("nan"),
               med_days_kill=float(np.median([days(a) for a in kills])) if kills else float("nan"),
               trades_per_attempt=float(np.mean([a["trades"] for a in done])) if done else float("nan"),
               open_attempt=next((f"{(a['final']/START-1)*100:+.1f}% after {a['trades']} trades" for a in attempts if a["result"] == "OPEN"), "—"))
    lines = []
    for a in attempts:
        s = dt.datetime.fromtimestamp(a["start"], dt.timezone.utc).strftime("%Y-%m-%d"); e = dt.datetime.fromtimestamp(a["end"], dt.timezone.utc).strftime("%Y-%m-%d")
        wr = a["wins"] / a["trades"] * 100 if a["trades"] else 0
        lines.append(f"| {s} → {e} | **{a['result']}** | {days(a):.0f} d | {a['trades']} | {wr:.0f}% | {np.mean(a['breadth']) if a['breadth'] else float('nan'):.1f}/4 | {btc_ret(a)*100:+.0f}% | {np.nanmean(a['vol']) if a['vol'] else float('nan'):.1f}× |")
    return row, lines


def trades_for(params=None):
    out = []
    for p in PAIRS:
        if (DATA / f"{p}_60.csv").exists(): out += S.s4_trend_trail(p, params)
    return out


if __name__ == "__main__":
    reg = regime_table()
    base = trades_for()
    first = dt.datetime.fromtimestamp(min(t["ts_open"] for t in base), dt.timezone.utc).strftime("%Y-%m-%d")
    last = dt.datetime.fromtimestamp(max(t["ts_close"] for t in base), dt.timezone.utc).strftime("%Y-%m-%d")
    flat = lambda r: (lambda eq: r)
    def ratchet(eq):                       # cushion above the fixed kill line buys room: 0.25% until +2%, 0.5% until +6%, then 0.75%
        g = eq / START - 1
        return 0.0025 if g < 0.02 else (0.005 if g < 0.06 else 0.0075)
    variants = [
        ("base 0.25%", base, flat(0.0025), 0),
        ("base 0.50%", base, flat(0.005), 0),
        ("ratchet 0.25→0.5→0.75%", base, ratchet, 0),
        ("breadth ≥2 coins up, 0.25%", base, flat(0.0025), 2),
        ("breadth ≥2, ratchet", base, ratchet, 2),
        ("breadth ≥3, ratchet", base, ratchet, 3),
        ("don=30, 0.25%", trades_for(dict(don=30)), flat(0.0025), 0),
        ("vol≥1.5×, 0.25%", trades_for(dict(vol_mult=1.5)), flat(0.0025), 0),
        ("stop 1.5×ATR, 0.25%", trades_for(dict(atr_mult=1.5)), flat(0.0025), 0),
        ("trail 4×ATR, 0.25%", trades_for(dict(trail=4.0)), flat(0.0025), 0),
        ("don=30 + vol≥1.5 + ratchet, breadth≥2", trades_for(dict(don=30, vol_mult=1.5)), ratchet, 2),
    ]
    L = [f"# attempts_report.md — back-to-back Kraken Funded attempts, {first} → {last}",
         f"Each attempt: start $10,000, PASS at +12%, KILL at −3% (static). Costs 4 bps/side + 2 bps slip + swap 0.0055%/4h. "
         f"Data: {', '.join(PAIRS)} 4H/1D from Binance history (Kraken live).", "",
         "| variant | attempts | PASS | KILL | pass rate | median days to pass | median days to kill | trades/attempt | attempt still open |",
         "|---|---|---|---|---|---|---|---|---|"]
    details = []
    for name, tr, rf, mb in variants:
        atts = replay(tr, rf, reg, min_breadth=mb)
        row, lines = summarize(name, atts, reg)
        L.append(f"| {row['variant']} | {row['attempts']} | {row['passes']} | {row['kills']} | {row['pass_rate']*100:.0f}% | {row['med_days_pass']:.0f} | {row['med_days_kill']:.0f} | {row['trades_per_attempt']:.0f} | {row['open_attempt']} |")
        details.append((name, lines))
        print(L[-1], flush=True)
    L.append("")
    df = pd.DataFrame(base); df["year"] = pd.to_datetime(df.ts_open, unit="s").dt.year
    df["netR"] = df.R - 2 * FEE * df.entry / df.r - FUND * (df.hold_h / 4) * df.entry / df.r
    L += ["## Edge by year (base rules, all signals, after costs)", "| year | trades | win | net R/trade | avg breakout volume |", "|---|---|---|---|---|"]
    for y, g in df.groupby("year"):
        L.append(f"| {y} | {len(g)} | {(g.R > 0).mean()*100:.0f}% | {g.netR.mean():+.2f}R | {g.vol_ratio.mean():.1f}× |")
    L.append("")
    for name, lines in details[:3] + [details[4]]:
        L += [f"## Attempt log — {name}", "| period | result | length | trades | win rate | avg breadth (coins in uptrend) | BTC move over attempt | avg breakout volume |", "|---|---|---|---|---|---|---|---|"] + lines + [""]
    OUT.write_text("\n".join(L), encoding="utf-8")
    print("written", OUT)
