"""
Candidate systematic strategies, all long only, all using the same exit walker and cost model
as backtest.py so they are comparable. Each returns a list of trade dicts.

S0 discount   — strategy_rules.md v0.1 (owner's approach, 1H)
S1 breakout   — 4H Donchian-20 breakout in a daily uptrend, 2×ATR stop, 3R target
S2 pullback   — 4H pullback to EMA20 in a daily uptrend, stop under the pullback low, 3R target
S3 meanrev    — 1H RSI-14 oversold inside a daily uptrend, 2×ATR stop, 1.5R target, 24h time stop
"""
import numpy as np, pandas as pd
import backtest as b

SLIP = b.SLIP


def rsi(s, n=14):
    d = s.diff(); up = d.clip(lower=0); dn = -d.clip(upper=0)
    rs = up.ewm(alpha=1 / n, adjust=False).mean() / dn.ewm(alpha=1 / n, adjust=False).mean().replace(0, np.nan)
    return 100 - 100 / (1 + rs)


def load_tf(pair, minutes):
    """bars of one timeframe with the daily regime flag merged in without look-ahead."""
    df = pd.read_csv(b.DATA / f"{pair}_{minutes}.csv"); df["close_ts"] = df.ts + minutes * 60
    d1 = pd.read_csv(b.DATA / f"{pair}_1440.csv"); d1["close_ts"] = d1.ts + 86400
    d1["ema50"] = b.ema(d1.close, 50); d1["regime_up"] = (d1.close > d1.ema50) & (d1.ema50 > d1.ema50.shift(10))
    df = pd.merge_asof(df.sort_values("close_ts"), d1[["close_ts", "regime_up"]].sort_values("close_ts"),
                       on="close_ts", direction="backward")
    df["atr"] = b.atr(df); df["vol20"] = df.volume.rolling(20).mean()
    return df.reset_index(drop=True)


def walk(df, i, stop, target, time_stop):
    """entry at bar i+1 open; returns (entry, exit, exit_i, reason). stop wins ties."""
    o, h, l, c = df.open.values, df.high.values, df.low.values, df.close.values
    n = len(df); entry = o[i + 1] * (1 + SLIP)
    for j in range(i + 1, min(n, i + 1 + time_stop)):
        if j > i + 1 and o[j] <= stop: return entry, o[j] * (1 - SLIP), j, "stop_gap"
        if l[j] <= stop: return entry, stop * (1 - SLIP), j, "stop"
        if h[j] >= target: return entry, target * (1 - SLIP), j, "target"
    j = min(n - 1, i + time_stop); return entry, c[j] * (1 - SLIP), j, "time"


def _rec(pair, df, i, entry, stop, target, exit_px, exit_i, reason, tf_min, tag):
    r = entry - stop
    return dict(pair=pair, sig_i=i, ts_open=int(df.ts[i + 1]), ts_close=int(df.ts[exit_i]), zone=tag,
                entry=entry, stop=stop, target=target, exit=exit_px, r=r, R=(exit_px - entry) / r,
                reason=reason, hold_h=(exit_i - (i + 1)) * tf_min / 60)


def s0_discount(pair, p=None):
    return b.signals(b.load(pair), pair, p or b.BASE)


def s1_breakout(pair, p=None):
    p = dict(dict(don=20, atr_mult=2.0, rr=3.0, vol_mult=1.2, time_stop=60, max_r_pct=0.08), **(p or {}))
    df = load_tf(pair, 240); hh = df.high.rolling(p["don"]).max().shift(1)
    out = []
    for i in range(60, len(df) - 1):
        if not df.regime_up[i] or not (df.close[i] > hh[i]) or df.volume[i] < p["vol_mult"] * df.vol20[i]: continue
        stop_dist = p["atr_mult"] * df.atr[i]
        entry_est = df.open[i + 1] * (1 + SLIP); stop = entry_est - stop_dist
        if stop_dist / entry_est > p["max_r_pct"]: continue
        entry, ex, ei, why = walk(df, i, stop, entry_est + p["rr"] * stop_dist, p["time_stop"])
        out.append(_rec(pair, df, i, entry, stop, entry_est + p["rr"] * stop_dist, ex, ei, why, 240, "breakout"))
    return _dedupe(out)


def s2_pullback(pair, p=None):
    p = dict(dict(rr=3.0, lb=6, time_stop=60, max_r_pct=0.06), **(p or {}))
    df = load_tf(pair, 240); df["e20"], df["e50"] = b.ema(df.close, 20), b.ema(df.close, 50)
    out = []
    for i in range(60, len(df) - 1):
        if not (df.regime_up[i] and df.e20[i] > df.e50[i] and df.low[i] <= df.e20[i] < df.close[i] and df.close[i] > df.open[i]): continue
        stop = df.low[max(0, i - p["lb"] + 1):i + 1].min() - 0.5 * df.atr[i]
        entry_est = df.open[i + 1] * (1 + SLIP); r = entry_est - stop
        if r <= 0 or r / entry_est > p["max_r_pct"]: continue
        entry, ex, ei, why = walk(df, i, stop, entry_est + p["rr"] * r, p["time_stop"])
        out.append(_rec(pair, df, i, entry, stop, entry_est + p["rr"] * r, ex, ei, why, 240, "pullback"))
    return _dedupe(out)


def s3_meanrev(pair, p=None):
    p = dict(dict(rsi_max=30, atr_mult=2.0, rr=1.5, time_stop=24, max_r_pct=0.05), **(p or {}))
    df = load_tf(pair, 60); df["rsi"] = rsi(df.close)
    out = []
    for i in range(60, len(df) - 1):
        if not (df.regime_up[i] and df.rsi[i] < p["rsi_max"] and df.close[i] > df.open[i]): continue
        stop_dist = p["atr_mult"] * df.atr[i]; entry_est = df.open[i + 1] * (1 + SLIP)
        if stop_dist / entry_est > p["max_r_pct"]: continue
        entry, ex, ei, why = walk(df, i, entry_est - stop_dist, entry_est + p["rr"] * stop_dist, p["time_stop"])
        out.append(_rec(pair, df, i, entry, entry_est - stop_dist, entry_est + p["rr"] * stop_dist, ex, ei, why, 60, "meanrev"))
    return _dedupe(out)


def _dedupe(trades):
    """one position per pair: drop signals that fire while a trade is open."""
    out, busy_until = [], -1
    for t in sorted(trades, key=lambda t: t["ts_open"]):
        if t["ts_open"] < busy_until: continue
        out.append(t); busy_until = t["ts_close"]
    return out


def walk_trail(df, i, stop, atr_mult, time_stop):
    """chandelier exit: stop trails highest close − atr_mult×ATR; never moves down."""
    o, h, l, c, a = df.open.values, df.high.values, df.low.values, df.close.values, df.atr.values
    n = len(df); entry = o[i + 1] * (1 + SLIP); hi = c[i]
    for j in range(i + 1, min(n, i + 1 + time_stop)):
        if j > i + 1 and o[j] <= stop: return entry, o[j] * (1 - SLIP), j, "stop_gap"
        if l[j] <= stop: return entry, stop * (1 - SLIP), j, "stop"
        hi = max(hi, c[j]); stop = max(stop, hi - atr_mult * a[j])
    j = min(n - 1, i + time_stop); return entry, c[j] * (1 - SLIP), j, "time"


def s4_trend_trail(pair, p=None):
    """4H Donchian-20 breakout in daily uptrend, 2×ATR initial stop, 3×ATR chandelier trail, no fixed target."""
    p = dict(dict(don=20, atr_mult=2.0, trail=3.0, vol_mult=1.0, time_stop=180, max_r_pct=0.08), **(p or {}))
    df = load_tf(pair, 240); hh = df.high.rolling(p["don"]).max().shift(1)
    out = []
    for i in range(60, len(df) - 1):
        if not df.regime_up[i] or not (df.close[i] > hh[i]) or df.volume[i] < p["vol_mult"] * df.vol20[i]: continue
        stop_dist = p["atr_mult"] * df.atr[i]; entry_est = df.open[i + 1] * (1 + SLIP)
        if stop_dist / entry_est > p["max_r_pct"]: continue
        entry, ex, ei, why = walk_trail(df, i, entry_est - stop_dist, p["trail"], p["time_stop"])
        out.append(dict(_rec(pair, df, i, entry, entry_est - stop_dist, float("nan"), ex, ei, why, 240, "trend_trail"),
                        vol_ratio=float(df.volume[i] / df.vol20[i]) if df.vol20[i] else float("nan"), atr_pct=float(df.atr[i] / df.close[i] * 100)))
    return _dedupe(out)


def s5_daily_trend(pair, p=None):
    """Classic daily Donchian-20 entry, exit on 10-day low or 2×ATR stop. Slow, few trades, the benchmark trend system."""
    p = dict(dict(don=20, exit_don=10, atr_mult=2.0, time_stop=120, max_r_pct=0.12), **(p or {}))
    df = load_tf(pair, 1440); hh = df.high.rolling(p["don"]).max().shift(1); ll = df.low.rolling(p["exit_don"]).min().shift(1)
    o, h, l, c = df.open.values, df.high.values, df.low.values, df.close.values
    out, n = [], len(df)
    for i in range(60, n - 1):
        if not df.regime_up[i] or not (c[i] > hh[i]): continue
        stop = o[i + 1] * (1 + SLIP) - p["atr_mult"] * df.atr[i]; entry = o[i + 1] * (1 + SLIP)
        if (entry - stop) / entry > p["max_r_pct"]: continue
        ex = ei = why = None
        for j in range(i + 1, min(n, i + 1 + p["time_stop"])):
            s = max(stop, ll[j] if ll[j] == ll[j] else stop)
            if j > i + 1 and o[j] <= s: ex, ei, why = o[j] * (1 - SLIP), j, "stop_gap"; break
            if l[j] <= s: ex, ei, why = s * (1 - SLIP), j, "stop"; break
        if ex is None: j = min(n - 1, i + p["time_stop"]); ex, ei, why = c[j] * (1 - SLIP), j, "time"
        out.append(_rec(pair, df, i, entry, stop, float("nan"), ex, ei, why, 1440, "daily_trend"))
    return _dedupe(out)


STRATEGIES = {"S4 trend_trail": s4_trend_trail, "S5 daily_trend": s5_daily_trend, "S0 discount": s0_discount, "S1 breakout": s1_breakout, "S2 pullback": s2_pullback, "S3 meanrev": s3_meanrev}
