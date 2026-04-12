import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from loguru import logger
from config import (
    TP1_PCT, TP2_PCT, SL_PCT, BREAKEVEN_BUFFER,
    MAKER_FEE, TRADE_RATIO, ORDER_TIMEOUT_H, PAPER_TRADE,
)
from kraken_client import (
    fetch_balance, place_limit_buy, place_limit_sell,
    cancel_order, get_order_status, get_order,
)
from trade_logger import log_trade
import notifier


@dataclass
class Position:
    entry_price: float = 0.0
    xrp_amount: float = 0.0       # total XRP bought
    xrp_remaining: float = 0.0   # XRP still held
    usd_spent: float = 0.0

    buy_order_id: str = ""
    buy_placed_at: float = 0.0    # epoch time

    tp1_order_id: str = ""
    tp2_order_id: str = ""
    sl_order_id: str = ""

    tp1_hit: bool = False
    is_open: bool = False

    total_profit: float = 0.0     # accumulates TP1 + TP2 profit


# Module-level position state
_pos = Position()


def has_open_position() -> bool:
    return _pos.is_open


def get_position() -> Position:
    return _pos


def open_position(market_data: dict) -> bool:
    """Place limit buy and attach TP/SL orders. Returns True on success."""
    global _pos

    balance = fetch_balance()
    balance_usd = balance["usd"]
    price = market_data["price"]

    trade_usd = round(balance_usd * TRADE_RATIO, 2)
    if trade_usd < 10:
        logger.warning(f"Trade amount too small: ${trade_usd:.2f}")
        return False

    tp1_price = round(price * (1 + TP1_PCT), 4)
    tp2_price = round(price * (1 + TP2_PCT), 4)
    sl_price  = round(price * (1 - SL_PCT), 4)
    xrp_amount = round(trade_usd / price, 2)

    buy_order = place_limit_buy(trade_usd, price)
    if not buy_order:
        return False

    tp1_order = place_limit_sell(round(xrp_amount * 0.5, 2), tp1_price, label="TP1")
    tp2_order = place_limit_sell(round(xrp_amount * 0.5, 2), tp2_price, label="TP2")
    sl_order  = place_limit_sell(xrp_amount, sl_price, label="SL")

    _pos = Position(
        entry_price=price,
        xrp_amount=xrp_amount,
        xrp_remaining=xrp_amount,
        usd_spent=trade_usd,
        buy_order_id=buy_order["id"],
        buy_placed_at=time.time(),
        tp1_order_id=tp1_order["id"] if tp1_order else "",
        tp2_order_id=tp2_order["id"] if tp2_order else "",
        sl_order_id=sl_order["id"] if sl_order else "",
        is_open=True,
    )

    notifier.notify_buy(price, xrp_amount, trade_usd, tp1_price, tp2_price, sl_price)
    logger.info(f"Position opened: {xrp_amount} XRP @ ${price:.4f}")
    return True


def check_buy_timeout() -> bool:
    """Cancel buy order if unfilled after ORDER_TIMEOUT_H hours. Returns True if cancelled."""
    global _pos
    if not _pos.is_open:
        return False

    elapsed_h = (time.time() - _pos.buy_placed_at) / 3600
    if elapsed_h < ORDER_TIMEOUT_H:
        return False

    status = get_order_status(_pos.buy_order_id)
    if status == "open":
        cancel_order(_pos.buy_order_id)
        cancel_order(_pos.tp1_order_id)
        cancel_order(_pos.tp2_order_id)
        cancel_order(_pos.sl_order_id)
        notifier.notify_order_cancelled(f"Unfilled after {ORDER_TIMEOUT_H}h. Conditions may have changed.")
        _pos = Position()
        logger.info("Buy order timed out and cancelled.")
        return True
    return False


def _calc_net_profit(entry: float, exit_price: float, xrp_qty: float) -> tuple[float, float]:
    """Return (net_profit_usd, fee_usd)."""
    gross = (exit_price - entry) * xrp_qty
    fee = (entry * xrp_qty * MAKER_FEE) + (exit_price * xrp_qty * MAKER_FEE)
    return gross - fee, fee


def monitor_position(risk_manager) -> str | None:
    """
    Check TP1/TP2/SL order statuses and handle accordingly.
    Returns 'TP1', 'TP2', 'SL', or None.
    """
    global _pos
    if not _pos.is_open:
        return None

    current_price = 0.0

    # ── Check TP1 ─────────────────────────────────────────────────────────────
    if not _pos.tp1_hit:
        tp1_status = get_order_status(_pos.tp1_order_id)
        if tp1_status == "closed" or (PAPER_TRADE and _should_simulate_tp(_pos, "TP1")):
            tp1_price = _pos.entry_price * (1 + TP1_PCT)
            half_xrp  = round(_pos.xrp_amount * 0.5, 2)
            net, fee  = _calc_net_profit(_pos.entry_price, tp1_price, half_xrp)

            _pos.tp1_hit = True
            _pos.xrp_remaining = round(_pos.xrp_amount * 0.5, 2)
            _pos.total_profit += net

            # Move SL to breakeven
            breakeven = round(_pos.entry_price * (1 + BREAKEVEN_BUFFER), 4)
            cancel_order(_pos.sl_order_id)
            new_sl = place_limit_sell(_pos.xrp_remaining, breakeven, label="SL_BE")
            _pos.sl_order_id = new_sl["id"] if new_sl else ""

            log_trade("sell", _pos.entry_price, tp1_price, half_xrp, fee, net, "TP1", PAPER_TRADE)
            notifier.notify_tp1(tp1_price, net, _pos.xrp_remaining, breakeven)
            risk_manager.record_trade(net)
            logger.info(f"TP1 hit @ ${tp1_price:.4f} | profit=${net:.4f}")
            return "TP1"

    # ── Check TP2 ─────────────────────────────────────────────────────────────
    tp2_status = get_order_status(_pos.tp2_order_id)
    if tp2_status == "closed" or (PAPER_TRADE and _should_simulate_tp(_pos, "TP2")):
        tp2_price = _pos.entry_price * (1 + TP2_PCT)
        net, fee  = _calc_net_profit(_pos.entry_price, tp2_price, _pos.xrp_remaining)

        _pos.total_profit += net
        total = _pos.total_profit

        log_trade("sell", _pos.entry_price, tp2_price, _pos.xrp_remaining, fee, net, "TP2", PAPER_TRADE)
        notifier.notify_tp2(tp2_price, total)
        risk_manager.record_trade(net)
        logger.info(f"TP2 hit @ ${tp2_price:.4f} | total profit=${total:.4f}")
        _pos = Position()
        return "TP2"

    # ── Check SL ──────────────────────────────────────────────────────────────
    sl_status = get_order_status(_pos.sl_order_id)
    if sl_status == "closed" or (PAPER_TRADE and _should_simulate_sl(_pos)):
        sl_price = _pos.entry_price * (1 - SL_PCT)
        net, fee = _calc_net_profit(_pos.entry_price, sl_price, _pos.xrp_remaining)

        log_trade("sell", _pos.entry_price, sl_price, _pos.xrp_remaining, fee, net, "SL", PAPER_TRADE)
        notifier.notify_stop_loss(sl_price, net)
        risk_manager.record_trade(net)
        logger.info(f"SL hit @ ${sl_price:.4f} | loss=${net:.4f}")
        _pos = Position()
        return "SL"

    return None


def close_all(reason: str = "MANUAL") -> None:
    """Emergency close: cancel all orders."""
    global _pos
    if not _pos.is_open:
        return
    cancel_order(_pos.buy_order_id)
    cancel_order(_pos.tp1_order_id)
    cancel_order(_pos.tp2_order_id)
    cancel_order(_pos.sl_order_id)
    logger.warning(f"All orders cancelled ({reason}).")
    _pos = Position()


def recover_existing_position() -> bool:
    """
    On bot restart: check Kraken for existing open orders and reconstruct state.
    Returns True if an existing position was found and adopted.
    """
    from kraken_client import fetch_open_orders
    orders = fetch_open_orders()
    buy_orders  = [o for o in orders if o["side"] == "buy"]
    sell_orders = [o for o in orders if o["side"] == "sell"]

    if not buy_orders and not sell_orders:
        return False

    logger.warning(f"Found {len(orders)} open orders on startup. Adopting existing position.")
    notifier.notify_error(
        f"Bot restarted with {len(orders)} open orders.\n"
        f"Monitoring existing position. Check Kraken for details."
    )
    # Mark position as open so the main loop monitors it
    global _pos
    _pos.is_open = True
    _pos.buy_placed_at = time.time()
    for o in buy_orders:
        _pos.buy_order_id = o["id"]
        _pos.entry_price = o.get("price", 0.0)
        _pos.xrp_amount = o.get("amount", 0.0)
        _pos.xrp_remaining = _pos.xrp_amount
    for o in sell_orders:
        if not _pos.tp1_order_id:
            _pos.tp1_order_id = o["id"]
        elif not _pos.tp2_order_id:
            _pos.tp2_order_id = o["id"]
        else:
            _pos.sl_order_id = o["id"]
    return True


# ── Paper trade simulation helpers ───────────────────────────────────────────

def _should_simulate_tp(pos: Position, level: str) -> bool:
    """Simulate TP trigger in paper trade after 30 minutes for testing."""
    if not PAPER_TRADE:
        return False
    elapsed_min = (time.time() - pos.buy_placed_at) / 60
    if level == "TP1":
        return elapsed_min >= 30 and not pos.tp1_hit
    if level == "TP2":
        return elapsed_min >= 60 and pos.tp1_hit
    return False


def _should_simulate_sl(pos: Position) -> bool:
    """In paper trade, never simulate SL unless explicitly set."""
    return False
