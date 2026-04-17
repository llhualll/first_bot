#!/usr/bin/env python3
"""
Run the dual-strategy backtest for 1..14 weeks and print a compact summary
table with the date range and P&L for each window.
"""

import ccxt
import pandas as pd
import pandas_ta as ta
from datetime import datetime, timezone, timedelta

from backtest import (
    run_backtest,
    INITIAL_BALANCE,
    EMA_FAST, EMA_SLOW, RSI_PERIOD, PANIC_ROC_CANDLES, UP_MACRO_SMA_LENGTH,
)


def fetch_ohlcv_chunked(days: int) -> pd.DataFrame:
    exchange = ccxt.kraken({"enableRateLimit": True})
    end_ms   = int(datetime.now(timezone.utc).timestamp() * 1000)
    start_ms = int((datetime.now(timezone.utc) - timedelta(days=days)).timestamp() * 1000)

    all_rows = []
    since    = start_ms
    step_ms  = 500 * 4 * 60 * 60 * 1000   # 500 × 4h
    while since < end_ms:
        raw = exchange.fetch_ohlcv("XRP/USD", "4h", since=since, limit=500)
        if not raw:
            break
        all_rows.extend(raw)
        last_ts = raw[-1][0]
        if last_ts <= since:
            break
        since = last_ts + 1
        if len(raw) < 500:
            break

    df = pd.DataFrame(all_rows, columns=["ts", "open", "high", "low", "close", "volume"])
    df.drop_duplicates(subset="ts", inplace=True)
    df["ts"] = pd.to_datetime(df["ts"], unit="ms", utc=True)
    df.set_index("ts", inplace=True)
    df.sort_index(inplace=True)
    return df


def enrich(df: pd.DataFrame) -> pd.DataFrame:
    df["ema_fast"]    = ta.ema(df["close"], length=EMA_FAST)
    df["ema_slow"]    = ta.ema(df["close"], length=EMA_SLOW)
    df["rsi"]         = ta.rsi(df["close"], length=RSI_PERIOD)
    df["roc"]         = df["close"].pct_change(periods=PANIC_ROC_CANDLES)
    df["ema_gap_pct"] = (df["ema_fast"] - df["ema_slow"]) / df["ema_slow"]
    df["sma_macro"]   = df["close"].rolling(UP_MACRO_SMA_LENGTH).mean()

    uptrend_mask = df["ema_fast"] > df["ema_slow"]
    since_cross, count = [], 0
    for up in uptrend_mask:
        count = count + 1 if up else 0
        since_cross.append(count)
    df["since_cross"] = since_cross
    return df


def main():
    max_weeks  = 14
    test_days  = max_weeks * 7
    warmup     = 28
    total_days = test_days + warmup

    print(f"Fetching XRP/USD 4H candles ({total_days} days) in chunks...")
    df = fetch_ohlcv_chunked(total_days)
    print(f"  fetched {len(df)} candles "
          f"({df.index[0].strftime('%Y-%m-%d')} → {df.index[-1].strftime('%Y-%m-%d')})\n")
    df = enrich(df)

    print("=" * 90)
    print(f"  XRP DUAL-STRATEGY BACKTEST — 1 to {max_weeks} WEEKS  (start ${INITIAL_BALANCE:.0f})")
    print("=" * 90)
    print(f"  {'Wk':<4} {'From (UTC)':<12} {'To (UTC)':<12} "
          f"{'Trades':<7} {'W/L':<7} {'WR%':<5} {'End $':<10} {'Net P&L':>10} {'%':>8}")
    print("  " + "─" * 86)

    for weeks in range(1, max_weeks + 1):
        days   = weeks * 7
        cutoff = datetime.now(timezone.utc) - timedelta(days=days)
        window = df[df.index >= cutoff]
        if window.empty:
            print(f"  {weeks:<4} (no data in window)")
            continue

        from_d = window.index[0].strftime("%Y-%m-%d")
        to_d   = window.index[-1].strftime("%Y-%m-%d")

        trades = run_backtest(df, days)
        closed = [t for t in trades if t.exit_reason != "OPEN"]
        wins   = [t for t in closed if t.net_pnl > 0]
        losses = [t for t in closed if t.net_pnl <= 0]
        pnl    = sum(t.net_pnl for t in trades)
        pct    = pnl / INITIAL_BALANCE * 100
        wr     = len(wins) / len(closed) * 100 if closed else 0
        end_b  = INITIAL_BALANCE + pnl

        print(f"  {weeks:<4} {from_d:<12} {to_d:<12} "
              f"{len(trades):<7} {len(wins)}/{len(losses):<5} "
              f"{wr:<5.0f} ${end_b:<9.2f} ${pnl:>+9.2f} {pct:>+7.2f}%")

    print("=" * 90)


if __name__ == "__main__":
    main()
