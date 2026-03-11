"""
Entry/exit signal detection for option scalping.
"""

import logging
from dataclasses import dataclass
from typing import Optional, List

from data_feed import OptionQuote
from config import (
    SPREAD_THRESHOLD, MOMENTUM_THRESHOLD,
    MOMENTUM_WINDOW, MIN_VOLUME, PROFIT_TARGET, STOP_LOSS
)

logger = logging.getLogger("scalper.signals")


dataclass
class Signal:
    """Represents a trading signal."""
    action: str
    quote: OptionQuote
    reason: str
    strength: float


class SignalEngine:
    """
    Generates entry/exit signals based on:
    - Bid-ask spread compression
    - Short-term price momentum
    - Volume filter
    - Greeks filter (delta range)
    """

    def __init__(self):
        self.min_delta = 0.20
        self.max_delta = 0.70

    def scan_entries(self, quotes: List[OptionQuote]) -> List[Signal]:
        """Scan all quotes for entry signals."""
        signals = []
        for q in quotes:
            signal = self._evaluate_entry(q)
            if signal:
                signals.append(signal)
        return signals

    def _evaluate_entry(self, q: OptionQuote) -> Optional[Signal]:
        """Evaluate a single option for entry signal."""
        if q.bid <= 0 or q.ask <= 0 or q.mid <= 0:
            return None
        if q.spread > SPREAD_THRESHOLD:
            return None
        if q.volume < MIN_VOLUME:
            return None
        if not (self.min_delta <= abs(q.delta) <= self.max_delta):
            return None

        momentum = self._calc_momentum(q)
        if momentum < MOMENTUM_THRESHOLD:
            return None

        spread_score = max(0, 1 - q.spread / SPREAD_THRESHOLD)
        momentum_score = min(1, momentum / (MOMENTUM_THRESHOLD * 3))
        strength = (spread_score + momentum_score) / 2

        logger.info(
            f"ENTRY SIGNAL: {{q.contract.localSymbol}} | "
            f"mid={{q.mid:.2f}} spread={{q.spread:.3f}} "
            f"momentum={{momentum:.4f}} delta={{q.delta:.3f}} "
            f"strength={{strength:.2f}}"
        )

        return Signal(
            action="BUY",
            quote=q,
            reason=f"spread={{q.spread:.3f}}, momentum={{momentum:.4f}}",
            strength=strength
        )

    def _calc_momentum(self, q: OptionQuote) -> float:
        """Calculate short-term momentum from price history."""
        history = list(q.price_history)
        if len(history) < MOMENTUM_WINDOW:
            return 0.0
        return history[-1] - history[0]

    def check_exit(self, entry_price: float, current_quote: OptionQuote) -> Optional[Signal]:
        """Check if an exit condition is met for a held position."""
        if current_quote.mid <= 0:
            return None

        pnl = current_quote.mid - entry_price

        if pnl >= PROFIT_TARGET:
            return Signal(
                action="SELL",
                quote=current_quote,
                reason=f"profit_target (pnl={{pnl:.3f}})",
                strength=1.0
            )

        if pnl <= -STOP_LOSS:
            return Signal(
                action="SELL",
                quote=current_quote,
                reason=f"stop_loss (pnl={{pnl:.3f}})",
                strength=1.0
            )

        return None
