"""
Paper wallet — tracks virtual USD balance across bot restarts.
Balance is persisted in paper_wallet.json.
Only used when PAPER_TRADE=true.
"""

import json
import os
from loguru import logger
from config import PAPER_BALANCE_START

WALLET_FILE = "paper_wallet.json"


def get_balance() -> float:
    """Return current virtual USD balance. Creates file with starting balance if missing."""
    if not os.path.exists(WALLET_FILE):
        _save(PAPER_BALANCE_START)
        logger.info(f"[PAPER] New virtual wallet created: ${PAPER_BALANCE_START:.2f}")
        return PAPER_BALANCE_START
    with open(WALLET_FILE) as f:
        data = json.load(f)
    return data.get("usd", PAPER_BALANCE_START)


def update(net_pnl: float) -> float:
    """Add net P&L to virtual balance. Returns new balance."""
    balance = get_balance() + net_pnl
    _save(balance)
    direction = "+" if net_pnl >= 0 else ""
    logger.info(f"[PAPER] Wallet updated: {direction}${net_pnl:.4f} → balance=${balance:.2f}")
    return balance


def reset() -> None:
    """Reset virtual balance back to starting amount."""
    _save(PAPER_BALANCE_START)
    logger.info(f"[PAPER] Wallet reset to ${PAPER_BALANCE_START:.2f}")


def _save(balance: float) -> None:
    with open(WALLET_FILE, "w") as f:
        json.dump({"usd": round(balance, 4)}, f)
