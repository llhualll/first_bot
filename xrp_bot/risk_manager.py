import time
from datetime import datetime, timezone
from loguru import logger
from config import (
    MAX_DAILY_LOSS, MAX_DAILY_GAIN, MAX_CONSEC_LOSS,
    MAX_DRAWDOWN_PCT, FLASH_CRASH_PCT, FLASH_CRASH_PAUSE_H,
)
from kraken_client import cancel_all_orders
import notifier


class RiskManager:
    def __init__(self):
        self.daily_pnl: float = 0.0          # running P&L for today (USD)
        self.peak_balance: float = 0.0        # highest recorded balance
        self.consec_losses: int = 0           # consecutive loss counter
        self.pause_until: float = 0.0         # epoch time to resume after pause
        self.flash_pause_until: float = 0.0   # epoch time to resume after flash crash
        self.stopped: bool = False            # hard stop (max drawdown)
        self._last_day: str = ""              # track UTC day for resets
        self._bear_notified: bool = False     # avoid spamming bear market alert

    # ── Daily reset ───────────────────────────────────────────────────────────

    def check_day_reset(self) -> None:
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        if today != self._last_day:
            logger.info(f"New UTC day: {today}. Resetting daily P&L.")
            self.daily_pnl = 0.0
            self._last_day = today

    # ── Record trade outcome ──────────────────────────────────────────────────

    def record_trade(self, net_pnl: float) -> None:
        self.daily_pnl += net_pnl
        if net_pnl < 0:
            self.consec_losses += 1
            logger.warning(f"Loss recorded. Consecutive losses: {self.consec_losses}")
        else:
            self.consec_losses = 0

        if self.consec_losses >= MAX_CONSEC_LOSS:
            pause_secs = FLASH_CRASH_PAUSE_H * 3600  # reuse same 4h window
            self.pause_until = time.time() + pause_secs
            logger.warning(f"Consecutive loss limit hit. Pausing {pause_secs/3600:.0f}h.")
            notifier.notify_consecutive_loss_pause(int(pause_secs / 3600))
            self.consec_losses = 0

    def update_peak(self, balance_usd: float) -> None:
        if balance_usd > self.peak_balance:
            self.peak_balance = balance_usd

    # ── Gate checks ───────────────────────────────────────────────────────────

    def is_paused(self) -> bool:
        if self.pause_until and time.time() < self.pause_until:
            remaining = (self.pause_until - time.time()) / 3600
            logger.info(f"Bot paused. Resumes in {remaining:.1f}h.")
            return True
        return False

    def is_flash_paused(self) -> bool:
        if self.flash_pause_until and time.time() < self.flash_pause_until:
            remaining = (self.flash_pause_until - time.time()) / 3600
            logger.info(f"Flash crash pause. Resumes in {remaining:.1f}h.")
            return True
        return False

    def check_daily_limits(self, balance_usd: float) -> bool:
        """Return True if trading should stop for today."""
        pnl_pct = self.daily_pnl / balance_usd if balance_usd else 0

        if pnl_pct <= -MAX_DAILY_LOSS:
            logger.warning(f"Daily loss limit hit: {pnl_pct*100:.2f}%")
            cancel_all_orders()
            notifier.notify_daily_loss_limit()
            return True

        if pnl_pct >= MAX_DAILY_GAIN:
            logger.info(f"Daily gain target reached: {pnl_pct*100:.2f}%")
            return True

        return False

    def check_max_drawdown(self, balance_usd: float) -> bool:
        """Return True if max drawdown breached (hard stop)."""
        if self.peak_balance <= 0:
            return False
        drawdown = (self.peak_balance - balance_usd) / self.peak_balance
        if drawdown >= MAX_DRAWDOWN_PCT:
            logger.critical(f"Max drawdown reached: {drawdown*100:.1f}%")
            cancel_all_orders()
            notifier.notify_max_drawdown(balance_usd, self.peak_balance, drawdown)
            self.stopped = True
            return True
        return False

    def check_flash_crash(self, change_1h: float) -> bool:
        """Return True if flash crash detected and pause triggered."""
        if change_1h <= -FLASH_CRASH_PCT:
            logger.warning(f"Flash crash: {change_1h*100:.1f}% in 1h")
            cancel_all_orders()
            notifier.notify_flash_crash(change_1h)
            self.flash_pause_until = time.time() + FLASH_CRASH_PAUSE_H * 3600
            return True
        return False

    def check_trend(self, ema_fast: float, ema_slow: float) -> bool:
        """Return True if market is in uptrend (ok to trade)."""
        uptrend = ema_fast > ema_slow
        if not uptrend and not self._bear_notified:
            notifier.notify_bear_market(ema_fast, ema_slow)
            self._bear_notified = True
            logger.info("Bear market: empty position mode.")
        elif uptrend and self._bear_notified:
            notifier.notify_trend_recovered()
            self._bear_notified = False
            logger.info("Uptrend recovered. Resuming trading.")
        return uptrend

    def can_trade(self, balance_usd: float, market_data: dict) -> bool:
        """Master gate: returns True only if all conditions allow trading."""
        self.check_day_reset()

        if self.stopped:
            logger.critical("Bot stopped (max drawdown). Manual restart required.")
            return False

        if self.is_paused():
            return False

        if self.is_flash_paused():
            return False

        if self.check_max_drawdown(balance_usd):
            return False

        if self.check_flash_crash(market_data.get("change_1h", 0)):
            return False

        if self.check_daily_limits(balance_usd):
            return False

        if not self.check_trend(
            market_data.get("ema_fast", 0),
            market_data.get("ema_slow", 0),
        ):
            return False

        return True
