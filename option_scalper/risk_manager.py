"""
Risk management: position limits, daily loss cap, trade throttling.
"""

import time
import logging

from config import (
    MAX_POSITIONS, MAX_DAILY_LOSS,
    MAX_TRADES_PER_DAY, COOLDOWN_SECONDS,
    MAX_POSITION_HOLD_SEC
)

logger = logging.getLogger("scalper.risk")


class RiskManager:
    """Enforces risk limits for the scalping bot."""

    def __init__(self):
        self.daily_pnl: float = 0.0
        self.trade_count: int = 0
        self.last_trade_time: float = 0.0
        self._halted: bool = False

    def can_trade(self, open_positions: int) -> bool:
        """Check all risk gates before allowing a new trade."""
        if self._halted:
            return False

        if open_positions >= MAX_POSITIONS:
            logger.debug("Risk: max positions reached.")
            return False

        if self.daily_pnl <= -MAX_DAILY_LOSS:
            logger.warning(f"Risk: daily loss limit hit (${self.daily_pnl:.2f}).")
            self._halted = True
            return False

        if self.trade_count >= MAX_TRADES_PER_DAY:
            logger.warning("Risk: max daily trades reached.")
            self._halted = True
            return False

        elapsed = time.time() - self.last_trade_time
        if elapsed < COOLDOWN_SECONDS:
            logger.debug(f"Risk: cooldown ({{elapsed:.1f}}s / {{COOLDOWN_SECONDS}}s).")
            return False

        return True

    def record_trade(self, pnl: float = 0.0):
        """Record a completed trade."""
        self.trade_count += 1
        self.daily_pnl += pnl
        self.last_trade_time = time.time()
        logger.info(
            f"Risk update: trades={{self.trade_count}}, "
            f"daily_pnl=${{self.daily_pnl:.2f}}"
        )

    def should_force_close(self, entry_time: float) -> bool:
        """Check if a position has been held too long."""
        return (time.time() - entry_time) > MAX_POSITION_HOLD_SEC

    @property
    def is_halted(self) -> bool:
        return self._halted

    def reset_daily(self):
        """Reset daily counters (call at start of each trading day)."""
        self.daily_pnl = 0.0
        self.trade_count = 0
        self._halted = False
        logger.info("Risk manager: daily counters reset.")
