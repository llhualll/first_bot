import pandas as pd
import pandas_ta as ta
from loguru import logger
from kraken_client import fetch_ohlcv, fetch_ticker
from config import (
    RSI_PERIOD, EMA_FAST, EMA_SLOW, SUPPORT_LOOKBACK,
    UP_RSI_ENTRY, UP_SUPPORT_TOLERANCE, UP_TP1_PCT, UP_TP2_PCT, UP_SL_PCT, UP_TRADE_RATIO,
    DN_RSI_ENTRY, DN_TP1_PCT, DN_TP2_PCT, DN_SL_PCT, DN_TRADE_RATIO,
)


def _to_df(ohlcv: list) -> pd.DataFrame:
    df = pd.DataFrame(ohlcv, columns=["timestamp", "open", "high", "low", "close", "volume"])
    df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms")
    df.set_index("timestamp", inplace=True)
    return df


def get_market_data() -> dict:
    """
    Fetch 4H and 1H candles, compute indicators.
    Returns dict with all values needed for entry decision.
    """
    candles_4h = fetch_ohlcv("4h", limit=60)
    candles_1h = fetch_ohlcv("1h", limit=60)

    if not candles_4h or not candles_1h:
        logger.warning("Failed to fetch candles.")
        return {}

    df_4h = _to_df(candles_4h)
    df_1h = _to_df(candles_1h)

    # EMA on 4H
    df_4h["ema_fast"] = ta.ema(df_4h["close"], length=EMA_FAST)
    df_4h["ema_slow"] = ta.ema(df_4h["close"], length=EMA_SLOW)

    # RSI on 1H
    df_1h["rsi"] = ta.rsi(df_1h["close"], length=RSI_PERIOD)

    # Dynamic support: EMA50 on 4H (more robust than rolling lowest low)
    support = float(df_4h["ema_slow"].iloc[-1])

    # Also compute RSI on 4H (less noise than 1H)
    df_4h["rsi"] = ta.rsi(df_4h["close"], length=RSI_PERIOD)

    ticker = fetch_ticker()
    current_price = ticker.get("last", 0.0)

    ema_fast  = float(df_4h["ema_fast"].iloc[-1])
    ema_slow  = float(df_4h["ema_slow"].iloc[-1])
    rsi       = float(df_4h["rsi"].iloc[-1])   # 4H RSI

    # 1H price change for flash crash detection
    price_1h_ago = float(df_1h["close"].iloc[-2]) if len(df_1h) >= 2 else current_price
    change_1h    = (current_price - price_1h_ago) / price_1h_ago if price_1h_ago else 0.0

    return {
        "price":        current_price,
        "ema_fast":     ema_fast,
        "ema_slow":     ema_slow,
        "rsi":          rsi,
        "support":      support,
        "change_1h":    change_1h,
    }


def is_uptrend(data: dict) -> bool:
    return data.get("ema_fast", 0) > data.get("ema_slow", 0)


def is_flash_crash(data: dict, threshold: float) -> bool:
    return data.get("change_1h", 0) <= -threshold


# ── Downtrend cooldown state ──────────────────────────────────────────────────
# Set to True by strategy.py after a downtrend SL hit.
# Skips the very next downtrend signal to avoid chasing a falling knife.
_dn_cooldown: bool = False


def set_downtrend_cooldown(value: bool) -> None:
    global _dn_cooldown
    _dn_cooldown = value
    if value:
        logger.info("[DOWNTREND] Cooldown active — skipping next downtrend signal after SL.")


def has_entry_signal(data: dict) -> dict | None:
    """
    Auto-detects trend and applies the matching strategy.

    Uptrend   (EMA20 > EMA50): RSI < 65 AND price within 5% of EMA50
    Downtrend (EMA20 < EMA50): RSI < 32, skip one signal after SL (cooldown)

    Returns a strategy dict on signal, or None if no signal.
    """
    global _dn_cooldown

    if not data:
        return None

    price   = data["price"]
    support = data["support"]
    rsi     = data["rsi"]
    uptrend = is_uptrend(data)

    if uptrend:
        near = abs(price - support) / support <= UP_SUPPORT_TOLERANCE if support else False
        signal = near and rsi < UP_RSI_ENTRY
        logger.info(
            f"[UPTREND] Signal check | price={price:.4f} support={support:.4f} "
            f"near={near} RSI={rsi:.1f} threshold={UP_RSI_ENTRY}"
        )
        if signal:
            return {
                "mode": "UPTREND",
                "tp1_pct": UP_TP1_PCT, "tp2_pct": UP_TP2_PCT,
                "sl_pct": UP_SL_PCT, "trade_ratio": UP_TRADE_RATIO,
            }
    else:
        if _dn_cooldown:
            logger.info(
                f"[DOWNTREND] Cooldown skip | RSI={rsi:.1f} price={price:.4f}"
            )
            _dn_cooldown = False   # consume the cooldown
            return None

        signal = rsi < DN_RSI_ENTRY
        logger.info(
            f"[DOWNTREND] Signal check | price={price:.4f} "
            f"RSI={rsi:.1f} threshold={DN_RSI_ENTRY}"
        )
        if signal:
            return {
                "mode": "DOWNTREND",
                "tp1_pct": DN_TP1_PCT, "tp2_pct": DN_TP2_PCT,
                "sl_pct": DN_SL_PCT, "trade_ratio": DN_TRADE_RATIO,
            }

    return None
