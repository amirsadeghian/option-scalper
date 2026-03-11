"""
Main bot orchestrator - ties all components together into the scalping loop.
"""

import time
import signal
import logging

from connection import IBConnection
from data_feed import DataFeed
from signals import SignalEngine
from execution import ExecutionEngine
from risk_manager import RiskManager
from logger_setup import setup_logger

logger = setup_logger()


class OptionScalperBot:
    """Medium-frequency option scalping bot for IBKR."""

    def __init__(self):
        self.conn = IBConnection()
        self.ib = None
        self.data_feed = None
        self.signal_engine = SignalEngine()
        self.execution = None
        self.risk = RiskManager()
        self._running = False

    def start(self):
        """Initialize all components and start the main loop."""
        logger.info("=" * 60)
        logger.info("  Option Scalper Bot Starting")
        logger.info("=" * 60)

        self.ib = self.conn.connect()

        self.data_feed = DataFeed(self.ib)
        self.data_feed.setup()
        self.data_feed.subscribe()

        self.execution = ExecutionEngine(self.ib)

        signal.signal(signal.SIGINT, self._shutdown_handler)

        self._running = True
        self._main_loop()

    def _main_loop(self):
        """Core scalping loop."""
        logger.info("Entering main scalping loop...")

        while self._running:
            try:
                self.ib.sleep(0.1)
                self.data_feed.update()
                self._check_exits()

                if self.risk.can_trade(self.execution.open_count):
                    self._check_entries()

            except KeyboardInterrupt:
                break
            except Exception as e:
                logger.error(f"Error in main loop: {{e}}", exc_info=True)
                self.ib.sleep(1)

        self._shutdown()

    def _check_entries(self):
        """Scan for entry signals and execute."""
        quotes = self.data_feed.get_quotes()
        signals = self.signal_engine.scan_entries(quotes)

        if not signals:
            return

        best = max(signals, key=lambda s: s.strength)

        con_id = str(best.quote.contract.conId)
        if self.execution.get_position(con_id):
            return

        logger.info(f"Acting on signal: {{best.quote.contract.localSymbol}} "
                     f"strength={{best.strength:.2f}}")

        position = self.execution.buy(best.quote.contract, best.quote.mid)
        if position:
            self.risk.record_trade()

    def _check_exits(self):
        """Check all open positions for exit conditions."""
        quotes_map = {
            str(q.contract.conId): q
            for q in self.data_feed.get_quotes()
        }

        for con_id, position in list(self.execution.positions.items()):
            quote = quotes_map.get(con_id)
            if not quote:
                continue

            exit_signal = self.signal_engine.check_exit(
                position.entry_price, quote
            )
            if exit_signal:
                pnl = self.execution.sell(
                    position, quote.mid, reason=exit_signal.reason
                )
                self.risk.record_trade(pnl)
                continue

            if self.risk.should_force_close(position.entry_time):
                logger.warning(
                    f"Force closing {{position.contract.localSymbol}} "
                    f"(held too long)"
                )
                pnl = self.execution.sell(
                    position, quote.mid, reason="max_hold_time"
                )
                self.risk.record_trade(pnl)

    def _shutdown_handler(self, signum, frame):
        logger.info("Shutdown signal received...")
        self._running = False

    def _shutdown(self):
        """Gracefully shut down: close positions, unsubscribe, disconnect."""
        logger.info("Shutting down...")

        quotes_map = {
            str(q.contract.conId): q
            for q in self.data_feed.get_quotes()
        }
        for con_id, position in list(self.execution.positions.items()):
            quote = quotes_map.get(con_id)
            mid = quote.mid if quote else 0
            self.execution.sell(position, mid, reason="shutdown")

        self.data_feed.unsubscribe()
        self.conn.disconnect()
        logger.info("Bot shut down complete.")


if __name__ == "__main__":
    bot = OptionScalperBot()
    bot.start()
