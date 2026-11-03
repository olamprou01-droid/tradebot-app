"""
Central config. Everything that decides whether real money is touched lives here.

MODE is locked to "paper" until you change it yourself in a later stage.
Paper mode = live Kraken prices, fills simulated locally. No key required.
"""
import os
from dotenv import load_dotenv

load_dotenv()

MODE = os.getenv("BOT_MODE", "paper")           # "paper" | "live"
if MODE not in ("paper", "live"):
    raise SystemExit(f"BOT_MODE must be paper or live, got {MODE!r}")
PAPER = MODE == "paper"

API_KEY = os.getenv("KRAKEN_API_KEY", "")
API_SECRET = os.getenv("KRAKEN_API_SECRET", "")
ACCOUNT_LABEL = os.getenv("KRAKEN_ACCOUNT_LABEL", "paper")   # must contain 'sub' before live is allowed

# Safety gate: live is refused unless the key is explicitly tagged as a SUBaccount key.
if not PAPER:
    if not API_KEY or not API_SECRET:
        raise SystemExit("Live mode refused: no API key.")
    if "sub" not in ACCOUNT_LABEL.lower():
        raise SystemExit("Live mode refused: KRAKEN_ACCOUNT_LABEL must identify a SUBaccount key, not the main account.")

PAIR = os.getenv("BOT_PAIR", "XBTUSD")           # Kraken pair code (XBTUSD = BTC/USD)

# Kraken Funded $10k tier rules — the paper sim enforces these so a pass here means a pass there.
FUNDED = {
    "start_equity": float(os.getenv("START_EQUITY", "10000")),
    "profit_target_pct": 12.0,
    "max_loss_pct": 3.0,            # breach = all positions closed, trading disabled
    "fee_bps_per_side": 4.0,
    "margin_funding_pct_per_day": 0.033,
}
