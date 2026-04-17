import requests
from loguru import logger
from config import TELEGRAM_TOKEN, TELEGRAM_CHAT_ID, TELEGRAM_ENABLED, PAPER_TRADE


def _send(text: str) -> None:
    if not TELEGRAM_ENABLED:
        logger.info(f"[TELEGRAM disabled] {text}")
        return
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {"chat_id": TELEGRAM_CHAT_ID, "text": text, "parse_mode": "HTML"}
    try:
        resp = requests.post(url, json=payload, timeout=10)
        if not resp.ok:
            logger.warning(f"Telegram send failed: {resp.text}")
    except Exception as e:
        logger.error(f"Telegram error: {e}")


def _mode() -> str:
    return "[PAPER] " if PAPER_TRADE else ""


def notify_startup(balance_usd: float, balance_xrp: float, price: float) -> None:
    _send(
        f"🤖 <b>{_mode()}XRP Bot Started</b>\n"
        f"─────────────────\n"
        f"Balance:  {balance_xrp:.2f} XRP (~${balance_usd:.2f})\n"
        f"Price:    ${price:.4f}\n"
        f"Mode:     {'Paper Trade' if PAPER_TRADE else 'Live'}\n"
        f"─────────────────\n"
        f"Bot is running 24/7 ✅"
    )


def notify_buy(price: float, amount_xrp: float, amount_usd: float,
               tp1: float, tp2: float, sl: float,
               strategy_mode: str = "UPTREND") -> None:
    tp1_pct = (tp1 / price - 1) * 100
    tp2_pct = (tp2 / price - 1) * 100
    sl_pct  = (1 - sl / price) * 100
    icon = "📈" if strategy_mode == "UPTREND" else "📉"
    _send(
        f"🟢 <b>{_mode()}Buy Order Placed</b>\n"
        f"─────────────────\n"
        f"Strategy: {icon} {strategy_mode}\n"
        f"Pair:     XRP/USD\n"
        f"Buy @:    ${price:.4f}\n"
        f"Amount:   {amount_xrp:.2f} XRP (${amount_usd:.2f})\n"
        f"─────────────────\n"
        f"TP1:      ${tp1:.4f} (+{tp1_pct:.1f}%)\n"
        f"TP2:      ${tp2:.4f} (+{tp2_pct:.1f}%)\n"
        f"SL:       ${sl:.4f} (-{sl_pct:.1f}%)"
    )


def notify_tp1(sell_price: float, profit: float, remaining_xrp: float, breakeven: float) -> None:
    _send(
        f"✅ <b>{_mode()}TP1 Hit — +0.5% Locked</b>\n"
        f"─────────────────\n"
        f"Sell @:   ${sell_price:.4f}\n"
        f"Profit:   +${profit:.2f}\n"
        f"Remaining: {remaining_xrp:.2f} XRP\n"
        f"SL moved to breakeven: ${breakeven:.4f}\n"
        f"─────────────────\n"
        f"Waiting for TP2..."
    )


def notify_tp2(sell_price: float, total_profit: float) -> None:
    _send(
        f"✅✅ <b>{_mode()}TP2 Hit — +1.0% Locked</b>\n"
        f"─────────────────\n"
        f"Sell @:   ${sell_price:.4f}\n"
        f"Total profit: +${total_profit:.2f}\n"
        f"─────────────────\n"
        f"Daily target reached. Stopping until UTC 00:00 🎯"
    )


def notify_stop_loss(sell_price: float, loss: float) -> None:
    _send(
        f"🔴 <b>{_mode()}Stop Loss Triggered</b>\n"
        f"─────────────────\n"
        f"Sell @:   ${sell_price:.4f}\n"
        f"Loss:     -${abs(loss):.2f}\n"
        f"─────────────────\n"
        f"Scanning for next signal..."
    )


def notify_order_cancelled(reason: str) -> None:
    _send(f"🕐 <b>{_mode()}Buy Order Cancelled</b>\n{reason}")


def notify_bear_market(ema_fast: float, ema_slow: float) -> None:
    _send(
        f"⚠️ <b>Downtrend Detected</b>\n"
        f"EMA20={ema_fast:.4f} < EMA50={ema_slow:.4f}\n"
        f"Switching to downtrend strategy: RSI &lt; 35 entries only, half size."
    )


def notify_trend_recovered() -> None:
    _send("✅ <b>Uptrend Recovered</b>\nEMA20 > EMA50 again. Resuming trading.")


def notify_flash_crash(change_pct: float) -> None:
    _send(
        f"🚨 <b>Flash Crash Detected!</b>\n"
        f"XRP dropped {abs(change_pct)*100:.1f}% in 1 hour.\n"
        f"All orders cancelled. Pausing 4 hours."
    )


def notify_daily_loss_limit() -> None:
    _send("🛑 <b>Daily Loss Limit Reached (-2%)</b>\nStopping until UTC 00:00.")


def notify_consecutive_loss_pause(hours: int) -> None:
    _send(f"⏸ <b>2 Consecutive Losses</b>\nPausing for {hours} hours.")


def notify_max_drawdown(balance: float, peak: float, drawdown_pct: float) -> None:
    _send(
        f"🚨 <b>Max Drawdown Reached — BOT STOPPED</b>\n"
        f"─────────────────\n"
        f"Peak balance:   ${peak:.2f}\n"
        f"Current:        ${balance:.2f}\n"
        f"Drawdown:       -{drawdown_pct*100:.1f}%\n"
        f"─────────────────\n"
        f"Manual review required before restarting."
    )


def notify_daily_report(date: str, trades: int, gross_profit: float,
                        gross_loss: float, net: float, balance_usd: float) -> None:
    sign = "+" if net >= 0 else ""
    _send(
        f"📊 <b>Daily Report — {date}</b>\n"
        f"─────────────────\n"
        f"Trades:    {trades}\n"
        f"Profit:    +${gross_profit:.2f}\n"
        f"Loss:      -${abs(gross_loss):.2f}\n"
        f"Net P&L:   {sign}${net:.2f} ({sign}{net/balance_usd*100:.2f}%)\n"
        f"Balance:   ~${balance_usd:.2f}\n"
        f"─────────────────\n"
        f"Next cycle starts now ✅"
    )


def notify_error(msg: str) -> None:
    _send(f"❗ <b>Bot Error</b>\n{msg}\nPlease check the logs.")
