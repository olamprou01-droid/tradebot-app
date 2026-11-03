"""
Backtest for strategy_rules.md v0.1 (Trend + Discount, long only).
Reads data/<PAIR>_{60,240,1440}.csv. Writes backtest_report.md and trades_backtest.csv.

Honesty rules: indicators use closed candles only; entry at next 1H open; stop and target
checked on the bar's high/low; stop wins ties; costs on every fill; nothing is annualised
from fewer than 20 trades without saying so.
"""
import sys, pathlib, math, itertools, datetime as dt
import numpy as np, pandas as pd

HERE = pathlib.Path(__file__).parent
DATA = HERE / "data"
PAIRS = ["XBTUSD", "ETHUSD"]
ROUND = {"XBTUSD": 500.0, "ETHUSD": 50.0}
START_EQ = 10_000.0
FEE, SLIP = 0.0004, 0.0002          # per side
FUNDING_4H = 0.000055                # Kraken Prop swap 0.033%/day, charged per 4h rollover on notional
KILL = 0.03                          # Funded kill line vs start equity
SPLITS = {"train 2022-23": ("2022-01-01", "2023-12-31"),
          "valid 2024": ("2024-01-01", "2024-12-31"),
          "OOS 2025-26": ("2025-01-01", "2026-12-31")}

BASE = dict(rr=3.0, vol_mult=1.2, stop_lb=12, adx_min=20, zone_lb=48, time_stop=96,
            max_r_pct=0.03, zones=("gap", "fib", "round"), whale=False)


# ---------- indicators ----------
def ema(s, n): return s.ewm(span=n, adjust=False).mean()

def atr(df, n=14):
    pc = df.close.shift()
    tr = pd.concat([df.high - df.low, (df.high - pc).abs(), (df.low - pc).abs()], axis=1).max(axis=1)
    return tr.ewm(alpha=1 / n, adjust=False).mean()

def adx(df, n=14):
    up, dn = df.high.diff(), -df.low.diff()
    plus = np.where((up > dn) & (up > 0), up, 0.0); minus = np.where((dn > up) & (dn > 0), dn, 0.0)
    tr = atr(df, n)
    pdi = 100 * pd.Series(plus, index=df.index).ewm(alpha=1 / n, adjust=False).mean() / tr
    mdi = 100 * pd.Series(minus, index=df.index).ewm(alpha=1 / n, adjust=False).mean() / tr
    dx = 100 * (pdi - mdi).abs() / (pdi + mdi).replace(0, np.nan)
    return dx.ewm(alpha=1 / n, adjust=False).mean()


def load(pair):
    d = {}
    for m in (60, 240, 1440):
        df = pd.read_csv(DATA / f"{pair}_{m}.csv")
        df["close_ts"] = df.ts + m * 60           # when the candle is actually known
        d[m] = df
    h1, h4, d1 = d[60], d[240], d[1440]
    d1["ema50"] = ema(d1.close, 50); d1["ema50_lag"] = d1.ema50.shift(10)
    d1["regime_up"] = (d1.close > d1.ema50) & (d1.ema50 > d1.ema50_lag)
    h4["ema50"] = ema(h4.close, 50); h4["adx"] = adx(h4)
    h4["bias_ok_raw"] = h4.close > h4.ema50
    h1["atr"] = atr(h1); h1["vol20"] = h1.volume.rolling(20).mean()
    # align higher timeframes by the time they were known (no look-ahead)
    h1 = pd.merge_asof(h1.sort_values("close_ts"), d1[["close_ts", "regime_up"]].sort_values("close_ts"),
                       on="close_ts", direction="backward")
    h1 = pd.merge_asof(h1, h4[["close_ts", "bias_ok_raw", "adx"]].rename(columns={"adx": "adx4"}).sort_values("close_ts"),
                       on="close_ts", direction="backward")
    return h1.reset_index(drop=True)


# ---------- signals ----------
def signals(h1, pair, p):
    o, h, l, c, v = (h1[x].values for x in ("open", "high", "low", "close", "volume"))
    atrv, vol20 = h1.atr.values, h1.vol20.values
    reg, bias, adx4 = h1.regime_up.values, h1.bias_ok_raw.values, h1.adx4.values
    n, lb = len(h1), p["zone_lb"]
    out = []
    gaps = []                                   # (idx_formed, bottom, top)
    for i in range(max(lb, 60), n - 1):
        if i >= 2 and l[i] > h[i - 2]:
            gaps.append((i, h[i - 2], l[i]))
        gaps = [g for g in gaps if g[0] > i - lb and not (l[g[0] + 1:i].size and l[g[0] + 1:i].min() <= g[1])]
        if not (reg[i] and bias[i] and adx4[i] >= p["adx_min"]):
            continue
        bull = c[i] > o[i] and v[i] >= p["vol_mult"] * vol20[i]
        if not bull:
            continue
        if p["whale"] and (h[i] - l[i]) < 1.5 * atrv[i]:
            continue
        zone = None
        if "gap" in p["zones"]:
            for g in gaps:
                if g[0] < i and l[i] <= g[2] and c[i] > g[2]:
                    zone = "gap"; break
        if zone is None and "fib" in p["zones"]:
            w0 = i - lb; lo_i = w0 + int(np.argmin(l[w0:i]))
            if lo_i < i - 1:
                hi_i = lo_i + int(np.argmax(h[lo_i:i]))
                if hi_i > lo_i:
                    d = h[hi_i] - l[lo_i]; top, bot = h[hi_i] - 0.5 * d, h[hi_i] - 0.618 * d
                    if l[i] <= top and l[i] >= bot - 0.25 * d and c[i] > top:
                        zone = "fib"
        if zone is None and "round" in p["zones"]:
            L = round(l[i] / ROUND[pair]) * ROUND[pair]
            if abs(l[i] - L) / L <= 0.003 and c[i] > L:
                zone = "round"
        if zone is None:
            continue
        stop = l[max(0, i - p["stop_lb"] + 1):i + 1].min() - 0.1 * atrv[i]
        entry = o[i + 1] * (1 + SLIP)
        r = entry - stop
        if r <= 0 or r / entry > p["max_r_pct"]:
            continue
        target = entry + p["rr"] * r
        # walk forward to the exit
        exit_px, exit_i, reason = None, None, None
        for j in range(i + 1, min(n, i + 1 + p["time_stop"])):
            if j > i + 1 and o[j] <= stop:          # gap through the stop
                exit_px, exit_i, reason = o[j] * (1 - SLIP), j, "stop_gap"; break
            if l[j] <= stop:
                exit_px, exit_i, reason = stop * (1 - SLIP), j, "stop"; break
            if h[j] >= target:
                exit_px, exit_i, reason = target * (1 - SLIP), j, "target"; break
        if exit_px is None:
            j = min(n - 1, i + p["time_stop"]); exit_px, exit_i, reason = c[j] * (1 - SLIP), j, "time"
        out.append(dict(pair=pair, sig_i=i, ts_open=int(h1.ts[i + 1]), ts_close=int(h1.ts[exit_i]),
                        zone=zone, entry=entry, stop=stop, target=target, exit=exit_px, r=r,
                        R=(exit_px - entry) / r, reason=reason, hold_h=exit_i - (i + 1)))
    return out


# ---------- portfolio simulation ----------
def simulate(trades, risk):
    trades = sorted(trades, key=lambda t: t["ts_open"])
    eq, peak, open_pos, rows = START_EQ, START_EQ, {}, []
    day_loss, day = 0.0, None
    killed, max_dd = False, 0.0
    daily_eq = {}
    for t in trades:
        # close anything that has finished before this entry
        for k in [k for k, v in open_pos.items() if v["ts_close"] <= t["ts_open"]]:
            v = open_pos.pop(k); eq += v["net"]; peak = max(peak, eq); max_dd = max(max_dd, 1 - eq / peak)
            d2 = dt.datetime.fromtimestamp(v["ts_close"], dt.timezone.utc).date()
            if d2 == day: day_loss += min(0, v["net"])
            daily_eq[d2] = eq
            if eq <= START_EQ * (1 - KILL): killed = True
        d = dt.datetime.fromtimestamp(t["ts_open"], dt.timezone.utc).date()
        if d != day: day, day_loss = d, 0.0
        if killed or t["pair"] in open_pos or len(open_pos) >= 2 or -day_loss >= eq * 0.01:
            continue
        size = eq * risk / t["r"]
        gross = size * (t["exit"] - t["entry"])
        fees = size * (t["entry"] + t["exit"]) * FEE + size * t["entry"] * FUNDING_4H * (t["hold_h"] / 4)   # commission + Kraken Prop swap per 4h
        rec = dict(t, size=size, risk_usd=size * t["r"], fees=fees, gross=gross, net=gross - fees, eq_before=eq)
        open_pos[t["pair"]] = rec; rows.append(rec)
    for v in open_pos.values():
        eq += v["net"]; peak = max(peak, eq); max_dd = max(max_dd, 1 - eq / peak)
        daily_eq[dt.datetime.fromtimestamp(v["ts_close"], dt.timezone.utc).date()] = eq
        if eq <= START_EQ * (1 - KILL): killed = True
    return rows, eq, max_dd, killed, daily_eq


def metrics(rows, eq_end, max_dd, killed, daily_eq, span_days):
    if not rows:
        return dict(trades=0)
    df = pd.DataFrame(rows)
    wins, losses = df[df.net > 0], df[df.net <= 0]
    ret = eq_end / START_EQ - 1
    yrs = max(span_days / 365.25, 1e-9)
    ann = (1 + ret) ** (1 / yrs) - 1 if ret > -1 else -1
    s = pd.Series(daily_eq).sort_index()
    dr = s.pct_change().dropna()
    sharpe = dr.mean() / dr.std() * math.sqrt(365) if len(dr) > 5 and dr.std() > 0 else float("nan")
    down = dr[dr < 0]
    sortino = dr.mean() / down.std() * math.sqrt(365) if len(down) > 2 and down.std() > 0 else float("nan")
    pf = wins.net.sum() / -losses.net.sum() if len(losses) and losses.net.sum() < 0 else float("inf")
    return dict(trades=len(df), net_ret=ret, ann_ret=ann, max_dd=max_dd, sharpe=sharpe, sortino=sortino,
                calmar=ann / max_dd if max_dd > 0 else float("nan"), pf=pf, win=len(wins) / len(df),
                avg_win=wins.net.mean() if len(wins) else 0, avg_loss=losses.net.mean() if len(losses) else 0,
                exp_R=df.R.mean(), exp_usd=df.net.mean(), hold_h=df.hold_h.mean(), fees=df.fees.sum(),
                exposure=df.hold_h.sum() / (span_days * 24) if span_days else float("nan"), killed=killed)


def bench(h1s, a, b):
    """buy-and-hold and daily EMA50/200 long-only cross, per pair, over [a,b]."""
    out = {}
    for pair, h1 in h1s.items():
        d = pd.read_csv(DATA / f"{pair}_1440.csv")
        d["dt"] = pd.to_datetime(d.ts, unit="s"); d = d[(d.dt >= a) & (d.dt <= b)]
        if len(d) < 2: continue
        out[f"B&H {pair}"] = d.close.iloc[-1] / d.close.iloc[0] - 1
        full = pd.read_csv(DATA / f"{pair}_1440.csv"); full["dt"] = pd.to_datetime(full.ts, unit="s")
        full["e50"], full["e200"] = ema(full.close, 50), ema(full.close, 200)
        full["pos"] = (full.e50 > full.e200).astype(int).shift(1).fillna(0)
        full["ret"] = full.close.pct_change().fillna(0) * full.pos
        f = full[(full.dt >= a) & (full.dt <= b)]
        out[f"EMA50/200 {pair}"] = (1 + f.ret).prod() - 1
    return out


def fmt(m):
    if m.get("trades", 0) == 0: return "no trades"
    return (f"trades {m['trades']}, net {m['net_ret']*100:+.1f}%, ann {m['ann_ret']*100:+.1f}%, maxDD {m['max_dd']*100:.1f}%, "
            f"Sharpe {m['sharpe']:.2f}, Sortino {m['sortino']:.2f}, Calmar {m['calmar']:.2f}, PF {m['pf']:.2f}, win {m['win']*100:.0f}%, "
            f"avg win ${m['avg_win']:.0f} / avg loss ${m['avg_loss']:.0f}, expectancy {m['exp_R']:+.2f}R (${m['exp_usd']:+.0f}), "
            f"hold {m['hold_h']:.0f}h, exposure {m['exposure']*100:.0f}%, fees ${m['fees']:.0f}, "
            f"{'KILLED (-3%)' if m['killed'] else 'survived'}")


def run(p, h1s, a, b, risk):
    trades = [t for pair, h1 in h1s.items() for t in signals(h1, pair, p)]
    ta, tb = pd.Timestamp(a).timestamp(), pd.Timestamp(b).timestamp() + 86400
    trades = [t for t in trades if ta <= t["ts_open"] < tb]
    rows, eq, dd, killed, deq = simulate(trades, risk)
    return rows, metrics(rows, eq, dd, killed, deq, (tb - ta) / 86400)


if __name__ == "__main__":
    h1s = {pair: load(pair) for pair in PAIRS}
    first = max(dt.datetime.fromtimestamp(h.ts.iloc[0], dt.timezone.utc) for h in h1s.values()).strftime("%Y-%m-%d")
    last = min(dt.datetime.fromtimestamp(h.ts.iloc[-1], dt.timezone.utc) for h in h1s.values()).strftime("%Y-%m-%d")
    L = [f"# backtest_report.md — strategy v0.1 (Trend + Discount, long only)",
         f"Data: {', '.join(PAIRS)} 1H/4H/1D, {first} → {last}. Costs: {FEE*1e4:.0f} bps fee + {SLIP*1e4:.0f} bps slippage per side. "
         f"Start equity ${START_EQ:,.0f}, kill line −{KILL*100:.0f}%.", ""]

    L.append("## 1. Base case by period (risk 0.5% per trade)")
    for name, (a, b) in SPLITS.items():
        rows, m = run(BASE, h1s, a, b, 0.005)
        L.append(f"- **{name}**: {fmt(m)}")
        for k, v in bench(h1s, a, b).items():
            L.append(f"  - benchmark {k}: {v*100:+.1f}%")
    L.append("")

    L.append("## 2. Owner's 2% risk vs 0.5% (full period)")
    for risk in (0.005, 0.01, 0.02):
        rows, m = run(BASE, h1s, first, last, risk)
        L.append(f"- risk {risk*100:.1f}%: {fmt(m)}")
    L.append("")

    L.append("## 3. Which entry zone carries the edge (full period, 0.5%)")
    for z in ("gap", "fib", "round"):
        rows, m = run(dict(BASE, zones=(z,)), h1s, first, last, 0.005)
        L.append(f"- {z} only: {fmt(m)}")
    rows, m = run(dict(BASE, whale=True), h1s, first, last, 0.005)
    L.append(f"- + whale-spike filter: {fmt(m)}")
    L.append("")

    L.append("## 4. Sensitivity (full period, 0.5%) — a real edge should not vanish on small changes")
    for k, vals in dict(rr=(2.0, 3.0, 4.0), vol_mult=(1.0, 1.2, 1.5), stop_lb=(8, 12, 20), adx_min=(15, 20, 25), time_stop=(48, 96, 168)).items():
        for v in vals:
            rows, m = run(dict(BASE, **{k: v}), h1s, first, last, 0.005)
            L.append(f"- {k}={v}: {fmt(m)}")
    L.append("")

    L.append("## 5. By year (0.5%)")
    years = sorted({dt.datetime.fromtimestamp(h.ts.iloc[0], dt.timezone.utc).year for h in h1s.values()} | {int(last[:4])})
    for y in range(min(years), int(last[:4]) + 1):
        rows, m = run(BASE, h1s, f"{y}-01-01", f"{y}-12-31", 0.005)
        L.append(f"- {y}: {fmt(m)}")

    rows, m = run(BASE, h1s, first, last, 0.005)
    pd.DataFrame(rows).to_csv(HERE / "trades_backtest.csv", index=False)
    (HERE / "backtest_report.md").write_text("\n".join(L), encoding="utf-8")
    print("\n".join(L))
