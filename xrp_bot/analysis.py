import pandas as pd
import pandas_ta as ta
from loguru import logger
from kraken_client import fetch_ohlcv, fetch_ticker
from config import (
    RSI_PERIOD, RSI_OVERSOLD,
    EMA_FAST, EMA_SLOW,
    SUPPORT_LOOKBACK, SUPPORT_TOLERANCE,
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


def has_entry_signal(data: dict) -> bool:
    """
    Entry conditions (all must be true):
    1. RSI(1H) < RSI_OVERSOLD (40)
    2. Price within SUPPORT_TOLERANCE (0.5%) of support level
    3. EMA_FAST(4H) > EMA_SLOW(4H) — uptrend
    """
    if not data:
        return False

    price   = data["price"]
    support = data["support"]
    rsi     = data["rsi"]

    near_support = abs(price - support) / support <= SUPPORT_TOLERANCE if support else False
    oversold     = rsi < RSI_OVERSOLD
    uptrend      = is_uptrend(data)

    logger.info(
        f"Signal check | price={price:.4f} support={support:.4f} "
        f"near={near_support} RSI={rsi:.1f} oversold={oversold} uptrend={uptrend}"
    )

    return near_support and oversold and uptrend
