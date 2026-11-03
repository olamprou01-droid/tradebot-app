"""
Ledger review -> strategy_performance.md + review.json (for the app). Proposes; never applies.
Learning method: every closed trade is bucketed (coin, volume on the breakout bar, volatility,
hour of entry, exit type); a bucket that is clearly worse than the rest with enough trades becomes a
proposed filter, to be confirmed on the backtest before anything changes. Vetoed proposals are tracked
as shadow trades so the owner's news/macro veto gets a score of its own.
"""
import json, pathlib, datetime as dt
import pandas as pd

HERE = pathlib.Path(__file__).parent
LEDGER, OUT_MD, OUT_JSON = HERE / "trades_ledger.csv", HERE / "strategy_performance.md", HERE / "review.json"
START_EQ = 10_000.0
MIN_BUCKET = 12


def bucket_table(df, col, label):
    g = df.groupby(col).agg(n=("R", "size"), expR=("R", "mean"), net=("net_pnl", "sum"), win=("win", "mean")).reset_index()
    return [dict(bucket=f"{label}: {k}", n=int(r.n), expR=round(r.expR, 2), net=round(r.net, 0), win=round(r.win * 100)) for k, r in zip(g[col], g.itertuples())]


def main():
    ts = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    res = dict(ts=ts, trades=0, lessons=[], suggestions=[], buckets=[], veto={})
    if not LEDGER.exists():
        OUT_MD.write_text(f"# strategy_performance.md\n_{ts}_ — no trades yet.\n"); OUT_JSON.write_text(json.dumps(res)); return
    df = pd.read_csv(LEDGER)
    df["R"] = pd.to_numeric(df.R, errors="coerce").fillna(df.net_pnl / df.capital_at_risk)
    df["win"] = df.net_pnl > 0
    taken = df[~df.exit_reason.astype(str).str.startswith("shadow")].copy()
    shadow = df[df.exit_reason.astype(str).str.startswith("shadow")].copy()
    L = ["# strategy_performance.md", f"_{ts}_ · {len(taken)} closed trades · {len(shadow)} vetoed (shadow-tracked)", ""]
    if len(taken):
        eq = START_EQ + taken.net_pnl.cumsum(); dd = float((1 - eq / eq.cummax()).max())
        losses = taken.net_pnl[taken.net_pnl < 0].sum()
        pf = float(taken.net_pnl[taken.net_pnl > 0].sum() / -losses) if losses < 0 else float("inf")
        res.update(trades=int(len(taken)), net=round(float(taken.net_pnl.sum()), 2), equity=round(float(eq.iloc[-1]), 2), max_dd=round(dd * 100, 2),
                   win=round(float(taken.win.mean() * 100)), expR=round(float(taken.R.mean()), 2), pf=round(pf, 2) if pf != float("inf") else None,
                   fees=round(float(taken.fees.sum()), 2), avg_hold_bars=round(float(pd.to_numeric(taken.bars_held, errors="coerce").mean() or 0), 1))
        L += ["## Overall", f"- net **{res['net']:+,.0f} USD** ({res['net']/START_EQ*100:+.2f}%), equity {res['equity']:,.0f}, max drawdown {res['max_dd']:.2f}% (kill 3%)",
              f"- win {res['win']}%, expectancy {res['expR']:+.2f}R, profit factor {res['pf']}, fees {res['fees']:.0f} USD", ""]
        taken["vol_b"] = pd.cut(pd.to_numeric(taken.vol_ratio, errors="coerce"), [0, 1.3, 2.0, 99], labels=["vol <1.3x", "vol 1.3-2x", "vol >2x"])
        taken["atr_b"] = pd.cut(pd.to_numeric(taken.atr_pct, errors="coerce"), [0, 2, 4, 99], labels=["calm <2%", "normal 2-4%", "wild >4%"])
        taken["hour_b"] = pd.to_datetime(taken.ts_open).dt.hour // 6 * 6
        taken["hour_b"] = taken.hour_b.astype(str) + "-" + (taken.hour_b + 6).astype(str) + " UTC"
        for col, label in (("pair", "coin"), ("vol_b", "breakout volume"), ("atr_b", "volatility"), ("hour_b", "entry hour"), ("exit_reason", "exit"), ("decision", "accepted by")):
            rows = bucket_table(taken, col, label); res["buckets"] += rows
            L.append(f"## By {label}"); L += [f"- {r['bucket']}: {r['n']} trades, {r['expR']:+.2f}R, net {r['net']:+,.0f}, win {r['win']}%" for r in rows]; L.append("")
        # lessons: buckets clearly worse than the rest
        n = len(taken); S = []
        if n < 30: S.append(f"Only {n} trades — no rule change before 30. Collecting.")
        for r in res["buckets"]:
            if r["n"] >= MIN_BUCKET and r["expR"] <= -0.25 and res["expR"] - r["expR"] >= 0.3 and not r["bucket"].startswith("exit"):
                S.append(f"{r['bucket']} loses {r['expR']:+.2f}R over {r['n']} trades vs {res['expR']:+.2f}R overall — candidate filter; test on the backtest first.")
        stops = taken.exit_reason.eq("stop").mean()
        early = (pd.to_numeric(taken.bars_held, errors="coerce") <= 2) & (taken.R < 0)
        if n >= 20 and early.mean() > 0.35: S.append(f"{early.mean()*100:.0f}% of trades are false breakouts stopped within 2 bars — test requiring volume ≥ 1.5× on the breakout bar.")
        giveback = (pd.to_numeric(taken.peak_R, errors="coerce") >= 1.5) & (taken.R < 0)
        if n >= 20 and giveback.sum() >= 4: S.append(f"{giveback.sum()} trades were +1.5R or better and still closed as losses — test a tighter trail (2.5×ATR) or a breakeven stop at +1.5R.")
        if dd > 0.015: S.append(f"Drawdown reached {dd*100:.1f}% — risk stays 0.25% until a new equity high; consider 0.15% if it passes 2%.")
        recent = taken.tail(20)
        if n >= 40 and recent.R.mean() < taken.R.mean() - 0.2: S.append("Last 20 trades clearly worse than the full sample — possible decay; re-run the backtest with the latest data.")
        res["suggestions"] = S
        # per-trade lessons for the journal view
        for r in taken.tail(30).itertuples():
            res["lessons"].append(dict(trade_id=r.trade_id, pair=r.pair, R=round(float(r.R), 2), exit=r.exit_reason, bars=r.bars_held, peak_R=r.peak_R, vol=r.vol_ratio))
    if len(shadow):
        v = dict(n=int(len(shadow)), expR=round(float(shadow.R.mean()), 2), net=round(float(shadow.net_pnl.sum()), 0))
        res["veto"] = v
        verdict = "your vetoes are saving money" if v["expR"] < (res.get("expR") or 0) else "the trades you vetoed did better than the ones taken — the veto is costing money"
        L += ["## Veto layer (shadow-tracked rejected proposals)", f"- {v['n']} vetoed, they would have made {v['expR']:+.2f}R avg ({v['net']:+,.0f} USD): {verdict}", ""]
        res["veto"]["verdict"] = verdict
    L.append("## Suggestions (proposals — nothing changes until the owner approves and the backtest confirms)")
    L += [f"- {s}" for s in res["suggestions"]] or ["- none yet"]
    OUT_MD.write_text("\n".join(L), encoding="utf-8"); OUT_JSON.write_text(json.dumps(res, default=str))
    print("\n".join(L))


if __name__ == "__main__":
    main()
