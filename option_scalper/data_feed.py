"""
Real-time option data streaming and near-ATM contract discovery.
"""

import logging
from typing import List, Optional
from collections import deque
from dataclasses import dataclass, field

from ib_insync import IB, Stock, Option, Ticker, Contract
import ib_insync.util as util

from config import (
    SYMBOL, EXCHANGE, CURRENCY, RIGHT,
    NUM_STRIKES, MOMENTUM_WINDOW
)

logger = logging.getLogger("scalper.data")


dataclass
class OptionQuote:
    """Snapshot of an option's live market data."""
    contract: Contract
    bid: float = 0.0
    ask: float = 0.0
    last: float = 0.0
    volume: int = 0
    open_interest: int = 0
    implied_vol: float = 0.0
    delta: float = 0.0
    gamma: float = 0.0
    theta: float = 0.0
    vega: float = 0.0
    mid: float = 0.0
    spread: float = 0.0
    price_history: deque = field(default_factory=lambda: deque(maxlen=MOMENTUM_WINDOW))


class DataFeed:
    """Discovers near-ATM options and streams real-time quotes."""

    def __init__(self, ib: IB):
        self.ib = ib
        self.underlying: Optional[Contract] = None
        self.option_contracts: List[Contract] = []
        self.tickers: dict[str, Ticker] = {}
        self.quotes: dict[str, OptionQuote] = {}

    def setup(self):
        """Discover underlying price, find ATM strikes, qualify contracts."""
        self.underlying = Stock(SYMBOL, EXCHANGE, CURRENCY)
        self.ib.qualifyContracts(self.underlying)

        [stock_ticker] = self.ib.reqTickers(self.underlying)
        underlying_price = stock_ticker.marketPrice()
        logger.info(f"Underlying {{SYMBOL}} price: {{underlying_price:.2f}}")

        chains = self.ib.reqSecDefOptParams(
            SYMBOL, "", self.underlying.secType, self.underlying.conId
        )
        if not chains:
            raise RuntimeError("No option chains found.")

        chain = next((c for c in chains if c.exchange == "SMART"), chains[0])

        expirations = sorted(chain.expirations)
        nearest_expiry = expirations[0]
        logger.info(f"Using expiry: {{nearest_expiry}}")

        strikes = sorted(chain.strikes)
        atm_idx = min(range(len(strikes)),
                      key=lambda i: abs(strikes[i] - underlying_price))
        start = max(0, atm_idx - NUM_STRIKES // 2)
        end = min(len(strikes), atm_idx + NUM_STRIKES // 2 + 1)
        selected_strikes = strikes[start:end]

        logger.info(f"Selected strikes: {{selected_strikes}}")

        self.option_contracts = []
        for strike in selected_strikes:
            opt = Option(SYMBOL, nearest_expiry, strike, RIGHT, EXCHANGE)
            self.option_contracts.append(opt)

        self.ib.qualifyContracts(*self.option_contracts)
        logger.info(f"Qualified {{len(self.option_contracts)}} option contracts.")

    def subscribe(self):
        """Subscribe to real-time market data for all option contracts."""
        for contract in self.option_contracts:
            ticker = self.ib.reqMktData(
                contract, genericTickList="106",
                snapshot=False, regulatorySnapshot=False
            )
            key = str(contract.conId)
            self.tickers[key] = ticker
            self.quotes[key] = OptionQuote(contract=contract)

        logger.info(f"Subscribed to {{len(self.tickers)}} option data streams.")

    def update(self):
        """Pull latest data from tickers into OptionQuote objects."""
        for key, ticker in self.tickers.items():
            q = self.quotes[key]

            bid = ticker.bid if util.isNan(ticker.bid) is False else q.bid
            ask = ticker.ask if util.isNan(ticker.ask) is False else q.ask

            q.bid = bid
            q.ask = ask
            q.last = ticker.last if not util.isNan(ticker.last) else q.last
            q.volume = ticker.volume if ticker.volume else q.volume
            q.mid = (bid + ask) / 2 if bid > 0 and ask > 0 else q.mid
            q.spread = ask - bid if bid > 0 and ask > 0 else q.spread

            if ticker.modelGreeks:
                q.implied_vol = ticker.modelGreeks.impliedVol or 0.0
                q.delta = ticker.modelGreeks.delta or 0.0
                q.gamma = ticker.modelGreeks.gamma or 0.0
                q.theta = ticker.modelGreeks.theta or 0.0
                q.vega = ticker.modelGreeks.vega or 0.0

            if q.mid > 0:
                q.price_history.append(q.mid)

    def get_quotes(self) -> List[OptionQuote]:
        """Return all current option quotes."""
        return list(self.quotes.values())

    def unsubscribe(self):
        """Cancel all market data subscriptions."""
        for contract in self.option_contracts:
            self.ib.cancelMktData(contract)
        logger.info("Unsubscribed from all option data streams.")
