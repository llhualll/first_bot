import csv
import os
from datetime import datetime, timezone
from loguru import logger

LOG_FILE = os.path.join(os.path.dirname(__file__), "trades.csv")

HEADERS = [
    "utc_date", "utc_time", "side", "entry_price", "exit_price",
    "xrp_amount", "usd_value", "fee_usd", "net_profit_usd", "trigger", "mode"
]


def _ensure_file() -> None:
    if not os.path.exists(LOG_FILE):
        with open(LOG_FILE, "w", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=HEADERS)
            writer.writeheader()


def log_trade(
    side: str,
    entry_price: float,
    exit_price: float,
    xrp_amount: float,
    fee_usd: float,
    net_profit_usd: float,
    trigger: str,        # "TP1", "TP2", "SL", "TIMEOUT", "MANUAL"
    paper: bool = False,
) -> None:
    _ensure_file()
    now = datetime.now(timezone.utc)
    usd_value = xrp_amount * exit_price
    row = {
        "utc_date":       now.strftime("%Y-%m-%d"),
        "utc_time":       now.strftime("%H:%M:%S"),
        "side":           side,
        "entry_price":    f"{entry_price:.4f}",
        "exit_price":     f"{exit_price:.4f}",
        "xrp_amount":     f"{xrp_amount:.4f}",
        "usd_value":      f"{usd_value:.2f}",
        "fee_usd":        f"{fee_usd:.4f}",
        "net_profit_usd": f"{net_profit_usd:.4f}",
        "trigger":        trigger,
        "mode":           "PAPER" if paper else "LIVE",
    }
    with open(LOG_FILE, "a", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=HEADERS)
        writer.writerow(row)
    logger.info(f"Trade logged: {trigger} | net={net_profit_usd:+.4f} USD")


def get_today_summary() -> dict:
    """Return today's trade stats: trades, gross_profit, gross_loss, net."""
    _ensure_file()
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    trades = gross_profit = gross_loss = 0.0
    with open(LOG_FILE, "r") as f:
        reader = csv.DictReader(f)
        for row in reader:
            if row["utc_date"] == today:
                trades += 1
                pnl = float(row["net_profit_usd"])
                if pnl >= 0:
                    gross_profit += pnl
                else:
                    gross_loss += pnl
    return {
        "trades":       int(trades),
        "gross_profit": gross_profit,
        "gross_loss":   gross_loss,
        "net":          gross_profit + gross_loss,
    }
