"""
Backtest — Stage 1 of the test plan.
Downloads 90 days of XRP/USD 1H candles from Kraken (no API key needed)
and simulates the trading strategy to validate its effectiveness.

Usage:
    python backtest.py
"""

import ccxt
import pandas as pd
import pandas_ta as ta
from datetime import datetime, timezone

# ── Parameters (mirrors config.py) ───────────────────────────────────────────
SYMBOL           = "XRP/USD"
RSI_PERIOD       = 14
RSI_OVERSOLD     = 45
EMA_FAST         = 20
EMA_SLOW         = 50
SUPPORT_LOOKBACK = 20
SUPPORT_TOLERANCE= 0.010
TP1_PCT          = 0.0082
TP2_PCT          = 0.0132
SL_PCT           = 0.0080
MAKER_FEE        = 0.0016
TRADE_RATIO      = 0.80
STARTING_BALANCE = 1700.0
DAYS             = 90


def fetch_history() -> pd.DataFrame:
    # 4H candles: 720 bars = ~120 days, covers the required 90-day window
    print(f"Downloading 720 x 4H candles of {SYMBOL} from Kraken (~120 days)...")
    exchange = ccxt.kraken({"enableRateLimit": True})
    ohlcv = exchange.fetch_ohlcv(SYMBOL, timeframe="4h", limit=720)
    df = pd.DataFrame(ohlcv, columns=["ts", "open", "high", "low", "close", "volume"])
    df["ts"] = pd.to_datetime(df["ts"], unit="ms", utc=True)
    df.set_index("ts", inplace=True)
    print(f"  Fetched {len(df)} x 4H candles ({df.index[0].date()} → {df.index[-1].date()})")
    return df


def run_backtest(df: pd.DataFrame) -> dict:
    # Compute indicators
    df["rsi"]       = ta.rsi(df["close"], length=RSI_PERIOD)
    df["ema_fast"]  = ta.ema(df["close"], length=EMA_FAST)
    df["ema_slow"]  = ta.ema(df["close"], length=EMA_SLOW)
    df.dropna(inplace=True)

    balance       = STARTING_BALANCE
    peak_balance  = STARTING_BALANCE
    trades        = []
    in_position   = False
    entry_price   = 0.0
    xrp_amount    = 0.0
    trade_usd     = 0.0
    tp1_hit       = False
    consec_losses = 0
    daily_pnl     = 0.0
    current_day   = None
    max_drawdown  = 0.0

    for i in range(SUPPORT_LOOKBACK, len(df)):
        row = df.iloc[i]
        day = df.index[i].date()

        # Daily reset
        if day != current_day:
            daily_pnl = 0.0
            current_day = day

        price = row["close"]
        if balance > peak_balance:
            peak_balance = balance
        dd = (peak_balance - balance) / peak_balance if peak_balance else 0
        if dd > max_drawdown:
            max_drawdown = dd

        # ── Manage open position ─────────────────────────────────────────────
        if in_position:
            tp1_price = entry_price * (1 + TP1_PCT)
            tp2_price = entry_price * (1 + TP2_PCT)
            sl_price  = entry_price * (1 - SL_PCT)

            if not tp1_hit and row["high"] >= tp1_price:
                # TP1: sell 50%
                half = xrp_amount * 0.5
                gross = (tp1_price - entry_price) * half
                fee   = entry_price * half * MAKER_FEE + tp1_price * half * MAKER_FEE
                net   = gross - fee
                balance += net
                daily_pnl += net
                tp1_hit = True
                trades.append({"type": "TP1", "net": net, "entry": entry_price, "exit": tp1_price})
                consec_losses = 0

            if tp1_hit and row["high"] >= tp2_price:
                # TP2: sell remaining 50%
                half  = xrp_amount * 0.5
                gross = (tp2_price - entry_price) * half
                fee   = entry_price * half * MAKER_FEE + tp2_price * half * MAKER_FEE
                net   = gross - fee
                balance += net
                daily_pnl += net
                trades.append({"type": "TP2", "net": net, "entry": entry_price, "exit": tp2_price})
                consec_losses = 0
                in_position = False
                tp1_hit = False

            elif row["low"] <= sl_price:
                # SL: exit full position
                qty   = xrp_amount if not tp1_hit else xrp_amount * 0.5
                gross = (sl_price - entry_price) * qty
                fee   = entry_price * qty * MAKER_FEE + sl_price * qty * MAKER_FEE
                net   = gross - fee
                balance += net
                daily_pnl += net
                trades.append({"type": "SL", "net": net, "entry": entry_price, "exit": sl_price})
                consec_losses += 1
                in_position = False
                tp1_hit = False

            continue

        # ── Check entry conditions ───────────────────────────────────────────
        if daily_pnl / balance >= 0.01:   # daily target reached
            continue
        if daily_pnl / balance <= -0.02:  # daily loss limit
            continue
        if consec_losses >= 2:
            consec_losses = 0  # simplified: reset after skipping
            continue

        support = row["ema_slow"]   # dynamic support: EMA50
        near_support = abs(price - support) / support <= SUPPORT_TOLERANCE if support else False
        oversold     = row["rsi"] < RSI_OVERSOLD
        uptrend      = row["ema_fast"] > row["ema_slow"]

        if near_support and oversold and uptrend:
            trade_usd   = balance * TRADE_RATIO
            xrp_amount  = trade_usd / price
            entry_price = price
            in_position = True
            tp1_hit     = False

    # ── Results ───────────────────────────────────────────────────────────────
    wins   = [t for t in trades if t["net"] > 0]
    losses = [t for t in trades if t["net"] <= 0]
    total_net = sum(t["net"] for t in trades)
    days_ran  = max((df.index[-1] - df.index[0]).days, 1)
    avg_daily = total_net / days_ran

    return {
        "total_trades":    len(trades),
        "wins":            len(wins),
        "losses":          len(losses),
        "win_rate":        len(wins) / len(trades) * 100 if trades else 0,
        "total_net_usd":   total_net,
        "final_balance":   STARTING_BALANCE + total_net,
        "avg_daily_pnl":   avg_daily,
        "avg_daily_pct":   avg_daily / STARTING_BALANCE * 100,
        "max_drawdown_pct":max_drawdown * 100,
        "days":            days_ran,
    }


def print_results(r: dict) -> None:
    win_rate = r["win_rate"]
    avg_pct  = r["avg_daily_pct"]
    drawdown = r["max_drawdown_pct"]

    pass_winrate  = "✅" if win_rate >= 55 else "❌"
    pass_daily    = "✅" if avg_pct >= 0.3 else "❌"
    pass_drawdown = "✅" if drawdown <= 30 else "❌"
    pass_profit   = "✅" if r["total_net_usd"] > 0 else "❌"

    print("\n" + "═" * 46)
    print("  BACKTEST RESULTS — XRP/USD (90 days)")
    print("═" * 46)
    print(f"  Period:          {r['days']} days")
    print(f"  Starting bal:    ${STARTING_BALANCE:,.2f}")
    print(f"  Final balance:   ${r['final_balance']:,.2f}")
    print(f"  Total net P&L:   ${r['total_net_usd']:+,.2f}")
    print(f"  Total trades:    {r['total_trades']}  (W:{r['wins']} L:{r['losses']})")
    print("─" * 46)
    print(f"  Win rate:        {win_rate:.1f}%   {pass_winrate}  (need ≥55%)")
    print(f"  Avg daily P&L:   {avg_pct:+.2f}%  {pass_daily}  (need ≥0.3%)")
    print(f"  Max drawdown:    {drawdown:.1f}%  {pass_drawdown}  (need ≤30%)")
    print(f"  Total profit +?: {'Yes' if r['total_net_usd']>0 else 'No'}        {pass_profit}")
    print("═" * 46)

    all_pass = all(s == "✅" for s in [pass_winrate, pass_daily, pass_drawdown, pass_profit])
    if all_pass:
        print("  RESULT: ✅ PASS — Proceed to Stage 2 (Paper Trade)")
    else:
        print("  RESULT: ❌ FAIL — Review strategy before proceeding")
    print("═" * 46 + "\n")


if __name__ == "__main__":
    df = fetch_history()
    results = run_backtest(df)
    print_results(results)
