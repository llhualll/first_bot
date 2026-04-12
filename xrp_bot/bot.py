"""
XRP Trading Bot — Main Entry Point
Runs 24/7, checks strategy every 60 minutes.

Usage:
    python bot.py
"""

import time
import sys
from datetime import datetime, timezone
from loguru import logger
from config import (
    PAPER_TRADE, PAUSE, CHECK_INTERVAL,
    STRATEGY_INTERVAL_MIN, DAILY_REPORT_UTC_HOUR,
)
from kraken_client import fetch_balance
from analysis import get_market_data
from strategy import (
    has_open_position, open_position,
    monitor_position, recover_existing_position,
    check_buy_timeout,
)
from risk_manager import RiskManager
from trade_logger import get_today_summary
import notifier

# ── Logging setup ─────────────────────────────────────────────────────────────
logger.remove()
logger.add(sys.stderr, level="INFO", format="{time:YYYY-MM-DD HH:mm:ss} | {level} | {message}")
logger.add("logs/bot.log", rotation="1 day", retention="30 days", level="DEBUG")

import os
os.makedirs("logs", exist_ok=True)


def startup_check() -> float:
    """Verify connection, fetch balance, send Telegram startup notice."""
    logger.info("=" * 50)
    logger.info(f"XRP Bot starting | Mode: {'PAPER TRADE' if PAPER_TRADE else 'LIVE'}")
    logger.info("=" * 50)

    bal = fetch_balance()
    if bal["usd"] == 0.0 and not PAPER_TRADE:
        logger.error("Could not fetch balance. Check API keys.")
        sys.exit(1)

    logger.info(f"Balance: {bal['xrp']:.2f} XRP (~${bal['usd']:.2f}) @ ${bal['price']:.4f}")
    notifier.notify_startup(bal["usd"], bal["xrp"], bal["price"])

    # Recover any existing open position from previous run
    if recover_existing_position():
        logger.info("Existing position recovered from Kraken.")

    return bal["usd"]


def send_daily_report(risk: RiskManager) -> None:
    bal = fetch_balance()
    summary = get_today_summary()
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    notifier.notify_daily_report(
        date=today,
        trades=summary["trades"],
        gross_profit=summary["gross_profit"],
        gross_loss=summary["gross_loss"],
        net=summary["net"],
        balance_usd=bal["usd"],
    )
    risk.update_peak(bal["usd"])


def main_loop() -> None:
    risk = RiskManager()
    balance_usd = startup_check()
    risk.update_peak(balance_usd)

    tick_count      = 0
    last_report_day = None

    logger.info("Entering main loop. Press Ctrl+C to stop.")

    while True:
        try:
            now_utc = datetime.now(timezone.utc)

            # ── Daily report at UTC 00:00 ──────────────────────────────────
            today = now_utc.strftime("%Y-%m-%d")
            if (now_utc.hour == DAILY_REPORT_UTC_HOUR
                    and today != last_report_day):
                send_daily_report(risk)
                last_report_day = today

            # ── Run full strategy check every STRATEGY_INTERVAL_MIN ────────
            tick_count += 1
            run_strategy = (tick_count * (CHECK_INTERVAL / 60)) >= STRATEGY_INTERVAL_MIN

            if run_strategy:
                tick_count = 0
                logger.info(f"── Strategy check @ {now_utc.strftime('%H:%M')} UTC ──")

                if PAUSE:
                    logger.info("Bot manually paused (PAUSE=true in .env).")
                    time.sleep(CHECK_INTERVAL)
                    continue

                # Fetch fresh market data
                market_data = get_market_data()
                if not market_data:
                    logger.warning("No market data. Skipping this cycle.")
                    time.sleep(CHECK_INTERVAL)
                    continue

                # Refresh balance
                balance_usd = fetch_balance()["usd"]
                risk.update_peak(balance_usd)

                # Check open position first
                if has_open_position():
                    check_buy_timeout()
                    result = monitor_position(risk)
                    if result == "TP2":
                        logger.info("TP2 reached. Daily target met — resting until next UTC day.")
                    time.sleep(CHECK_INTERVAL)
                    continue

                # Master risk gate
                if not risk.can_trade(balance_usd, market_data):
                    time.sleep(CHECK_INTERVAL)
                    continue

                # Check entry signal
                from analysis import has_entry_signal
                if has_entry_signal(market_data):
                    logger.info("Entry signal found! Opening position...")
                    open_position(market_data)
                else:
                    logger.info("No signal this cycle. Waiting...")

            time.sleep(CHECK_INTERVAL)

        except KeyboardInterrupt:
            logger.info("Shutdown requested by user.")
            notifier.notify_error("Bot stopped manually (KeyboardInterrupt).")
            break
        except Exception as e:
            logger.exception(f"Unexpected error: {e}")
            notifier.notify_error(f"Unexpected error: {e}")
            time.sleep(60)  # brief pause before retrying


if __name__ == "__main__":
    main_loop()
