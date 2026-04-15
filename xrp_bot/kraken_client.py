import ccxt
from loguru import logger
from config import KRAKEN_API_KEY, KRAKEN_SECRET, PAPER_TRADE, SYMBOL


def get_exchange() -> ccxt.kraken:
    exchange = ccxt.kraken({
        "apiKey": KRAKEN_API_KEY,
        "secret": KRAKEN_SECRET,
        "enableRateLimit": True,
        "options": {"defaultType": "spot"},
    })
    return exchange


_exchange = get_exchange()


def fetch_ohlcv(timeframe: str, limit: int = 100) -> list:
    """Fetch OHLCV candles. timeframe: '1h', '4h', etc."""
    try:
        return _exchange.fetch_ohlcv(SYMBOL, timeframe=timeframe, limit=limit)
    except Exception as e:
        logger.error(f"fetch_ohlcv({timeframe}) failed: {e}")
        return []


def fetch_ticker() -> dict:
    """Return current ticker for SYMBOL."""
    try:
        return _exchange.fetch_ticker(SYMBOL)
    except Exception as e:
        logger.error(f"fetch_ticker failed: {e}")
        return {}


def fetch_balance() -> dict:
    """Return free USD cash balance, free XRP balance, and current price."""
    try:
        balance = _exchange.fetch_balance()
        xrp_free = balance.get("XRP", {}).get("free", 0.0)
        # Kraken reports USD as "USD" or "ZUSD" depending on the account
        usd_free = balance.get("USD", {}).get("free", 0.0)
        if usd_free == 0.0:
            usd_free = balance.get("ZUSD", {}).get("free", 0.0)
        ticker = fetch_ticker()
        price = ticker.get("last", 0.0)
        return {"xrp": xrp_free, "usd": usd_free, "price": price}
    except Exception as e:
        logger.error(f"fetch_balance failed: {e}")
        return {"xrp": 0.0, "usd": 0.0, "price": 0.0}


def fetch_open_orders() -> list:
    """Return all open orders for SYMBOL."""
    try:
        return _exchange.fetch_open_orders(SYMBOL)
    except Exception as e:
        logger.error(f"fetch_open_orders failed: {e}")
        return []


def fetch_open_positions() -> list:
    """Return open orders that are buy-side (acting as position proxy for spot)."""
    orders = fetch_open_orders()
    return [o for o in orders if o.get("side") == "buy"]


def place_limit_buy(amount_usd: float, price: float) -> dict | None:
    """Place a limit buy order. amount_usd is USD value to spend."""
    ticker = fetch_ticker()
    current_price = ticker.get("last", price)
    xrp_amount = round(amount_usd / current_price, 2)

    if PAPER_TRADE:
        logger.info(f"[PAPER] LIMIT BUY {xrp_amount} XRP @ ${price:.4f}")
        return {
            "id": "paper_buy",
            "side": "buy",
            "price": price,
            "amount": xrp_amount,
            "status": "open",
            "timestamp": _exchange.milliseconds(),
        }
    try:
        order = _exchange.create_limit_order(SYMBOL, "buy", xrp_amount, price)
        logger.info(f"Limit buy placed: {xrp_amount} XRP @ ${price:.4f} | ID: {order['id']}")
        return order
    except Exception as e:
        logger.error(f"place_limit_buy failed: {e}")
        return None


def place_limit_sell(amount_xrp: float, price: float, label: str = "") -> dict | None:
    """Place a limit sell order."""
    if PAPER_TRADE:
        logger.info(f"[PAPER] LIMIT SELL {amount_xrp} XRP @ ${price:.4f} ({label})")
        return {
            "id": f"paper_sell_{label}",
            "side": "sell",
            "price": price,
            "amount": amount_xrp,
            "status": "open",
            "timestamp": _exchange.milliseconds(),
        }
    try:
        order = _exchange.create_limit_order(SYMBOL, "sell", amount_xrp, price)
        logger.info(f"Limit sell placed ({label}): {amount_xrp} XRP @ ${price:.4f} | ID: {order['id']}")
        return order
    except Exception as e:
        logger.error(f"place_limit_sell({label}) failed: {e}")
        return None


def cancel_order(order_id: str) -> bool:
    """Cancel an order by ID."""
    if PAPER_TRADE or order_id.startswith("paper_"):
        logger.info(f"[PAPER] Cancel order {order_id}")
        return True
    try:
        _exchange.cancel_order(order_id, SYMBOL)
        logger.info(f"Order cancelled: {order_id}")
        return True
    except Exception as e:
        logger.error(f"cancel_order({order_id}) failed: {e}")
        return False


def cancel_all_orders() -> None:
    """Cancel all open orders for SYMBOL."""
    orders = fetch_open_orders()
    for order in orders:
        cancel_order(order["id"])
    logger.info(f"Cancelled {len(orders)} open orders.")


def get_order_status(order_id: str) -> str:
    """Return order status: 'open', 'closed', 'canceled'."""
    if PAPER_TRADE or order_id.startswith("paper_"):
        return "open"
    try:
        order = _exchange.fetch_order(order_id, SYMBOL)
        return order.get("status", "unknown")
    except Exception as e:
        logger.error(f"get_order_status({order_id}) failed: {e}")
        return "unknown"


def get_order(order_id: str) -> dict:
    """Fetch full order details."""
    if PAPER_TRADE or order_id.startswith("paper_"):
        return {}
    try:
        return _exchange.fetch_order(order_id, SYMBOL)
    except Exception as e:
        logger.error(f"get_order({order_id}) failed: {e}")
        return {}
