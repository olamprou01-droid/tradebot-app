"""
Ports of community freqtrade strategies (glonlas Strategy001-004) into our engine, so they face the
same test as ours: real history, 4 bps/side + slip + swap, Kraken challenge replay.

freqtrade semantics kept: buy signal -> enter next bar open; exit on sell signal, on the minimal_roi
table (profit >= x after n minutes), or on the 30% stoploss. Their 5-minute timeframe is run on 1H and
4H bars here (we hold no 5m history; the ROI table is converted from minutes to bars).
"""
import numpy as np, pandas as pd
import backtest as b, strategies as S

SLIP, FEE = b.SLIP, b.FEE
ROI = [(60, 0.01), (30, 0.03), (20, 0.04), (0, 0.05)]       # minutes -> min profit, as in the originals
STOPLOSS = -0.30


# ---------- indicators (pandas versions of the TA-Lib calls the strategies use) ----------
def ema(s, n): return s.ewm(span=n, adjust=False).mean()
def sma(s, n): return s.rolling(n).mean()
def rsi(s, n=14):
    d = s.diff(); up = d.clip(lower=0); dn = -d.clip(upper=0)
    rs = up.ewm(alpha=1 / n, adjust=False).mean() / dn.ewm(alpha=1 / n, adjust=False).mean().replace(0, np.nan)
    return 100 - 100 / (1 + rs)
def fisher_rsi(r): x = 0.1 * (r - 50); return (np.exp(2 * x) - 1) / (np.exp(2 * x) + 1)
def stochf(df, k=5, d=3):
    lo, hi = df.low.rolling(k).min(), df.high.rolling(k).max()
    fk = 100 * (df.close - lo) / (hi - lo).replace(0, np.nan); return fk, fk.rolling(d).mean()
def stoch_slowk(df): fk, fd = stochf(df, 5, 3); return fd          # TA-Lib STOCH slowk = SMA3 of fastk
def bb_lower(df, n=20, k=2):
    tp = (df.high + df.low + df.close) / 3; m = tp.rolling(n).mean(); s = tp.rolling(n).std(ddof=0); return m - k * s
def mfi(df, n=14):
    tp = (df.high + df.low + df.close) / 3; mf = tp * df.volume; up = mf.where(tp > tp.shift(), 0.0); dn = mf.where(tp < tp.shift(), 0.0)
    r = up.rolling(n).sum() / dn.rolling(n).sum().replace(0, np.nan); return 100 - 100 / (1 + r)
def cci(df, n=14):
    tp = (df.high + df.low + df.close) / 3; m = tp.rolling(n).mean(); md = (tp - m).abs().rolling(n).mean(); return (tp - m) / (0.015 * md)
def adx(df, n=14): return b.adx(df, n)
def heikin(df):
    hc = (df.open + df.high + df.low + df.close) / 4; ho = hc.copy()
    o = df.open.values; c = hc.values; out = np.empty(len(df)); out[0] = o[0]
    for i in range(1, len(df)): out[i] = (out[i - 1] + c[i - 1]) / 2
    return pd.Series(out, index=df.index), hc
def sar(df, af0=0.02, step=0.02, mx=0.2):
    h, l = df.high.values, df.low.values; n = len(df); out = np.full(n, np.nan)
    if n < 2: return pd.Series(out, index=df.index)
    up = True; af = af0; ep = h[0]; s = l[0]
    for i in range(1, n):
        s = s + af * (ep - s)
        if up:
            s = min(s, l[i - 1], l[i - 2] if i > 1 else l[i - 1])
            if l[i] < s: up, s, ep, af = False, ep, l[i], af0
            elif h[i] > ep: ep, af = h[i], min(af + step, mx)
        else:
            s = max(s, h[i - 1], h[i - 2] if i > 1 else h[i - 1])
            if h[i] > s: up, s, ep, af = True, ep, h[i], af0
            elif l[i] < ep: ep, af = l[i], min(af + step, mx)
        out[i] = s
    return pd.Series(out, index=df.index)
def hammer(df):
    body = (df.close - df.open).abs(); rng = (df.high - df.low).replace(0, np.nan)
    lower = df[["open", "close"]].min(axis=1) - df.low; upper = df.high - df[["open", "close"]].max(axis=1)
    return ((lower >= 2 * body) & (upper <= 0.1 * rng) & (body > 0) & (body <= 0.3 * rng)).astype(int) * 100
def crossed_above(a, b_): return (a > b_) & (a.shift() <= b_.shift())


# ---------- the four strategies: return (buy, sell) boolean series ----------
def s001(df):
    e20, e50, e100 = ema(df.close, 20), ema(df.close, 50), ema(df.close, 100); ho, hc = heikin(df)
    buy = crossed_above(e20, e50) & (hc > e20) & (ho < hc)
    sell = crossed_above(e50, e100) & (hc < e20) & (ho > hc)
    return buy, sell
def s002(df):
    r = rsi(df.close); fr = fisher_rsi(r); sk = stoch_slowk(df); bbl = bb_lower(df); ps = sar(df); hm = hammer(df)
    buy = (r < 30) & (sk < 20) & (bbl > df.close) & (hm == 100)
    sell = (ps > df.close) & (fr > 0.3)
    return buy, sell
def s003(df):
    r = rsi(df.close); fr = fisher_rsi(r); m = mfi(df); fk, fd = stochf(df); ps = sar(df)
    e5, e10, e50, e100, sm = ema(df.close, 5), ema(df.close, 10), ema(df.close, 50), ema(df.close, 100), sma(df.close, 40)
    buy = (r < 28) & (r > 0) & (df.close < sm) & (fr < -0.94) & (m < 16) & ((e50 > e100) | crossed_above(e5, e10)) & (fd > fk) & (fd > 0)
    sell = (ps > df.close) & (fr > 0.3)
    return buy, sell
def s004(df):
    a, sa = adx(df), adx(df, 35); c = cci(df); fk, fd = stochf(df, 5); sfk, sfd = stochf(df, 50); e5 = ema(df.close, 5)
    buy = ((a > 50) | (sa > 26)) & (c < -100) & (fk.shift() < 20) & (fd.shift() < 20) & (sfk.shift() < 30) & (sfd.shift() < 30) & (fk.shift() < fd.shift()) & (fk > fd)
    sell = (sa < 25) & ((fk > 70) | (fd > 70)) & (fk.shift() < fd.shift()) & (df.close > e5)
    return buy, sell

PORTED = {"S001 ema-cross/HA": s001, "S002 rsi+stoch+hammer": s002, "S003 oversold combo": s003, "S004 adx+cci+stoch": s004}


def run_ported(pair, fn, tf_min=60, roi_scale=None, stoploss=STOPLOSS, max_hold_bars=None):
    """freqtrade-style trade list in our record format. ROI minutes are converted to bars: minutes / tf_min
    (so on 1H the '60 min -> 1%' rule fires after 1 bar), unless roi_scale overrides the divisor."""
    df = S.load_tf(pair, tf_min); buy, sell = fn(df)
    buy, sell = buy.fillna(False).values, sell.fillna(False).values
    o, h, l, c = df.open.values, df.high.values, df.low.values, df.close.values
    div = roi_scale or tf_min; roi = sorted([(max(1, int(round(m / div))) if m else 0, p) for m, p in ROI])   # bars, profit
    out, n, i = [], len(df), 60
    while i < n - 1:
        if not buy[i]: i += 1; continue
        entry = o[i + 1] * (1 + SLIP); stop = entry * (1 + stoploss); ex = ei = why = None
        for j in range(i + 1, n):
            held = j - (i + 1)
            if l[j] <= stop: ex, ei, why = stop * (1 - SLIP), j, "stop"; break
            gain = c[j] / entry - 1
            tgt = max([p for bars, p in roi if held >= bars], default=None)
            if tgt is not None and gain >= tgt: ex, ei, why = c[j] * (1 - SLIP), j, "roi"; break
            if sell[j]: ex, ei, why = c[j] * (1 - SLIP), j, "sell_signal"; break
            if max_hold_bars and held >= max_hold_bars: ex, ei, why = c[j] * (1 - SLIP), j, "time"; break
        if ex is None: j = n - 1; ex, ei, why = c[j] * (1 - SLIP), j, "end"
        r = entry - stop
        out.append(dict(pair=pair, sig_i=i, ts_open=int(df.ts[i + 1]), ts_close=int(df.ts[ei]), zone=fn.__name__, entry=entry, stop=stop,
                        target=float("nan"), exit=ex, r=r, R=(ex - entry) / r, reason=why, hold_h=(ei - (i + 1)) * tf_min / 60, vol_ratio=float("nan")))
        i = ei + 1
    return out
