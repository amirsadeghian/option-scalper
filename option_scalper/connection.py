"""
IBKR connection manager with auto-reconnect.
"""

import time
import logging
from ib_insync import IB

from config import TWS_HOST, TWS_PORT, CLIENT_ID

logger = logging.getLogger("scalper.connection")


class IBConnection:
    """Manages the IB connection with reconnect logic."""

    def __init__(self):
        self.ib = IB()
        self._setup_events()

    def _setup_events(self):
        self.ib.disconnectedEvent += self._on_disconnect
        self.ib.errorEvent += self._on_error

    def _on_disconnect(self):
        logger.warning("Disconnected from IBKR. Attempting reconnect...")
        self._reconnect()

    def _on_error(self, reqId, errorCode, errorString, contract):
        logger.error(f"IB Error {errorCode}: {errorString} (reqId={reqId})")

    def connect(self):
        """Establish connection to TWS / IB Gateway."""
        logger.info(f"Connecting to IBKR at {TWS_HOST}:{TWS_PORT}...")
        self.ib.connect(TWS_HOST, TWS_PORT, clientId=CLIENT_ID)
        logger.info("Connected to IBKR successfully.")
        return self.ib

    def _reconnect(self, max_retries: int = 5, delay: int = 5):
        for attempt in range(1, max_retries + 1):
            try:
                logger.info(f"Reconnect attempt {attempt}/{max_retries}...")
                self.ib.disconnect()
                time.sleep(delay)
                self.ib.connect(TWS_HOST, TWS_PORT, clientId=CLIENT_ID)
                logger.info("Reconnected successfully.")
                return
            except Exception as e:
                logger.error(f"Reconnect failed: {e}")
                time.sleep(delay * attempt)
        logger.critical("Max reconnect attempts reached. Shutting down.")
        raise ConnectionError("Could not reconnect to IBKR.")

    def disconnect(self):
        self.ib.disconnect()
        logger.info("Disconnected from IBKR.")
