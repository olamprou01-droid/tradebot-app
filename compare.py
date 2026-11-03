"""
Strategy × coin comparison. Writes compare_report.md.
Raw edge = per-trade R after fees (fees converted to R). Portfolio = 0.5% risk, Funded kill line on.
Out-of-sample (OOS) = 2025-01-01 onward; nothing was tuned on it.
"""
import sys, datetime as dt, pathlib
import pandas as pd
import backtest as b, strategies as S

OOS_FROM = pd.Timestamp("2025-01-01").timestamp()
pairs = [p.name.split("_")[0] for p in sorted(b.DATA.glob("*_60.csv"))]
if len(sys.argv) > 1: pairs = sys.argv[1:]


def raw_stats(tr):
    if not tr: return dict(n=0)
    df = pd.DataFrame(tr); cost = 2 * b.FEE * df.entry / df.r; net = df.R - cost
    oos = net[df.ts_open >= OOS_FROM]; ins = net[df.ts_open < OOS_FROM]
    return dict(n=len(df), win=(df.R > 0).mean(), netR=net.mean(), netR_in=ins.mean() if len(ins) else float("nan"),
                netR_oos=oos.mean() if len(oos) else float("nan"), n_oos=len(oos), hold=df.hold_h.mean(),
                tgt=(df.reason == "target").mean())


def port(tr):
    rows, eq, dd, killed, deq = b.simulate(tr, 0.005)
    span = (max(t["ts_close"] for t in tr) - min(t["ts_open"] for t in tr)) / 86400 if tr else 1
    return b.metrics(rows, eq, dd, killed, deq, span)


L = ["# compare_report.md — strategy × coin", f"Pairs: {', '.join(pairs)}. Fees 4 bps + slip 2 bps per side. OOS = 2025-01-01 → end.", "",
     "| strategy | pair | trades | win | netR all | netR in-sample | netR OOS (n) | target hit | hold h | 0.5% portfolio: net / maxDD / kill |",
     "|---|---|---|---|---|---|---|---|---|---|"]
best = []
for sname, fn in S.STRATEGIES.items():
    for pair in pairs:
        try:
            tr = fn(pair)
        except Exception as e:
            L.append(f"| {sname} | {pair} | error {type(e).__name__}: {e} |"); continue
        s = raw_stats(tr)
        if s["n"] < 10:
            L.append(f"| {sname} | {pair} | {s['n']} | — | — | — | — | — | — | too few trades |"); continue
        m = port(tr)
        L.append(f"| {sname} | {pair} | {s['n']} | {s['win']*100:.0f}% | {s['netR']:+.3f} | {s['netR_in']:+.3f} | {s['netR_oos']:+.3f} ({s['n_oos']}) | "
                 f"{s['tgt']*100:.0f}% | {s['hold']:.0f} | {m['net_ret']*100:+.1f}% / {m['max_dd']*100:.1f}% / {'KILLED' if m['killed'] else 'ok'} |")
        best.append((s["netR_oos"] if s["n_oos"] >= 15 else -9, s["netR"], sname, pair, s["n"], m["killed"]))
        print(L[-1], flush=True)
L.append("")
L.append("## Ranked by out-of-sample edge (min 15 OOS trades)")
for oos, allr, sname, pair, n, killed in sorted(best, reverse=True)[:12]:
    if oos > -9: L.append(f"- {sname} / {pair}: OOS {oos:+.3f}R, all {allr:+.3f}R, n={n}, {'killed' if killed else 'survived'}")
pathlib.Path(b.HERE / "compare_report.md").write_text("\n".join(L), encoding="utf-8")
print("\n".join(L[-14:]))
