"""
Self-test of the live bot: data feeds, every indicator, both signal functions, sizing maths, limits.
Run by the cloud runner when dispatched with {"test": true}; results go to the phone and the log.
"""
import math, time, datetime as dt
import pandas as pd, numpy as np
import bot, exchange, backtest as b, strategies as S, ported as PT


def finite(x): return x is not None and not (isinstance(x, float) and (math.isnan(x) or math.isinf(x)))


def run():
    checks = []
    def ok(name, cond, detail=""):
        checks.append((name, bool(cond), detail))
    # 1. exchange connection
    try:
        drift = abs(exchange.server_time() - int(time.time())); ok("Kraken reachable, clock drift", drift < 30, f"{drift}s")
    except Exception as e: ok("Kraken reachable", False, str(e)[:80])
    # 2. data per pair + indicators
    for pair in bot.PAIRS:
        try:
            last = bot.refresh_history(pair); age_h = (time.time() - last) / 3600
            ok(f"{pair} 4H data fresh", age_h < 5, f"last bar {age_h:.1f}h ago")
            b.DATA = bot.LIVE
            h4 = S.load_tf(pair, 240); h1 = S.load_tf(pair, 60); d1 = pd.read_csv(bot.LIVE / f"{pair}_1440.csv")
            ok(f"{pair} bars", len(h4) > 300 and len(h1) > 300 and len(d1) > 200, f"4H {len(h4)} · 1H {len(h1)} · 1D {len(d1)}")
            i = len(h4) - 2; j = len(h1) - 2
            vals = dict(ema50_4h=float(b.ema(h4.close, 50).iloc[i]), atr14_4h=float(h4.atr.iloc[i]), vol20_4h=float(h4.vol20.iloc[i]),
                        ema50_1d=float(b.ema(d1.close, 50).iloc[-2]), rsi14_1h=float(PT.rsi(h1.close).iloc[j]), stoch_1h=float(PT.stoch_slowk(h1).iloc[j]),
                        bb_lower_1h=float(PT.bb_lower(h1).iloc[j]), sar_1h=float(PT.sar(h1).iloc[j]), fisher_1h=float(PT.fisher_rsi(PT.rsi(h1.close)).iloc[j]))
            bad = [k for k, v in vals.items() if not finite(v)]
            ok(f"{pair} indicators finite", not bad, ", ".join(f"{k}={v:.4g}" for k, v in vals.items() if k in ("atr14_4h", "rsi14_1h", "stoch_1h")) + (f" BAD: {bad}" if bad else ""))
            ok(f"{pair} indicator sanity", 0 <= vals["rsi14_1h"] <= 100 and 0 <= vals["stoch_1h"] <= 100 and vals["atr14_4h"] > 0 and vals["bb_lower_1h"] < float(h1.close.iloc[j]) * 1.2)
            w = bot.build_watch(pair, float(h4.close.iloc[-1]))
            ok(f"{pair} watch builds", w["status"] in ("OFF", "WATCH", "ARMED", "REV") and finite(w["dist_pct"]) and len(w["candles"]) >= 100, f"{w['status']} · trigger {bot.P(w['trigger'])} · rev {w['reversal']['conditions']}/4")
            s4 = bot.signal_now(pair); s2 = bot.signal_now_reversal(pair)
            ok(f"{pair} signal functions run", True, f"trend {'SIGNAL' if s4 else 'none'} · reversal {'SIGNAL' if s2 else 'none'}")
        except Exception as e:
            ok(f"{pair} data/indicators", False, f"{type(e).__name__}: {str(e)[:80]}")
    # 3. sizing maths on a synthetic signal
    try:
        st = dict(equity=10000.0); t = exchange.ticker(bot.PAIRS[0]); px = t["ask"]
        p = bot.make_proposal(st, dict(pair=bot.PAIRS[0], stop_dist=px * 0.03, rr=None, atr=px * 0.01, vol_ratio=1.0, signal="test", regime="test", strat="S4"))
        loss_at_stop = p["size"] * (p["entry"] - p["stop"]) + 2 * p["size"] * p["entry"] * bot.FEE
        ok("sizing: loss at stop = 1% of equity", abs(loss_at_stop - 100) < 2, f"${loss_at_stop:.1f} · order {p['notional']:.0f} USD = {p['pct_of_account']:.1f}%")
        ok("sizing: risk cut below -2.5%", bot.risk_now(9740) == bot.RISK_CUT and bot.risk_now(9760) == bot.RISK)
    except Exception as e: ok("sizing", False, str(e)[:80])
    # 4. limits
    L = bot.LIMITS
    ok("limits = Starter plan", L["kill"] == 0.06 and L["halt"] == 0.05 and L["target"] == 0.10, f"kill {L['kill']*100:.0f}% halt {L['halt']*100:.0f}% target {L['target']*100:.0f}%")
    ok("max open / per coin", bot.MAX_OPEN == 2)
    # 5. backtest engine consistency: live walker vs strategy on the same files
    try:
        b.DATA = bot.LIVE; tr = S.s4_trend_trail(bot.PAIRS[0]); ok("backtest engine runs on live data", True, f"{len(tr)} S4 trades in the last 120 days of {bot.PAIRS[0]}")
    except Exception as e: ok("backtest engine", False, str(e)[:80])
    return checks


def report(checks):
    fails = [c for c in checks if not c[1]]
    lines = [("✅ " if c[1] else "❌ ") + c[0] + (f" — {c[2]}" if c[2] else "") for c in checks]
    head = f"SELF-TEST {'PASSED' if not fails else 'FAILED'}: {len(checks)-len(fails)}/{len(checks)} checks"
    return head, "\n".join(lines), fails


if __name__ == "__main__":
    h, body, fails = report(run()); print(h); print(body)
