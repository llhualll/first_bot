import pandas as pd
import pandas_ta as ta
from loguru import logger
from kraken_client import fetch_ohlcv, fetch_ticker
from config import (
    RSI_PERIOD, EMA_FAST, EMA_SLOW, SUPPORT_LOOKBACK,
    UP_RSI_ENTRY, UP_SUPPORT_TOLERANCE, UP_TP1_PCT, UP_TP2_PCT, UP_SL_PCT, UP_TRADE_RATIO,
    UP_MIN_CROSS_CANDLES, UP_MACRO_SMA_LENGTH,
    DN_RSI_ENTRY, DN_TP1_PCT, DN_TP2_PCT, DN_SL_PCT, DN_TRADE_RATIO, DN_MAX_EMA_GAP,
    PANIC_ROC_CANDLES,
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
    # Need ≥ UP_MACRO_SMA_LENGTH candles for SMA150 warmup, plus buffer
    candles_4h = fetch_ohlcv("4h", limit=max(200, UP_MACRO_SMA_LENGTH + 20))
    candles_1h = fetch_ohlcv("1h", limit=60)

    if not candles_4h or not candles_1h:
        logger.warning("Failed to fetch candles.")
        return {}

    df_4h = _to_df(candles_4h)
    df_1h = _to_df(candles_1h)

    # EMA on 4H
    df_4h["ema_fast"] = ta.ema(df_4h["close"], length=EMA_FAST)
    df_4h["ema_slow"] = ta.ema(df_4h["close"], length=EMA_SLOW)

    # Macro regime SMA on 4H (≈25-day trend)
    df_4h["sma_macro"] = df_4h["close"].rolling(UP_MACRO_SMA_LENGTH).mean()

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
    sma_macro_val = df_4h["sma_macro"].iloc[-1]
    sma_macro = float(sma_macro_val) if not pd.isna(sma_macro_val) else None

    # 1H price change for flash crash detection
    price_1h_ago = float(df_1h["close"].iloc[-2]) if len(df_1h) >= 2 else current_price
    change_1h    = (current_price - price_1h_ago) / price_1h_ago if price_1h_ago else 0.0

    # Panic index: 40h ROC and EMA divergence (consumed by risk_manager)
    roc_40h = float(df_4h["close"].pct_change(periods=PANIC_ROC_CANDLES).iloc[-1]) \
              if len(df_4h) > PANIC_ROC_CANDLES else 0.0
    ema_gap_pct = (ema_fast - ema_slow) / ema_slow if ema_slow else 0.0

    # Count consecutive candles where EMA20 > EMA50 (uptrend confirmation)
    cross_candles = 0
    for i in range(len(df_4h) - 1, -1, -1):
        ef = df_4h["ema_fast"].iloc[i]
        es = df_4h["ema_slow"].iloc[i]
        if pd.isna(ef) or pd.isna(es):
            break
        if ef > es:
            cross_candles += 1
        else:
            break

    return {
        "price":         current_price,
        "ema_fast":      ema_fast,
        "ema_slow":      ema_slow,
        "rsi":           rsi,
        "support":       support,
        "change_1h":     change_1h,
        "roc_40h":       roc_40h,
        "ema_gap_pct":   ema_gap_pct,
        "cross_candles": cross_candles,   # consecutive candles EMA20 > EMA50
        "sma_macro":     sma_macro,       # 4H SMA150 ≈ 25-day avg (macro regime)
    }


def is_uptrend(data: dict) -> bool:
    return data.get("ema_fast", 0) > data.get("ema_slow", 0)


def is_flash_crash(data: dict, threshold: float) -> bool:
    return data.get("change_1h", 0) <= -threshold


# ── Downtrend cooldown state ──────────────────────────────────────────────────
# Set to True by strategy.py after a downtrend SL hit.
# Skips the very next downtrend signal to avoid chasing a falling knife.
# NOTE: uptrend cooldown was tested and reverted — V-shaped recoveries in
# uptrends mean the signal right after a SL is often a winning entry.
_dn_cooldown: bool = False


def set_downtrend_cooldown(value: bool) -> None:
    global _dn_cooldown
    _dn_cooldown = value
    if value:
        logger.info("[DOWNTREND] Cooldown active — skipping next downtrend signal after SL.")


def set_uptrend_cooldown(value: bool) -> None:
    """No-op: uptrend cooldown was removed after backtesting showed it
    skips winning V-shaped recovery entries."""
    pass


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
        _dn_cooldown = False   # reset downtrend cooldown when trend flips back up

        near = abs(price - support) / support <= UP_SUPPORT_TOLERANCE if support else False
        cross_candles = data.get("cross_candles", UP_MIN_CROSS_CANDLES)
        confirmed = cross_candles >= UP_MIN_CROSS_CANDLES
        sma_macro = data.get("sma_macro")
        macro_bull = sma_macro is not None and price > sma_macro
        signal = near and rsi < UP_RSI_ENTRY and confirmed and macro_bull
        sma_str = f"{sma_macro:.4f}" if sma_macro is not None else "n/a"
        logger.info(
            f"[UPTREND] Signal check | price={price:.4f} support={support:.4f} "
            f"near={near} RSI={rsi:.1f} threshold={UP_RSI_ENTRY} "
            f"cross_candles={cross_candles} min={UP_MIN_CROSS_CANDLES} "
            f"SMA150={sma_str} macro_bull={macro_bull}"
            + (" [crossover unconfirmed — skip]" if not confirmed else "")
            + (" [below SMA150 — bear regime, skip]" if not macro_bull else "")
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

        ema_gap_pct = data.get("ema_gap_pct", 0.0)
        too_deep = ema_gap_pct < DN_MAX_EMA_GAP
        signal = rsi < DN_RSI_ENTRY and not too_deep
        logger.info(
            f"[DOWNTREND] Signal check | price={price:.4f} "
            f"RSI={rsi:.1f} threshold={DN_RSI_ENTRY} "
            f"EMA_gap={ema_gap_pct*100:.2f}% max={DN_MAX_EMA_GAP*100:.0f}%"
            + (" [FREEFALL — skip]" if too_deep else "")
        )
        if signal:
            return {
                "mode": "DOWNTREND",
                "tp1_pct": DN_TP1_PCT, "tp2_pct": DN_TP2_PCT,
                "sl_pct": DN_SL_PCT, "trade_ratio": DN_TRADE_RATIO,
            }

    return None
