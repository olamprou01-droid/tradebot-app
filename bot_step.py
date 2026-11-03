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
first = not (d / "bot_state.json").exists()
if first:
    bot.notify("☁️ TradeBot now runs in the cloud", f"{bot.STRATEGY} on {','.join(bot.PAIRS)} · paper · risk {bot.RISK*100:.2f}% · every ~10 min, no PC needed.", tags="robot")
t0 = time.time()
bot.step(st, first=True)                             # force=True publishes every run (the workflow commits only if something changed)
bot.log(f"cloud step done in {time.time()-t0:.0f}s · equity {st['equity']:.2f} · open {len(st['open'])} · pending {bool(st['pending'])} · "
        + " ".join(f"{w['pair'][:-3]}:{w['status']}" for w in st["watch"].values()))
# keep the log small
lines = bot.LOG_F.read_text(encoding="utf-8").splitlines()[-1500:]
bot.LOG_F.write_text("\n".join(lines) + "\n", encoding="utf-8")
# raw candles are a cache, not state — don't commit them
import shutil; shutil.rmtree(bot.LIVE, ignore_errors=True)
