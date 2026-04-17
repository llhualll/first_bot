#!/usr/bin/env python3
"""
Dual-strategy backtest using real XRP/USD 4H data from Kraken.

Entry logic mirrors analysis.py exactly.
Trade simulation uses candle high/low to determine TP/SL hit order.

Usage:
  python backtest.py        → last 7 days
  python backtest.py 28     → last 28 days (4 weeks)
"""

import ccxt
import pandas as pd
import pandas_ta as ta
from datetime import datetime, timezone, timedelta
from dataclasses import dataclass

# ── Strategy params (mirrors config.py) ────────────────────────────────────
INITIAL_BALANCE      = 1500.0
MAKER_FEE            = 0.0016
BREAKEVEN_BUFFER     = 0.0003
MAX_CONSEC_LOSS      = 2
CONSEC_LOSS_PAUSE_H  = 24

UP_RSI_ENTRY         = 65
UP_SUPPORT_TOLERANCE = 0.05
UP_TP1_PCT           = 0.015
UP_TP2_PCT           = 0.025
UP_SL_PCT            = 0.010
UP_TRADE_RATIO       = 0.80

DN_RSI_ENTRY         = 32      # tightened (was 35)
DN_TP1_PCT           = 0.008
DN_TP2_PCT           = 0.013
DN_SL_PCT            = 0.006
DN_TRADE_RATIO       = 0.40

EMA_FAST   = 20
EMA_SLOW   = 50
RSI_PERIOD = 14


# ── Data fetching ───────────────────────────────────────────────────────────

def fetch_ohlcv(timeframe="4h", days=28) -> pd.DataFrame:
    exchange = ccxt.kraken({"enableRateLimit": True})
    since = int((datetime.now(timezone.utc) - timedelta(days=days)).timestamp() * 1000)
    raw = exchange.fetch_ohlcv("XRP/USD", timeframe=timeframe, since=since, limit=500)
    df = pd.DataFrame(raw, columns=["ts", "open", "high", "low", "close", "volume"])
    df["ts"] = pd.to_datetime(df["ts"], unit="ms", utc=True)
    df.set_index("ts", inplace=True)
    return df


# ── Trade model ─────────────────────────────────────────────────────────────

@dataclass
class Trade:
    mode: str
    entry_ts: datetime
    entry_price: float
    xrp_amount: float
    usd_spent: float
    tp1_price: float
    tp2_price: float
    sl_price: float
    tp1_pct: float
    tp2_pct: float
    sl_pct: float
    rsi_at_entry: float = 0.0
    # filled after simulation
    exit_ts: datetime = None
    exit_reason: str = ""
    net_pnl: float = 0.0
    tp1_hit: bool = False


def calc_net(entry: float, exit_price: float, qty: float) -> float:
    gross = (exit_price - entry) * qty
    fee   = entry * qty * MAKER_FEE + exit_price * qty * MAKER_FEE
    return gross - fee


def simulate_trade(trade: Trade, future: pd.DataFrame) -> Trade:
    """
    Walk forward candle-by-candle.
    Within a single candle, if both TP and SL levels are breached,
    we conservatively assume SL hit first (worst-case for us).
    """
    xrp_half  = trade.xrp_amount * 0.5
    xrp_rem   = trade.xrp_amount
    sl_price  = trade.sl_price
    tp1_hit   = False
    total_pnl = 0.0

    for ts, c in future.iterrows():
        if tp1_hit:
            be_price = trade.entry_price * (1 + BREAKEVEN_BUFFER)
            hit_tp2  = c["high"] >= trade.tp2_price
            hit_be   = c["low"]  <= be_price

            if hit_be and hit_tp2:
                # Both hit same candle: conservative → breakeven exit
                net = calc_net(trade.entry_price, be_price, xrp_rem)
                total_pnl += net
                trade.exit_reason = "TP1+BE"
                trade.exit_ts = ts
                break
            if hit_tp2:
                net = calc_net(trade.entry_price, trade.tp2_price, xrp_rem)
                total_pnl += net
                trade.exit_reason = "TP1+TP2"
                trade.exit_ts = ts
                break
            if hit_be:
                net = calc_net(trade.entry_price, be_price, xrp_rem)
                total_pnl += net
                trade.exit_reason = "TP1+BE"
                trade.exit_ts = ts
                break
        else:
            hit_sl  = c["low"]  <= sl_price
            hit_tp1 = c["high"] >= trade.tp1_price

            if hit_sl and hit_tp1:
                # Conservative: SL first
                net = calc_net(trade.entry_price, sl_price, xrp_rem)
                total_pnl += net
                trade.exit_reason = "SL"
                trade.exit_ts = ts
                break
            if hit_sl:
                net = calc_net(trade.entry_price, sl_price, xrp_rem)
                total_pnl += net
                trade.exit_reason = "SL"
                trade.exit_ts = ts
                break
            if hit_tp1:
                net = calc_net(trade.entry_price, trade.tp1_price, xrp_half)
                total_pnl += net
                xrp_rem  = xrp_half
                tp1_hit  = True
                sl_price = trade.entry_price * (1 + BREAKEVEN_BUFFER)
    else:
        trade.exit_reason = "OPEN"
        trade.exit_ts = future.index[-1] if len(future) else trade.entry_ts

    trade.net_pnl = total_pnl
    trade.tp1_hit = tp1_hit
    return trade


# ── Main backtest ───────────────────────────────────────────────────────────

def run_backtest(df, test_days: int) -> list:
    """Run the backtest on the last `test_days` of df. Returns trade list."""
    cutoff  = datetime.now(timezone.utc) - timedelta(days=test_days)
    test_df = df[df.index >= cutoff]

    balance      = INITIAL_BALANCE
    trades       = []
    skip_until   = None
    dn_cooldown  = False   # downtrend only; uptrend cooldown removed after testing
    consec_losses = 0
    pause_until  = None    # consecutive loss pause (24h after 2 SL)

    for ts, row in test_df.iterrows():
        if pd.isna(row["ema_fast"]) or pd.isna(row["ema_slow"]) or pd.isna(row["rsi"]):
            continue
        if skip_until is not None and ts <= skip_until:
            continue
        if pause_until is not None and ts <= pause_until:
            continue

        price   = row["close"]
        ema20   = row["ema_fast"]
        ema50   = row["ema_slow"]
        rsi     = row["rsi"]
        uptrend = ema20 > ema50

        signal = None
        if uptrend:
            dn_cooldown = False   # reset when trend flips back up
            near = abs(price - ema50) / ema50 <= UP_SUPPORT_TOLERANCE
            if near and rsi < UP_RSI_ENTRY:
                signal = dict(mode="UPTREND",
                              tp1_pct=UP_TP1_PCT, tp2_pct=UP_TP2_PCT,
                              sl_pct=UP_SL_PCT, trade_ratio=UP_TRADE_RATIO)
        else:
            if dn_cooldown:
                dn_cooldown = False   # consume, skip this signal
                continue
            if rsi < DN_RSI_ENTRY:
                signal = dict(mode="DOWNTREND",
                              tp1_pct=DN_TP1_PCT, tp2_pct=DN_TP2_PCT,
                              sl_pct=DN_SL_PCT, trade_ratio=DN_TRADE_RATIO)

        if not signal:
            continue

        trade_usd = round(balance * signal["trade_ratio"], 2)
        if trade_usd < 10:
            continue

        t = Trade(
            mode         = signal["mode"],
            entry_ts     = ts,
            entry_price  = price,
            xrp_amount   = trade_usd / price,
            usd_spent    = trade_usd,
            tp1_price    = price * (1 + signal["tp1_pct"]),
            tp2_price    = price * (1 + signal["tp2_pct"]),
            sl_price     = price * (1 - signal["sl_pct"]),
            tp1_pct      = signal["tp1_pct"],
            tp2_pct      = signal["tp2_pct"],
            sl_pct       = signal["sl_pct"],
            rsi_at_entry = rsi,
        )

        future = df[df.index > ts].head(60)
        t      = simulate_trade(t, future)

        balance += t.net_pnl
        trades.append(t)

        # Track consecutive losses for 24h pause
        if t.net_pnl < 0:
            consec_losses += 1
        else:
            consec_losses = 0
        if consec_losses >= MAX_CONSEC_LOSS:
            pause_until = t.exit_ts + pd.Timedelta(hours=CONSEC_LOSS_PAUSE_H) if t.exit_ts else None
            consec_losses = 0

        if t.exit_reason == "SL" and t.mode == "DOWNTREND":
            dn_cooldown = True
        if t.exit_ts:
            skip_until = t.exit_ts

    return trades


def print_results(trades: list, test_days: int) -> None:
    sep = "=" * 64
    print(sep)
    print(f"  BACKTEST RESULTS — Dual Strategy (last {test_days} days)")
    print(sep)

    if not trades:
        print("\n  No trades triggered in this period.\n")
        return

    balance   = INITIAL_BALANCE + sum(t.net_pnl for t in trades)
    closed    = [t for t in trades if t.exit_reason != "OPEN"]
    wins      = [t for t in closed if t.net_pnl > 0]
    losses    = [t for t in closed if t.net_pnl <= 0]
    tp1tp2    = [t for t in closed if t.exit_reason == "TP1+TP2"]
    tp1be     = [t for t in closed if t.exit_reason == "TP1+BE"]
    sl_hits   = [t for t in closed if t.exit_reason == "SL"]
    still_open = [t for t in trades if t.exit_reason == "OPEN"]
    up_t      = [t for t in trades if t.mode == "UPTREND"]
    dn_t      = [t for t in trades if t.mode == "DOWNTREND"]
    dn_closed = [t for t in dn_t   if t.exit_reason != "OPEN"]
    dn_wins   = [t for t in dn_closed if t.net_pnl > 0]

    total_pnl     = sum(t.net_pnl for t in trades)
    total_pnl_pct = total_pnl / INITIAL_BALANCE * 100
    win_rate      = len(wins) / len(closed) * 100 if closed else 0
    dn_wr         = len(dn_wins) / len(dn_closed) * 100 if dn_closed else 0

    print(f"\n  Starting balance :  ${INITIAL_BALANCE:.2f}")
    print(f"  Ending balance   :  ${balance:.2f}  ({total_pnl:+.2f} / {total_pnl_pct:+.2f}%)")

    print(f"\n  Total trades     :  {len(trades)}")
    print(f"    Uptrend   📈   :  {len(up_t)}")
    print(f"    Downtrend 📉   :  {len(dn_t)}  (win rate: {dn_wr:.0f}%)")
    print(f"    Still open     :  {len(still_open)}")

    print(f"\n  Closed trades    :  {len(closed)}")
    print(f"    TP1 + TP2  ✅✅:  {len(tp1tp2)}")
    print(f"    TP1 + BE   ✅➖:  {len(tp1be)}")
    print(f"    Stop loss  ❌  :  {len(sl_hits)}")

    print(f"\n  Win rate         :  {win_rate:.0f}%  ({len(wins)}W / {len(losses)}L)")
    print(f"  Net P&L          :  ${total_pnl:+.4f}  ({total_pnl_pct:+.2f}%)")

    if wins:
        print(f"  Avg win          :  +${sum(t.net_pnl for t in wins)/len(wins):.2f}")
    if losses:
        print(f"  Avg loss         :  -${abs(sum(t.net_pnl for t in losses)/len(losses)):.2f}")
    if wins and losses:
        rr = (sum(t.net_pnl for t in wins)/len(wins)) / abs(sum(t.net_pnl for t in losses)/len(losses))
        print(f"  Reward/Risk      :  {rr:.2f}x")

    print(f"\n{'─'*64}")
    print(f"  {'#':<3} {'Mode':<10} {'Entry (UTC)':<17} {'Price':<8} "
          f"{'RSI':<6} {'Outcome':<12} {'P&L':>9}")
    print(f"{'─'*64}")
    for n, t in enumerate(trades, 1):
        icon = "📈" if t.mode == "UPTREND" else "📉"
        tag  = "TP1+TP2" if t.exit_reason == "TP1+TP2" else \
               "TP1+BE"  if t.exit_reason == "TP1+BE"  else \
               "SL"      if t.exit_reason == "SL"       else "OPEN"
        print(f"  {n:<3} {icon} {t.mode:<8} "
              f"{str(t.entry_ts)[:16]:<17} "
              f"${t.entry_price:<7.4f} "
              f"{t.rsi_at_entry:<6.1f} "
              f"{tag:<12} "
              f"${t.net_pnl:+.4f}")
    print(f"{'─'*64}")
    print(f"  {'':52} Total: ${total_pnl:+.4f}")
    print()


def run():
    import sys
    test_days  = int(sys.argv[1]) if len(sys.argv) > 1 else 7
    warmup_days = test_days + 28   # extra history for EMA50 warmup

    print(f"Fetching XRP/USD 4H candles from Kraken ({warmup_days} days for warmup)...")
    df = fetch_ohlcv("4h", days=warmup_days)

    df["ema_fast"] = ta.ema(df["close"], length=EMA_FAST)
    df["ema_slow"] = ta.ema(df["close"], length=EMA_SLOW)
    df["rsi"]      = ta.rsi(df["close"], length=RSI_PERIOD)

    cutoff  = datetime.now(timezone.utc) - timedelta(days=test_days)
    test_df = df[df.index >= cutoff]
    print(f"Test window : {test_df.index[0].strftime('%Y-%m-%d %H:%M')} → "
          f"{test_df.index[-1].strftime('%Y-%m-%d %H:%M')} UTC")
    print(f"Candles     : {len(test_df)} × 4H\n")

    trades = run_backtest(df, test_days)
    print_results(trades, test_days)


if __name__ == "__main__":
    run()
