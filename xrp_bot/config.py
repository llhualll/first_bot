import os
from dotenv import load_dotenv

load_dotenv()

# ── Exchange ───────────────────────────────────────────────
SYMBOL           = "XRP/USD"
KRAKEN_API_KEY   = os.getenv("KRAKEN_API_KEY", "")
KRAKEN_SECRET    = os.getenv("KRAKEN_SECRET", "")

# ── Mode ───────────────────────────────────────────────────
PAPER_TRADE      = os.getenv("PAPER_TRADE", "true").lower() == "true"
PAUSE            = os.getenv("PAUSE", "false").lower() == "true"

# ── Position sizing ────────────────────────────────────────
TRADE_RATIO      = 0.80   # use 80% of available balance per trade
RESERVE_RATIO    = 0.20   # keep 20% as safety buffer

# ── Fees ───────────────────────────────────────────────────
MAKER_FEE        = 0.0016  # 0.16% limit order (Maker)

# ── Take profit / stop loss ────────────────────────────────
TP1_PCT          = 0.0082  # +0.82% price move → +0.5% net profit (sell 50%)
TP2_PCT          = 0.0132  # +1.32% price move → +1.0% net profit (sell 100%)
SL_PCT           = 0.0080  # -0.80% price move → stop loss (full exit)
BREAKEVEN_BUFFER = 0.0003  # cover fees when moving SL to breakeven after TP1

# ── Risk controls ──────────────────────────────────────────
MAX_DAILY_LOSS   = 0.02    # stop trading if daily loss >= 2%
MAX_DAILY_GAIN   = 0.03    # stop trading if daily gain >= 3% (was 1%, too conservative)
MAX_CONSEC_LOSS  = 2       # pause 24h after this many consecutive losses
MAX_DRAWDOWN_PCT = 0.15    # stop bot if balance drops >15% from peak
FLASH_CRASH_PCT  = 0.05    # halt if XRP drops >5% in 1 hour
FLASH_CRASH_PAUSE_H = 4    # hours to pause after flash crash

# ── Order management ───────────────────────────────────────
ORDER_TIMEOUT_H  = 2       # cancel unfilled limit buy after 2 hours
CHECK_INTERVAL   = 60      # main loop interval in seconds (60 = 1 minute polling)
                           # full strategy check runs every 60 minutes via counter

# ── Technical analysis ─────────────────────────────────────
RSI_PERIOD       = 14
RSI_OVERSOLD     = 50      # enter only when RSI < 50 (uptrend pullback; 40 was too strict)
EMA_FAST         = 20
EMA_SLOW         = 50
SUPPORT_LOOKBACK = 20      # candles to look back for support level
SUPPORT_TOLERANCE= 0.012   # price must be within 1.2% of support (0.5% was too tight)

# ── Scheduling ─────────────────────────────────────────────
DAY_RESET_UTC_HOUR = 0     # reset daily P&L at UTC 00:00
STRATEGY_INTERVAL_MIN = 15 # run full strategy check every 15 minutes (was 60)

# ── Telegram ───────────────────────────────────────────────
TELEGRAM_TOKEN      = os.getenv("TELEGRAM_TOKEN", "")
TELEGRAM_CHAT_ID    = os.getenv("TELEGRAM_CHAT_ID", "")
TELEGRAM_ENABLED    = bool(TELEGRAM_TOKEN and TELEGRAM_CHAT_ID)
DAILY_REPORT_UTC_HOUR = 0  # send daily report at UTC 00:00
