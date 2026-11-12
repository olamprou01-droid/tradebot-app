"""
Cloud runner: one bot iteration with all state in a directory (the repo's `data` branch).
GitHub Actions runs this every ~10 minutes and commits the directory, so nothing has to stay
switched on. The phone app reads the same directory.

    python bot_step.py <state_dir>
"""
import os, sys, pathlib, time
os.environ.setdefault("BOT_RUNTIME", "cloud")
d = pathlib.Path(sys.argv[1] if len(sys.argv) > 1 else "cloud_state").resolve(); d.mkdir(exist_ok=True)

import bot, review
bot.STATE_F, bot.LEDGER_F, bot.JOURNAL_F, bot.LOG_F = d / "bot_state.json", d / "ledger.csv", d / "journal.json", d / "bot_log.txt"
bot.OUT = d; bot.LIVE = d / "live"; bot.LIVE.mkdir(exist_ok=True)
bot.GH_TOKEN = ""                                    # the workflow commits the files itself — no API pushes
review.LEDGER, review.OUT_MD, review.OUT_JSON = d / "ledger.csv", d / "strategy_performance.md", d / "review.json"

st = bot.load_state()
if not st.get("announced_cloud"):
    bot.notify("SYSTEM · ☁️ TradeBot now runs in the cloud", f"{bot.STRATEGY} on {','.join(bot.PAIRS)} · paper · risk {bot.RISK*100:.2f}% · every ~10 min, no PC needed.", tags="system")
    st["announced_cloud"] = True
flag = d / "RUN_TEST"
if os.getenv("TEST_ALERT") or flag.exists():
    if flag.exists(): flag.unlink()
    import selftest
    head, body, fails = selftest.report(selftest.run())
    bot.log(head); bot.log(body)
    bot.notify("SYSTEM · " + ("✅ " if not fails else "❌ ") + head, body[:3800], priority="high" if fails else "default", tags="system,test")
    # one sample of every alert type, clearly marked TEST, so the phone channel is proven end to end
    acts = [{"action": "http", "label": "ACCEPT", "url": f"{bot.NTFY}/{bot.NTFY_CTL}", "method": "POST", "body": "accept:testonly", "clear": True},
            {"action": "http", "label": "REJECT", "url": f"{bot.NTFY}/{bot.NTFY_CTL}", "method": "POST", "body": "reject:testonly", "clear": True}]
    T = "TEST ONLY — not a real signal.\n"
    bot.notify(bot.head("S4", "ETHUSD", 2274.7, "📈 BUY PROPOSAL"), T + "TREND strategy · BUY ETHUSD @ 2,274.7\nORDER SIZE: 3245 USD = 32.5% of account (1.4267 ETH)\nSTOP 2,206.4 (-3.0%) · 4H breakout, trailing exit, no fixed TP\nmax loss $100", actions=acts, tags="trend,proposal,test")
    bot.notify(bot.head("S002", "SOLUSD", 90.12, "🔄 BUY PROPOSAL"), T + "REVERSAL strategy · BUY SOLUSD @ 90.12\nORDER SIZE: 3330 USD = 33.3% of account (36.95 SOL)\nSTOP 87.42 (-3.0%) · 1H washout, exit +1-5% or sell signal\nmax loss $100", actions=acts, tags="reversal,proposal,test")
    bot.notify(bot.head("S4", "ETHUSD", 2274.7, "🟢 OPENED"), T + "3245 USD (32.5% of account) · now place a STOP LOSS order at 2,206.4 (-3.0%)", tags="trend,opened,test")
    bot.notify(bot.head("S4", "ETHUSD", 2274.7, "🔵 MOVE STOP → 2,310.0"), T + "Edit your stop-loss order to 2,310.0 (+0.5R locked in). Price 2,415.0.", tags="trend,stop-moved,test")
    bot.notify(bot.head("S4", "ETHUSD", 2274.7, "🟡 NEAR STOP 2,310.0"), T + "Price 2,322.0 is 0.52% above the stop 2,310.0. Close at market if it trades through it.", tags="trend,near-stop,test")
    bot.notify(bot.head("S002", "SOLUSD", 90.12, "🔴 CLOSE NOW @ 91.05 (ROI)"), T + "roi · exit at market now\nentry 90.12 → exit 91.05\nnet +31 USD (+0.3R)", tags="reversal,close,test")
    bot.log("test alerts sent")
t0 = time.time()
bot.step(st, first=True)                             # force=True publishes every run (the workflow commits only if something changed)
bot.log(f"cloud step done in {time.time()-t0:.0f}s · equity {st['equity']:.2f} · open {len(st['open'])} · pending {bool(st['pending'])} · "
        + " ".join(f"{w['pair'][:-3]}:{w['status']}" for w in st["watch"].values()))
# keep the log small
lines = bot.LOG_F.read_text(encoding="utf-8").splitlines()[-1500:]
bot.LOG_F.write_text("\n".join(lines) + "\n", encoding="utf-8")
# raw candles are a cache, not state — don't commit them
import shutil; shutil.rmtree(bot.LIVE, ignore_errors=True)
