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
    bot.notify("☁️ TradeBot now runs in the cloud", f"{bot.STRATEGY} on {','.join(bot.PAIRS)} · paper · risk {bot.RISK*100:.2f}% · every ~10 min, no PC needed.", tags="robot")
    st["announced_cloud"] = True
flag = d / "RUN_TEST"
if os.getenv("TEST_ALERT") or flag.exists():
    if flag.exists(): flag.unlink()
    import selftest
    head, body, fails = selftest.report(selftest.run())
    bot.log(head); bot.log(body)
    bot.notify(("✅ " if not fails else "❌ ") + head, body[:3800], priority="high" if fails else "default", tags="test_tube")
    # one sample of every alert type, clearly marked TEST, so the phone channel is proven end to end
    bot.notify("🧪 TEST · 📈 PROPOSAL · BUY ETHUSD @ 2,274.7", "TEST ONLY — not a real signal.\nORDER SIZE: 3245 USD = 32.5% of account (1.4267 ETH)\nSTOP 2,206.4 (-3.0%) · TREND · trailing exit\nmax loss $100",
               actions=[{"action": "http", "label": "ACCEPT", "url": f"{bot.NTFY}/{bot.NTFY_CTL}", "method": "POST", "body": "accept:testonly", "clear": True},
                        {"action": "http", "label": "REJECT", "url": f"{bot.NTFY}/{bot.NTFY_CTL}", "method": "POST", "body": "reject:testonly", "clear": True}], tags="chart_with_upwards_trend")
    bot.notify("🧪 TEST · 🟡 ETHUSD NEAR STOP · 2,215.0 is 0.39% above 2,206.4", "TEST ONLY. Close at market if it trades through 2,206.4.", tags="warning")
    bot.notify("🧪 TEST · 🔴 CLOSE ETHUSD NOW @ 2,206.4 · STOP", "TEST ONLY. stop hit · exit at market now", tags="small_red_triangle_down")
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
