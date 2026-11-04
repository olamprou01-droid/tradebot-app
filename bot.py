"""
TradeBot v2 — paper trading engine + phone app feed + ntfy alerts.

Every minute:
  1. Pull Kraken history for each coin; on a newly closed 4H bar ask the strategy (same code as the
     backtest) whether that bar is a signal. Build a WATCH view for every coin: regime, trigger level,
     distance to trigger, volume vs average, status ARMED / WATCH / OFF.
  2. A signal becomes a PROPOSAL -> phone (ntfy, ACCEPT/REJECT buttons) + app + desktop page.
     No answer in PROPOSAL_TIMEOUT_MIN = accept (paper). A REJECTED proposal is still tracked as a
     SHADOW trade (no equity impact) so the review can price your vetoes.
  3. Open positions are marked on live Kraken bid; stop / trail / time exits simulated with
     4 bps fee + 2 bps slippage per side; every closed trade -> trades_ledger.csv.
  4. Risk limits enforced in code: daily, weekly, 2% halt, 3% kill.
  5. State, watch, ledger, journal, review are published to the GitHub repo the phone app reads
     (data branch, so it never triggers a Pages build). Notes typed in the app come back via ntfy.

Run: run_bot.bat    Desktop page: http://127.0.0.1:8787    Phone app: https://<user>.github.io/<repo>/
"""
import os, csv, json, time, uuid, base64, hashlib, threading, pathlib, datetime as dt, html, sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import requests, pandas as pd
import config, exchange
import backtest as b, strategies as S, ported as PT

HERE = pathlib.Path(__file__).parent
LIVE = HERE / "live_data"; LIVE.mkdir(exist_ok=True)
OUT = HERE / "app_data"; OUT.mkdir(exist_ok=True)
STATE_F, LEDGER_F, LOG_F, JOURNAL_F = HERE / "state.json", HERE / "trades_ledger.csv", HERE / "bot_log.txt", HERE / "journal.json"

# ---------------- settings (edit here, nowhere else) ----------------
STRATEGY = "S4 trend_trail + S002 reversal"   # 4H trend breakout (trailing) + 1H oversold reversal (3% stop, ROI/sell-signal exit)
STRATEGY_TF_MIN = 240
PAIRS = ["ETHUSD", "SOLUSD", "LINKUSD", "XRPUSD"]   # decision memo 2026-11-03 (compare_report.md)
RISK = 0.01                                         # 1% per trade in the EVALUATION — Intermediate plan (5% max DD), memo §14
RISK_CUT_BELOW, RISK_CUT = -0.025, 0.005            # below -2.5% on the attempt, risk halves to 0.5%
START_EQUITY = config.FUNDED["start_equity"]
LIMITS = dict(daily=0.010, weekly=0.015, halt=0.050, kill=0.060, target=0.10)   # Kraken Prop STARTER: 10% target, 6% max DD static, 3% daily (memo §16)
MAX_OPEN = 2                                        # across both strategies, one position per coin
REV_STOP, REV_ROI_ANY, REV_ROI_AFTER1 = 0.03, 0.05, 0.01   # S002: fixed 3% stop; exit at +5% any time, +1% after 1 closed bar, or on its sell signal
PROPOSAL_TIMEOUT_MIN = 30                   # cloud bot runs every ~10 min, so 3 chances to answer
DON, ATR_MULT, TRAIL_ATR, TIME_STOP_BARS = 20, 2.0, 3.0, 180
FUNDING_4H = 0.000055                       # Kraken Prop swap: 0.033%/day charged per 4h rollover, on notional
NEAR_STOP_PCT, MILESTONES = 0.0075, (1, 2, 3, 5)   # alert when price is within 0.75% of the stop; TP checkpoints in R
NTFY = "https://ntfy.sh"
NTFY_TOPIC = os.getenv("NTFY_TOPIC", "tradebot-eth-7k2m9x")
NTFY_CTL = NTFY_TOPIC + "-ctl"
GH_REPO, GH_TOKEN, GH_DATA_BRANCH = os.getenv("GITHUB_REPO", ""), os.getenv("GITHUB_TOKEN", ""), "data"
PUBLISH_EVERY = 90
PORT = 8787
FEE, SLIP = b.FEE, b.SLIP
LEDGER_COLS = ["trade_id", "ts_open", "ts_close", "pair", "side", "strategy", "regime", "entry", "exit", "stop", "target",
               "size", "capital_at_risk", "fees", "funding", "gross_pnl", "net_pnl", "return_pct", "signal", "exit_reason", "decision",
               "vol_ratio", "atr_pct", "bars_held", "peak_R", "R"]


def now(): return dt.datetime.now(dt.timezone.utc)
def P(x):
    """price formatting for messages: 41862.1 / 533.78 / 2.1034"""
    x = float(x); return f"{x:,.1f}" if x >= 1000 else (f"{x:.2f}" if x >= 10 else f"{x:.4f}")
def iso(ts): return dt.datetime.fromtimestamp(ts, dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def log(msg):
    line = f"{now():%Y-%m-%d %H:%M:%S} {msg}"
    print(line, flush=True)
    with open(LOG_F, "a", encoding="utf-8") as f: f.write(line + "\n")


PRIO = {"min": 1, "low": 2, "default": 3, "high": 4, "urgent": 5}
def notify(title, msg, actions=None, priority="default", tags=""):
    """ntfy JSON publish — UTF-8 safe (emoji in titles), actions as objects."""
    body = {"topic": NTFY_TOPIC, "title": title, "message": msg, "priority": PRIO.get(priority, 3),
            "tags": [t for t in tags.split(",") if t]}
    if actions: body["actions"] = actions
    try: requests.post(NTFY, json=body, timeout=10)
    except Exception as e: log(f"ntfy failed: {e}")


# ---------------- state ----------------
def load_state():
    st = json.loads(STATE_F.read_text()) if STATE_F.exists() else {}
    defaults = dict(equity=START_EQUITY, peak=START_EQUITY, open=[], shadow=[], pending=None, last_bar={}, halted=False, killed=False,
                    day="", day_pnl=0.0, week="", week_pnl=0.0, history=[], watch={}, ctl_since=int(time.time()), reviewed="", started=time.time(), manual=[])
    for k, v in defaults.items(): st.setdefault(k, v)
    return st


def save_state(st): STATE_F.write_text(json.dumps(st, indent=1, default=str))


def ledger_write(row):
    new = not LEDGER_F.exists()
    with open(LEDGER_F, "a", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=LEDGER_COLS)
        if new: w.writeheader()
        w.writerow({k: row.get(k, "") for k in LEDGER_COLS})


def journal_load(): return json.loads(JOURNAL_F.read_text()) if JOURNAL_F.exists() else []
def journal_add(entry):
    j = journal_load(); j.insert(0, entry); JOURNAL_F.write_text(json.dumps(j[:500], indent=1))


# ---------------- market data & watch ----------------
def refresh_history(pair):
    """Kraken -> CSVs in the backtest format so strategy code runs unchanged. Last row = open candle."""
    for m in (60, 240, 1440):
        rows = exchange.ohlc(pair, m)
        pd.DataFrame([[r[0], r[1], r[2], r[3], r[4], r[6]] for r in rows],
                     columns=["ts", "open", "high", "low", "close", "volume"]).to_csv(LIVE / f"{pair}_{m}.csv", index=False)
    return int(pd.read_csv(LIVE / f"{pair}_{STRATEGY_TF_MIN}.csv").ts.iloc[-1])


def build_watch(pair, live_px):
    """what the app shows per coin: regime, trigger, distance, status, chart candles."""
    b.DATA = LIVE
    df = S.load_tf(pair, STRATEGY_TF_MIN)
    i = len(df) - 2                                   # last closed bar
    trig = float(df.high.iloc[i - DON + 1:i + 1].max())   # breakout level for the bar now forming
    atr = float(df.atr.iloc[i]); vol_ratio = float(df.volume.iloc[i] / df.vol20.iloc[i]) if df.vol20.iloc[i] else 0
    reg = bool(df.regime_up.iloc[i]); dist = (trig - live_px) / live_px
    status = "OFF" if not reg else ("ARMED" if dist <= 0.01 else "WATCH")
    h1 = S.load_tf(pair, 60); j = len(h1) - 2
    rsi1 = float(PT.rsi(h1.close).iloc[j]); sk = float(PT.stoch_slowk(h1).iloc[j]); bbl = float(PT.bb_lower(h1).iloc[j]); hm = int(PT.hammer(h1).iloc[j])
    rev = dict(rsi=rsi1, conditions=int(rsi1 < 30) + int(sk < 20) + int(h1.close.iloc[j] < bbl) + int(hm == 100), of=4)
    if rev["conditions"] >= 3 and status == "OFF": status = "REV"       # reversal setup nearly complete even though trend is down
    cs = df.iloc[-150:]
    don = df.high.rolling(DON).max().shift(1).iloc[-150:]
    return dict(pair=pair, px=live_px, regime_up=reg, trigger=trig, dist_pct=dist * 100, atr_pct=atr / live_px * 100,
                vol_ratio=vol_ratio, status=status, stop_if_entered=live_px - ATR_MULT * atr, reversal=rev,
                candles=[[int(r.ts), round(r.open, 6), round(r.high, 6), round(r.low, 6), round(r.close, 6), round(r.volume, 2)] for r in cs.itertuples()],
                donchian=[[int(t), round(v, 6)] for t, v in zip(cs.ts, don) if v == v])


def signal_now(pair):
    """Is the last CLOSED 4H bar an entry signal? Same rules as strategies.s4_trend_trail (regime up,
    close above the previous 20 highs, volume >= 20-bar average, stop distance <= 8% of price).
    Evaluated directly so an earlier signal still "open" in the backtest walker cannot hide a new one."""
    b.DATA = LIVE
    tf = S.load_tf(pair, STRATEGY_TF_MIN); i = len(tf) - 2
    hh = float(tf.high.iloc[i - DON:i].max())
    if not (bool(tf.regime_up.iloc[i]) and float(tf.close.iloc[i]) > hh and float(tf.volume.iloc[i]) >= float(tf.vol20.iloc[i])):
        return None
    atr = float(tf.atr.iloc[i])
    if ATR_MULT * atr / float(tf.close.iloc[i]) > 0.08: return None
    return dict(pair=pair, stop_dist=ATR_MULT * atr, rr=None, atr=atr, vol_ratio=float(tf.volume.iloc[i] / tf.vol20.iloc[i]), signal="trend_trail", regime="up", strat="S4")


def signal_now_reversal(pair):
    """S002 (community strategy, ported): last CLOSED 1H bar has RSI<30, slow stoch<20, close below lower Bollinger
    and a hammer candle. Fixed 3% stop. No trend filter — it buys the washout."""
    b.DATA = LIVE
    h1 = S.load_tf(pair, 60); j = len(h1) - 2
    buy, _ = PT.s002(h1)
    if not bool(buy.iloc[j]): return None
    px = float(h1.close.iloc[j]); atr = float(h1.atr.iloc[j])
    return dict(pair=pair, stop_dist=px * REV_STOP, rr=None, atr=atr, vol_ratio=float(h1.volume.iloc[j] / h1.vol20.iloc[j]) if h1.vol20.iloc[j] else 1.0,
                signal="reversal", regime="washout", strat="S002")


# ---------------- risk ----------------
def risk_check(st):
    today = now().strftime("%Y-%m-%d"); week = now().strftime("%G-W%V")
    if st["day"] != today: st["day"], st["day_pnl"] = today, 0.0
    if st["week"] != week: st["week"], st["week_pnl"] = week, 0.0
    dd = 1 - st["equity"] / START_EQUITY
    if dd >= LIMITS["kill"] and not st["killed"]:
        st["killed"] = True; notify("KILL LINE HIT", f"Equity {st['equity']:.0f} is -{dd*100:.1f}% from start. The real account would be closed. Bot stopped.", priority="urgent", tags="skull")
    if dd >= LIMITS["halt"] and not st["halted"] and not st["killed"]:
        st["halted"] = True; notify(f"HALT: {LIMITS['halt']*100:.0f}% drawdown", f"1% left before the {LIMITS['kill']*100:.0f}% kill line. No new entries until you clear it in the app.", priority="high", tags="warning")
    reasons = []
    if st["killed"]: reasons.append("kill line")
    if st["halted"]: reasons.append("halted")
    if -st["day_pnl"] >= LIMITS["daily"] * st["equity"]: reasons.append("daily cap")
    if -st["week_pnl"] >= LIMITS["weekly"] * st["equity"]: reasons.append("weekly cap")
    st["blocked"] = ", ".join(reasons)
    return bool(reasons)


# ---------------- proposals ----------------
def risk_now(eq):
    """0.5% normally; 0.25% once the attempt is more than 1.5% under water (replay 2018-26: fewer kills, same passes)."""
    return RISK_CUT if eq / START_EQUITY - 1 < RISK_CUT_BELOW else RISK


def make_proposal(st, sig):
    t = exchange.ticker(sig["pair"]); entry = t["ask"] * (1 + SLIP); stop = entry - sig["stop_dist"]; r = entry - stop
    risk = risk_now(st["equity"])
    size = st["equity"] * risk / r; size *= 1 - 2 * FEE * entry / r
    size = round(size, exchange.asset_pair(sig["pair"])["lot_decimals"])
    if size <= 0 or r <= 0:
        log(f"proposal skipped {sig['pair']}: stop distance too small for the fee budget"); return None
    target = entry + sig["rr"] * r if sig["rr"] else None
    notional = size * entry
    return dict(id=uuid.uuid4().hex[:8], pair=sig["pair"], entry=entry, stop=stop, target=target, size=size, r=r, risk=risk,
                notional=notional, pct_of_account=notional / st["equity"] * 100,
                max_loss=size * r + 2 * size * entry * FEE, signal=sig["signal"], regime=sig["regime"], atr=sig["atr"], strat=sig.get("strat", "S4"),
                vol_ratio=sig["vol_ratio"], atr_pct=sig["atr"] / entry * 100,
                created=time.time(), expires=time.time() + PROPOSAL_TIMEOUT_MIN * 60, decision=None)


def push_proposal(p):
    kind = "REVERSAL · 1H washout, exit +1-5% or sell signal" if p.get("strat") == "S002" else "TREND · 4H breakout, trailing exit, no fixed TP"
    body = (f"BUY {p['pair']} @ {P(p['entry'])}\nORDER SIZE: {p['notional']:.0f} USD = {p['pct_of_account']:.1f}% of account ({p['size']} {p['pair'][:-3]})\n"
            f"STOP {P(p['stop'])} ({(p['stop']/p['entry']-1)*100:+.1f}%) · {kind}\nmax loss ${p['max_loss']:.0f}\n"
            f"volume {p['vol_ratio']:.1f}× avg · ATR {p['atr_pct']:.1f}%\nNo answer in {PROPOSAL_TIMEOUT_MIN} min = accept (paper)")
    actions = [{"action": "http", "label": "ACCEPT", "url": f"{NTFY}/{NTFY_CTL}", "method": "POST", "body": f"accept:{p['id']}", "clear": True},
               {"action": "http", "label": "REJECT", "url": f"{NTFY}/{NTFY_CTL}", "method": "POST", "body": f"reject:{p['id']}", "clear": True}]
    notify(f"{'🔄' if p.get('strat') == 'S002' else '📈'} PROPOSAL · BUY {p['pair']} @ {P(p['entry'])}", body, actions=actions, priority="high", tags="chart_with_upwards_trend")


def poll_control(st):
    """answers and notes from the phone (ntfy control topic)."""
    try:
        r = requests.get(f"{NTFY}/{NTFY_CTL}/json?poll=1&since={st['ctl_since']}", timeout=10)
        n_msgs = sum(1 for l in r.text.splitlines() if '"event":"message"' in l.replace(" ", ""))
        if n_msgs or os.getenv("BOT_RUNTIME") == "cloud": log(f"ctl poll since {iso(st['ctl_since'])[11:16]}Z: http {r.status_code}, {n_msgs} message(s)")
        for line in r.text.splitlines():
            m = json.loads(line)
            if m.get("event") != "message": continue
            st["ctl_since"] = max(st["ctl_since"], int(m.get("time", 0)) + 1)
            msg = m.get("message", "").strip(); low = msg.lower()
            log(f"phone -> bot: {msg[:80]}")
            if low in ("accept:testonly", "reject:testonly"):
                notify("✅ Your tap reached the bot", f"Received '{msg}' from your phone at {iso(int(m.get('time', time.time())))[11:16]} UTC. The ACCEPT/REJECT buttons work.", tags="white_check_mark"); continue
            p = st.get("pending")
            if (low.startswith("accept:") or low.startswith("reject:")) and p and low.split(":", 1)[1] == p["id"]:
                decide(st, low.split(":")[0], "phone")
            elif low == "clearhalt":
                st["halted"] = False; log("halt cleared from phone"); notify("Halt cleared", "New entries allowed again.", tags="white_check_mark")
            elif low.startswith("note:"):
                _, tid, text = msg.split(":", 2)
                journal_add(dict(ts=iso(time.time()), trade_id=tid.strip(), note=text.strip(), by="owner")); log(f"journal note on {tid}: {text[:60]}")
            elif low.startswith("mopen:"):           # mopen:PAIR:price:usd_size:note
                _, pair, price, usd, note = (msg.split(":", 4) + [""])[:5]
                m = dict(id="m" + uuid.uuid4().hex[:6], pair=pair.strip().upper(), entry=float(price), usd=float(usd or 0), note=note.strip(), ts_open=time.time(), by="owner")
                st["manual"].append(m); log(f"MANUAL OPEN {m}")
                journal_add(dict(ts=iso(time.time()), trade_id=m["id"], note=f"Real trade opened: {m['pair']} @ {P(m['entry'])}, {m['usd']:.0f} USD. {m['note']}", by="owner"))
                notify(f"📝 Logged real trade {m['pair']} @ {P(m['entry'])}", f"{m['usd']:.0f} USD · shown on every screen. Close it from the app when you exit.", tags="memo")
            elif low.startswith("mclose:"):          # mclose:id:price:note
                _, mid, price, note = (msg.split(":", 3) + [""])[:4]
                for m in list(st["manual"]):
                    if m["id"] == mid.strip(): close_manual(st, m, float(price), note.strip())
    except Exception as e:
        log(f"ctl poll failed: {e}")


def decide(st, decision, who):
    p = st.get("pending")
    if not p or p.get("decision"): return
    p["decision"] = decision; st["pending"] = None
    pos = dict(p, trade_id=p["id"], ts_open=time.time(), hi=p["entry"], bars=0, peak_R=0.0, decision=f"{decision}:{who}")
    if decision == "accept" and len(st["open"]) < MAX_OPEN and p["pair"] not in [o["pair"] for o in st["open"]]:
        st["open"].append(pos); log(f"OPEN {p['pair']} {p['size']} @ {P(p['entry'])} stop {P(p['stop'])} ({who})")
        notify(f"🟢 OPENED {p['pair']} @ {P(p['entry'])}", f"{p.get('notional', 0):.0f} USD ({p.get('pct_of_account', 0):.1f}% of account) · now place a STOP LOSS order at {P(p['stop'])} ({(p['stop']/p['entry']-1)*100:+.1f}%)\nI will tell you when price gets near it and when to move it up.", tags="white_check_mark")
        journal_add(dict(ts=iso(time.time()), trade_id=p["id"], note=f"Opened {p['pair']} @ {P(p['entry'])} ({who}). Volume {p['vol_ratio']:.1f}× avg, ATR {p['atr_pct']:.1f}%.", by="bot"))
    else:
        pos["shadow"] = True; st["shadow"].append(pos)
        log(f"REJECTED {p['id']} {p['pair']} ({who}) — tracking as shadow")
        journal_add(dict(ts=iso(time.time()), trade_id=p["id"], note=f"Vetoed {p['pair']} @ {P(p['entry'])} ({who}). Tracked as shadow trade.", by="bot"))
    st["history"].insert(0, f"{now():%m-%d %H:%M} {decision.upper()} {p['pair']} @ {P(p['entry'])} ({who})"); st["history"] = st["history"][:30]


# ---------------- positions ----------------
def mark_positions(st, bar_closed_pairs, bar_closed_1h=()):
    for m in st.get("manual", []):
        try: m["mark"] = exchange.ticker(m["pair"])["bid"]; m["upnl"] = m["usd"] * (m["mark"] / m["entry"] - 1)
        except Exception as e: log(f"manual mark {m['pair']}: {e}")
    for lst in (st["open"], st["shadow"]):
        for pos in list(lst):
            t = exchange.ticker(pos["pair"]); bid = t["bid"]
            rev_exit = None
            if pos.get("strat") == "S002":
                if pos["pair"] in bar_closed_1h:                   # reversal: evaluated on each closed 1H bar
                    b.DATA = LIVE; h1 = S.load_tf(pos["pair"], 60); j = len(h1) - 2; c = float(h1.close.iloc[j])
                    pos["bars"] = pos.get("bars", 0) + 1; gain = c / pos["entry"] - 1
                    if gain >= REV_ROI_ANY or (pos["bars"] >= 1 and gain >= REV_ROI_AFTER1): rev_exit = ("roi", c)
                    else:
                        _, sell = PT.s002(h1)
                        if bool(sell.iloc[j]): rev_exit = ("sell_signal", c)
            elif pos["pair"] in bar_closed_pairs:                   # trend: trail updates on 4H bar close only
                b.DATA = LIVE; tf = S.load_tf(pos["pair"], STRATEGY_TF_MIN); c, atr_now = float(tf.close.iloc[-2]), float(tf.atr.iloc[-2])
                pos["bars"] = pos.get("bars", 0) + 1
                pos["hi"] = max(pos["hi"], c); pos["stop"] = max(pos["stop"], pos["hi"] - TRAIL_ATR * atr_now)   # same as backtest walker
            pos["mark"] = bid; pos["upnl"] = (bid - pos["entry"]) * pos["size"]
            pos["R_now"] = (bid - pos["entry"]) / pos["r"]; pos["peak_R"] = max(pos.get("peak_R", 0), pos["R_now"])
            pos["stop_dist_pct"] = (bid - pos["stop"]) / bid * 100
            if not pos.get("shadow"): position_alerts(pos, bid)
            reason = None
            if bid <= pos["stop"]: reason, px = "stop", pos["stop"] * (1 - SLIP)
            elif rev_exit: reason, px = rev_exit[0], rev_exit[1] * (1 - SLIP)
            elif pos.get("target") and bid >= pos["target"]: reason, px = "target", pos["target"] * (1 - SLIP)
            elif pos.get("strat") != "S002" and pos.get("bars", 0) >= TIME_STOP_BARS: reason, px = "time", bid * (1 - SLIP)
            if reason: close_position(st, pos, px, reason, shadow=pos.get("shadow", False))


def position_alerts(pos, bid):
    """owner closes manually in the Funded app, so he needs to know: near the stop, stop moved, TP checkpoints."""
    pair, r = pos["pair"], pos["r"]
    if pos["stop_dist_pct"] <= NEAR_STOP_PCT * 100 and not pos.get("near_alerted"):
        pos["near_alerted"] = True
        notify(f"🟡 {pair} NEAR STOP · {P(bid)} is {pos['stop_dist_pct']:.2f}% above {P(pos['stop'])}",
               f"Close at market if it trades through {P(pos['stop'])}. Current {pos['R_now']:+.1f}R ({pos['upnl']:+.0f} USD).", priority="high", tags="warning")
    elif pos["stop_dist_pct"] > NEAR_STOP_PCT * 200: pos["near_alerted"] = False
    if pos["stop"] > pos.get("stop_alerted", pos["stop"]) + 0.25 * r:
        locked = (pos["stop"] - pos["entry"]) / r
        notify(f"🔵 {pair} STOP MOVED UP → {P(pos['stop'])}", f"Edit your stop-loss order to {P(pos['stop'])} ({locked:+.1f}R {'locked in' if locked > 0 else 'from entry'}). Price {P(bid)}.", tags="arrow_up")
    pos.setdefault("stop_alerted", pos["stop"]); pos["stop_alerted"] = max(pos["stop_alerted"], pos["stop"])
    done = set(pos.get("milestones", []))
    for m in MILESTONES:
        if pos["R_now"] >= m and m not in done:
            done.add(m); notify(f"🏁 {pair} +{m}R reached @ {P(bid)}", f"+{pos['upnl']:.0f} USD. " + ("Reversal trade: it exits at +1% after the first closed hour, +5% any time, or on its sell signal — I will tell you." if pos.get("strat") == "S002" else f"No fixed TP in this strategy — the trail ({P(pos['stop'])}) does the exit. Take partial profit here only if you decide so."), tags="checkered_flag")
    pos["milestones"] = sorted(done)


def close_position(st, pos, px, reason, shadow=False):
    gross = (px - pos["entry"]) * pos["size"]; fees = pos["size"] * (pos["entry"] + px) * FEE
    funding = pos["size"] * pos["entry"] * FUNDING_4H * pos.get("bars", 0); net = gross - fees - funding
    R = net / pos["max_loss"]
    (st["shadow"] if shadow else st["open"]).remove(pos)
    if not shadow:
        st["equity"] += net; st["peak"] = max(st["peak"], st["equity"]); st["day_pnl"] += net; st["week_pnl"] += net
    ledger_write(dict(trade_id=pos["trade_id"], ts_open=iso(pos["ts_open"]), ts_close=iso(time.time()), pair=pos["pair"], side="long",
                      strategy=pos.get("strat", "S4"), regime=pos["regime"], entry=round(pos["entry"], 6), exit=round(px, 6), stop=round(pos["stop"], 6),
                      target=round(pos["target"], 6) if pos.get("target") else "", size=pos["size"], capital_at_risk=round(pos["max_loss"], 2),
                      fees=round(fees, 2), funding=round(funding, 2), gross_pnl=round(gross, 2), net_pnl=round(net, 2), return_pct=round(net / START_EQUITY * 100, 3),
                      signal=pos["signal"], exit_reason=("shadow_" if shadow else "") + reason, decision=pos["decision"],
                      vol_ratio=round(pos.get("vol_ratio", 0), 2), atr_pct=round(pos.get("atr_pct", 0), 2), bars_held=pos.get("bars", 0),
                      peak_R=round(pos.get("peak_R", 0), 2), R=round(R, 2)))
    tag = "SHADOW " if shadow else ""
    log(f"{tag}CLOSE {pos['pair']} {reason} @ {P(px)} net {net:+.2f}" + ("" if shadow else f" equity {st['equity']:.2f}"))
    if not shadow:
        notify(f"🔴 CLOSE {pos['pair']} NOW @ {P(px)} · {reason.upper()}", f"{'stop hit' if reason.startswith('stop') else reason} · exit at market now\nnet {net:+.0f} USD ({R:+.1f}R) · equity {st['equity']:.0f}", priority="high", tags="moneybag" if net > 0 else "small_red_triangle_down")
    lesson = auto_lesson(pos, reason, R)
    journal_add(dict(ts=iso(time.time()), trade_id=pos["trade_id"], note=f"{tag}Closed {pos['pair']} {reason} {R:+.1f}R. {lesson}", by="bot"))
    st["history"].insert(0, f"{now():%m-%d %H:%M} {tag}CLOSE {pos['pair']} {reason} {R:+.1f}R"); st["history"] = st["history"][:30]


def close_manual(st, m, px, note):
    st["manual"].remove(m)
    gross = m["usd"] * (px / m["entry"] - 1); fees = m["usd"] * (1 + px / m["entry"]) * FEE
    bars = (time.time() - m["ts_open"]) / 14400; funding = m["usd"] * FUNDING_4H * bars; net = gross - fees - funding
    bot_pos = next((o for o in st["open"] if o["pair"] == m["pair"]), None)
    ledger_write(dict(trade_id=m["id"], ts_open=iso(m["ts_open"]), ts_close=iso(time.time()), pair=m["pair"], side="long", strategy="manual",
                      regime="", entry=m["entry"], exit=px, stop=bot_pos["stop"] if bot_pos else "", target="", size=round(m["usd"] / m["entry"], 6),
                      capital_at_risk="", fees=round(fees, 2), funding=round(funding, 2), gross_pnl=round(gross, 2), net_pnl=round(net, 2),
                      return_pct=round(net / START_EQUITY * 100, 3), signal="manual", exit_reason="manual_" + ("profit" if net > 0 else "loss"),
                      decision="manual", bars_held=round(bars, 1), R=""))
    journal_add(dict(ts=iso(time.time()), trade_id=m["id"], note=f"Real trade closed: {m['pair']} {P(m['entry'])} → {P(px)}, net {net:+.0f} USD ({(px/m['entry']-1)*100:+.2f}%). {note}", by="owner"))
    notify(f"📝 Real trade closed {m['pair']} {net:+.0f} USD", f"{P(m['entry'])} → {P(px)} ({(px/m['entry']-1)*100:+.2f}%) after fees+swap. {note}", tags="memo")
    st["history"].insert(0, f"{now():%m-%d %H:%M} REAL CLOSE {m['pair']} {net:+.0f} USD"); st["history"] = st["history"][:30]
    log(f"MANUAL CLOSE {m['id']} {m['pair']} @ {px} net {net:+.2f}")


def auto_lesson(pos, reason, R):
    """one honest sentence per closed trade — pattern, not prediction."""
    if R < 0 and pos.get("bars", 0) <= 2: return "Stopped within 2 bars: false breakout. Watch volume on the breakout bar."
    if R < 0 and pos.get("peak_R", 0) >= 1.5: return f"Was +{pos['peak_R']:.1f}R at peak and gave it back: trail may be too loose for this coin."
    if R < 0 and reason == "time": return "Went nowhere for 30 days — a breakout that never ran."
    if R > 0 and reason == "stop": return f"Trailed out at +{R:.1f}R after +{pos.get('peak_R', 0):.1f}R peak — normal trend exit."
    if R < 0: return "Normal loss inside the risk budget."
    return "Closed in profit."


# ---------------- publishing for the phone app ----------------
_gh_sha = {}
def gh_put(path, content: bytes, msg):
    if not (GH_REPO and GH_TOKEN): return False
    url = f"https://api.github.com/repos/{GH_REPO}/contents/{path}"
    h = {"Authorization": f"Bearer {GH_TOKEN}", "Accept": "application/vnd.github+json"}
    if path not in _gh_sha:
        g = requests.get(url, headers=h, params={"ref": GH_DATA_BRANCH}, timeout=15)
        _gh_sha[path] = g.json().get("sha") if g.status_code == 200 else None
    body = {"message": msg, "content": base64.b64encode(content).decode(), "branch": GH_DATA_BRANCH}
    if _gh_sha.get(path): body["sha"] = _gh_sha[path]
    r = requests.put(url, headers=h, json=body, timeout=20)
    if r.status_code in (200, 201):
        _gh_sha[path] = r.json()["content"]["sha"]; return True
    _gh_sha.pop(path, None); log(f"github put {path} failed {r.status_code}: {r.text[:120]}"); return False


def gh_ensure_branch():
    if not (GH_REPO and GH_TOKEN): return
    h = {"Authorization": f"Bearer {GH_TOKEN}", "Accept": "application/vnd.github+json"}
    r = requests.get(f"https://api.github.com/repos/{GH_REPO}/git/ref/heads/{GH_DATA_BRANCH}", headers=h, timeout=15)
    if r.status_code == 200: return
    base = requests.get(f"https://api.github.com/repos/{GH_REPO}", headers=h, timeout=15).json().get("default_branch", "main")
    sha = requests.get(f"https://api.github.com/repos/{GH_REPO}/git/ref/heads/{base}", headers=h, timeout=15).json()["object"]["sha"]
    requests.post(f"https://api.github.com/repos/{GH_REPO}/git/refs", headers=h, json={"ref": f"refs/heads/{GH_DATA_BRANCH}", "sha": sha}, timeout=15)
    log(f"created branch {GH_DATA_BRANCH}")


_last_pub, _pub_hash = 0, {}
def publish(st, force=False):
    """write app_data/*.json locally and push changed files to GitHub (data branch)."""
    global _last_pub
    files = {
        "state.json": json.dumps(dict(
            ts=time.time(), strategy=STRATEGY, pairs=PAIRS, risk=risk_now(st["equity"]), risk_base=RISK, start=START_EQUITY, equity=st["equity"], peak=st["peak"],
            day_pnl=st["day_pnl"], week_pnl=st["week_pnl"], limits=LIMITS, halted=st["halted"], killed=st["killed"], blocked=st.get("blocked", ""),
            pending=st["pending"], open=st["open"], shadow=st["shadow"], manual=st.get("manual", []), history=st["history"][:30], started=st["started"],
            mode=os.getenv("BOT_RUNTIME", "pc")), default=str),
        "watch.json": json.dumps(st["watch"]),
        "journal.json": json.dumps(journal_load()[:200]),
    }
    if LEDGER_F.exists(): files["ledger.csv"] = LEDGER_F.read_text(encoding="utf-8")
    if (HERE / "review.json").exists(): files["review.json"] = (HERE / "review.json").read_text(encoding="utf-8")
    for name, content in files.items(): (OUT / name).write_text(content, encoding="utf-8")
    if not force and time.time() - _last_pub < PUBLISH_EVERY: return
    _last_pub = time.time()
    for name, content in files.items():
        h = hashlib.md5(content.encode()).hexdigest()
        if _pub_hash.get(name) != h and gh_put(name, content.encode(), f"bot: {name}"):
            _pub_hash[name] = h
    app = HERE / "app" / "index.html"
    if app.exists():
        h = hashlib.md5(app.read_bytes()).hexdigest()
        if _pub_hash.get("app") != h and gh_put_main("index.html", app.read_bytes()): _pub_hash["app"] = h


def gh_put_main(path, content):
    """the app itself lives on the default branch (GitHub Pages source)."""
    global GH_DATA_BRANCH
    keep = GH_DATA_BRANCH; GH_DATA_BRANCH = "main"
    try: return gh_put(path, content, "bot: app")
    finally: GH_DATA_BRANCH = keep


# ---------------- desktop page (minimal; the phone app is the main UI) ----------------
PAGE = """<!doctype html><meta charset=utf-8><meta http-equiv=refresh content=30><title>TradeBot</title><style>
body{background:#0b0d10;color:#c9d1d9;font:14px/1.5 ui-monospace,Consolas,monospace;margin:0;padding:16px;max-width:720px}
h1{font-size:13px;letter-spacing:.15em;color:#8b949e;margin:0 0 12px}.k{color:#8b949e}.g{color:#3fb950}.r{color:#f85149}.a{color:#d29922}.t{color:#2dd4bf}
.box{border:1px solid #21262d;border-radius:6px;padding:12px;margin:0 0 12px}.big{font-size:28px;font-weight:600}
button{background:none;border:1px solid #2dd4bf;color:#2dd4bf;font:inherit;letter-spacing:.1em;text-transform:uppercase;padding:8px 18px;border-radius:4px;cursor:pointer;margin-right:8px}
button.r{border-color:#f85149;color:#f85149}table{width:100%%;border-collapse:collapse}td{padding:3px 6px 3px 0}small{color:#8b949e}
</style><h1>TRADEBOT · PAPER · %(strategy)s · %(pairs)s</h1>%(body)s"""


def render(st):
    dd = 1 - st["equity"] / START_EQUITY; col = "g" if st["equity"] >= START_EQUITY else ("r" if dd > 0.02 else "a")
    body = f'<div class=box><span class=k>EQUITY</span> <span class="big {col}">{st["equity"]:,.0f}</span> <span class="{col}">{(st["equity"]/START_EQUITY-1)*100:+.2f}%</span>' \
           f'<br><small>kill -5% · halt -4% · day {st["day_pnl"]:+.0f} · week {st["week_pnl"]:+.0f}' + (f' · <span class=r>blocked: {st["blocked"]}</span>' if st.get("blocked") else "") + '</small>'
    if st["halted"] and not st["killed"]: body += ' <form method=post action=/clear style="display:inline"><button>clear halt</button></form>'
    body += "</div>"
    p = st.get("pending")
    if p:
        body += f'<div class=box style="border-color:#2dd4bf"><span class=t>PROPOSAL</span> {p["pair"]} LONG · entry {P(p["entry"])} · stop {P(p["stop"])} · size {p["size"]} · <span class=r>max loss {p["max_loss"]:.0f}</span>' \
                f'<br><small>auto-accept in {max(0, int((p["expires"]-time.time())/60))} min</small><br><br><form method=post action=/accept style="display:inline"><button>accept</button></form>' \
                f'<form method=post action=/reject style="display:inline"><button class=r>reject</button></form></div>'
    body += "<div class=box><span class=k>WATCH</span><table>"
    for w in st["watch"].values():
        c = {"ARMED": "t", "WATCH": "a", "OFF": "k"}[w["status"]]
        body += f'<tr><td>{w["pair"]}</td><td class={c}>{w["status"]}</td><td>{P(w["px"])}</td><td>trigger {P(w["trigger"])}</td><td>{w["dist_pct"]:+.2f}%</td><td>vol {w["vol_ratio"]:.1f}×</td></tr>'
    body += "</table></div>"
    if st["open"]:
        body += "<div class=box><span class=k>OPEN</span><table>"
        for o in st["open"]:
            u = o.get("upnl", 0); body += f'<tr><td>{o["pair"]}</td><td>{o["size"]} @ {P(o["entry"])}</td><td>stop {P(o["stop"])}</td><td class="{"g" if u >= 0 else "r"}">{u:+.0f}</td></tr>'
        body += "</table></div>"
    if st["history"]: body += "<div class=box><span class=k>LAST</span><br>" + "<br>".join(html.escape(h) for h in st["history"][:8]) + "</div>"
    body += f'<small>phone topic {NTFY_TOPIC} · github {"on" if GH_REPO and GH_TOKEN else "OFF (set GITHUB_REPO/GITHUB_TOKEN in .env)"} · {now():%H:%M:%S} UTC</small>'
    return PAGE % dict(strategy=STRATEGY, pairs=",".join(PAIRS), body=body)


class H(BaseHTTPRequestHandler):
    def log_message(self, *a): pass
    def do_GET(self):
        if self.path == "/desktop":
            with LOCK: body, ctype = render(STATE).encode("utf-8"), "text/html; charset=utf-8"
            self.send_response(200); self.send_header("Content-Type", ctype); self.end_headers(); self.wfile.write(body); return
        if self.path.startswith("/app") or self.path == "/":   # one app, same screen on PC and phone
            body, ctype = (HERE / "app" / "index.html").read_bytes(), "text/html; charset=utf-8"
        elif self.path.startswith("/data/"):
            f = OUT / self.path[6:].split("?")[0]
            if not f.exists(): self.send_response(404); self.end_headers(); return
            body, ctype = f.read_bytes(), ("application/json" if f.suffix == ".json" else "text/plain") + "; charset=utf-8"
        else:
            with LOCK: body, ctype = render(STATE).encode("utf-8"), "text/html; charset=utf-8"
        self.send_response(200); self.send_header("Content-Type", ctype); self.send_header("Cache-Control", "no-store"); self.end_headers()
        self.wfile.write(body)
    def do_POST(self):
        with LOCK:
            if self.path == "/accept": decide(STATE, "accept", "desktop")
            elif self.path == "/reject": decide(STATE, "reject", "desktop")
            elif self.path == "/clear": STATE["halted"] = False; log("halt cleared on desktop")
            save_state(STATE)
        self.send_response(303); self.send_header("Location", "/"); self.end_headers()


LOCK = threading.Lock(); STATE = load_state()


def main():
    if config.MODE != "paper": sys.exit("This engine only runs in paper mode.")
    threading.Thread(target=lambda: ThreadingHTTPServer(("127.0.0.1", PORT), H).serve_forever(), daemon=True).start()
    log(f"bot v2 started · {STRATEGY} · {PAIRS} · risk {RISK*100:.2f}% · desktop http://127.0.0.1:{PORT} · app http://127.0.0.1:{PORT}/app · github {'on' if GH_REPO and GH_TOKEN else 'off'}")
    try: gh_ensure_branch()
    except Exception as e: log(f"github branch check failed: {e}")
    notify("TradeBot v2 running", f"{STRATEGY} on {','.join(PAIRS)} · paper · risk {RISK*100:.2f}%", tags="robot")
    first = True
    while True:
        with LOCK: step(STATE, first)
        first = False
        time.sleep(60)


def step(st, first=False):
    """one full iteration; called every minute on the PC, every ~10 min in the cloud."""
    try:
        blocked = risk_check(st); closed_now, closed_1h = [], []
        for pair in PAIRS:
            last_bar = refresh_history(pair); px = exchange.ticker(pair)["last"]
            st["watch"][pair] = build_watch(pair, px)
            last_1h = int(pd.read_csv(LIVE / f"{pair}_60.csv").ts.iloc[-1])
            sigs = []
            if st["last_bar"].get(pair) != last_bar:
                st["last_bar"][pair] = last_bar; closed_now.append(pair); sigs.append(signal_now(pair))
            if st["last_bar"].get(pair + "_60") != last_1h:
                st["last_bar"][pair + "_60"] = last_1h; closed_1h.append(pair); sigs.append(signal_now_reversal(pair))
            for sig in [x for x in sigs if x]:
                if not blocked and not st["pending"] and pair not in [o["pair"] for o in st["open"]] and len(st["open"]) < MAX_OPEN:
                    st["pending"] = make_proposal(st, sig)
                    if st["pending"]: push_proposal(st["pending"]); log(f"PROPOSAL {st['pending']}")
                else: log(f"{sig['strat']} signal on {pair} ignored (blocked={st.get('blocked')}, pending={bool(st['pending'])}, open={len(st['open'])})")
            w = st["watch"][pair]
            if w["status"] == "ARMED" and not st.get(f"armed_{pair}"):
                notify(f"👀 {pair} ARMED · {P(w['px'])} is {w['dist_pct']:.2f}% below trigger {P(w['trigger'])}", f"Daily trend UP · breakout level {P(w['trigger'])} · vol {w['vol_ratio']:.1f}× avg · stop would be {P(w['stop_if_entered'])}", tags="eyes")
            st[f"armed_{pair}"] = w["status"] == "ARMED"
        poll_control(st)
        if st["pending"] and time.time() > st["pending"]["expires"]: decide(st, "accept", "timeout")
        mark_positions(st, closed_now, closed_1h)
        if now().strftime("%Y-%m-%d") != st.get("reviewed", ""):
            import review; review.main(); st["reviewed"] = now().strftime("%Y-%m-%d")
        save_state(st); publish(st, force=first)
    except Exception as e:
        log(f"loop error: {type(e).__name__}: {e}")


if __name__ == "__main__":
    main()
