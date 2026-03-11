"""
Logging configuration for structured log output and trade journaling.
"""

import logging
import csv
import os
from datetime import datetime

from config import LOG_FILE, TRADE_LOG_FILE


def setup_logger(name: str = "scalper") -> logging.Logger:
    logger = logging.getLogger(name)
    logger.setLevel(logging.DEBUG)

    ch = logging.StreamHandler()
    ch.setLevel(logging.INFO)
    ch.setFormatter(logging.Formatter(
        "%(asctime)s [%(levelname)s] %(message)s", datefmt="%H:%M:%S"
    ))

    fh = logging.FileHandler(LOG_FILE)
    fh.setLevel(logging.DEBUG)
    fh.setFormatter(logging.Formatter(
        "%(asctime)s [%(levelname)s] %(name)s - %(message)s"
    ))

    logger.addHandler(ch)
    logger.addHandler(fh)
    return logger


class TradeJournal:
    """Logs every trade to a CSV file for post-analysis."""

    HEADERS = [
        "timestamp", "symbol", "expiry", "strike", "right",
        "action", "quantity", "price", "pnl", "reason"
    ]

    def __init__(self, filepath: str = TRADE_LOG_FILE):
        self.filepath = filepath
        if not os.path.exists(filepath):
            with open(filepath, "w", newline="") as f:
                writer = csv.writer(f)
                writer.writerow(self.HEADERS)

    def log_trade(self, symbol, expiry, strike, right,
                  action, quantity, price, pnl=0.0, reason=""):
        with open(self.filepath, "a", newline="") as f:
            writer = csv.writer(f)
            writer.writerow([
                datetime.now().isoformat(), symbol, expiry, strike, right,
                action, quantity, price, pnl, reason
            ])
