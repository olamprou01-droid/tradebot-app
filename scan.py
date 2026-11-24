"""
Look back over live Kraken history and list every entry signal the two engines would have given since a
given time — used to check what was missed while the bot was not running. Read-only: opens nothing.

    python scan.py <out_dir> [since_unix_ts]
"""
import os, sys, json, time, pathlib, datetime as dt
import pandas as pd
import bot, backtest as b, strategies as S, ported as PT

out_dir = pathlib.Path(sys.argv[1]).resolve(); out_dir.mkdir(exist_ok=True)
since = int(sys.argv[2]) if len(sys.argv) > 2 and sys.argv[2].strip() else int(time.time()) - 14 * 86400
bot.LIVE = out_dir / "live"; bot.LIVE.mkdir(exist_ok=True); b.DATA = bot.LIVE
iso = lambda t: dt.datetime.fromtimestamp(int(t), dt.timezone.utc).strftime("%Y-%m-%d %H:%M")
res = dict(ts=time.time(), since=since, since_utc=iso(since), pairs={})

for pair in bot.PAIRS:
    if not os.getenv("SCAN_OFFLINE"): bot.refresh_history(pair)
    h4 = S.load_tf(pair, 240); h1 = S.load_tf(pair, 60)
    n4, n1 = len(h4), len(h1)
    # ---- trend engine: same conditions as bot.signal_now, on every closed 4H bar
    hh = h4.high.rolling(bot.DON).max().shift(1)
    sig = h4.regime_up.astype(bool) & (h4.close > hh) & (h4.volume >= h4.vol20) & (bot.ATR_MULT * h4.atr / h4.close <= 0.08)
    trend = []
    for i in [i for i in range(60, n4 - 1) if sig.iloc[i] and h4.close_ts.iloc[i] >= since]:
        stop0 = float(h4.close.iloc[i] - bot.ATR_MULT * h4.atr.iloc[i]); rec = dict(signal_at=iso(h4.close_ts.iloc[i]), price=float(h4.close.iloc[i]), stop=stop0)
        if i + 1 < n4:
            entry, ex, ei, why = S.walk_trail(h4, i, float(h4.open.iloc[i + 1]) * (1 + b.SLIP) - bot.ATR_MULT * float(h4.atr.iloc[i]), bot.TRAIL_ATR, bot.TIME_STOP_BARS)
            still = ei >= n4 - 1 and why == "time"
            rec.update(entry=float(entry), now_or_exit=float(ex), status="still open" if still else f"closed ({why}) {iso(h4.ts.iloc[ei])}",
                       result_R=float(round((ex - entry) / (entry - stop0), 2)))
        trend.append(rec)
    # ---- reversal engine: S002 buy on every closed 1H bar; exit +5% close, sell signal, or 3% stop
    buy, sell = PT.s002(h1); buy = buy.fillna(False).values; sell = sell.fillna(False).values
    revs = []
    for i in [i for i in range(60, n1 - 1) if buy[i] and h1.close_ts.iloc[i] >= since]:
        entry = float(h1.open.iloc[i + 1]) * (1 + b.SLIP); stop = entry * (1 - bot.REV_STOP); status, ex = "still open", float(h1.close.iloc[-1])
        for j in range(i + 1, n1 - 1):
            if h1.low.iloc[j] <= stop: status, ex = f"closed (stop) {iso(h1.close_ts.iloc[j])}", stop; break
            if h1.close.iloc[j] / entry - 1 >= bot.REV_ROI_ANY: status, ex = f"closed (+5%) {iso(h1.close_ts.iloc[j])}", float(h1.close.iloc[j]); break
            if sell[j]: status, ex = f"closed (sell signal) {iso(h1.close_ts.iloc[j])}", float(h1.close.iloc[j]); break
        revs.append(dict(signal_at=iso(h1.close_ts.iloc[i]), price=float(h1.close.iloc[i]), entry=entry, stop=stop, now_or_exit=ex, status=status, result_R=float(round((ex - entry) / (entry - stop), 2))))
    # ---- context: how close things came
    r = PT.rsi(h1.close); sk = PT.stoch_slowk(h1); bbl = PT.bb_lower(h1); hm = PT.hammer(h1)
    cond = (r < 30).astype(int) + (sk < 20).astype(int) + (h1.close < bbl).astype(int) + (hm == 100).astype(int)
    m4 = h4.close_ts >= since; m1 = h1.close_ts >= since
    i4 = n4 - 2
    res["pairs"][pair] = dict(
        trend_signals=trend, reversal_signals=revs, price=float(h4.close.iloc[-1]),
        daily_trend_up_now=bool(h4.regime_up.iloc[i4]), bars_4h_with_trend_up=int(h4.regime_up[m4].astype(bool).sum()), bars_4h_total=int(m4.sum()),
        trigger_now=float(h4.high.iloc[i4 - bot.DON + 1:i4 + 1].max()), max_reversal_conditions=int(cond[m1].max()) if m1.any() else 0,
        hours_with_3_of_4=int((cond[m1] >= 3).sum()), data_until=iso(h1.close_ts.iloc[-2]))

(out_dir / "scan_result.json").write_text(json.dumps(res, indent=1))
import shutil; shutil.rmtree(bot.LIVE, ignore_errors=True)
for p, v in res["pairs"].items():
    print(f"{p}: trend {len(v['trend_signals'])} · reversal {len(v['reversal_signals'])} · trend-up bars {v['bars_4h_with_trend_up']}/{v['bars_4h_total']} · max rev {v['max_reversal_conditions']}/4 · now {v['price']:.4g} trigger {v['trigger_now']:.4g}")
    for t in v["trend_signals"]: print("   TREND", t)
    for t in v["reversal_signals"]: print("   REVERSAL", t)
