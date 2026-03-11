"""
Order placement and position tracking for the scalping bot.
"""

import time
import logging
from dataclasses import dataclass, field
from typing import Dict, Optional

from ib_insync import IB, Contract, Trade, LimitOrder, MarketOrder

from config import ORDER_SIZE, USE_LIMIT_ORDERS, LIMIT_OFFSET
from logger_setup import TradeJournal

logger = logging.getLogger("scalper.execution")


dataclass
class Position:
    """Tracks an open position."""
    contract: Contract
    entry_price: float
    entry_time: float
    size: int
    trade: Trade
    con_id: str = ""

    def __post_init__(self):
        self.con_id = str(self.contract.conId)


class ExecutionEngine:
    """Handles order placement, fill tracking, and position management."""

    def __init__(self, ib: IB):
        self.ib = ib
        self.positions: Dict[str, Position] = {}
        self.journal = TradeJournal()

    def buy(self, contract: Contract, mid_price: float) -> Optional[Position]:
        """Place a buy order for the given option contract."""
        try:
            if USE_LIMIT_ORDERS:
                limit_price = round(mid_price + LIMIT_OFFSET, 2)
                order = LimitOrder("BUY", ORDER_SIZE, limit_price)
                order.tif = "IOC"
            else:
                order = MarketOrder("BUY", ORDER_SIZE)

            trade = self.ib.placeOrder(contract, order)
            logger.info(
                f"BUY order placed: {{contract.localSymbol}} "
                f"@ {{mid_price:.2f}} (size={{ORDER_SIZE}})"
            )

            self.ib.sleep(1)

            if trade.orderStatus.status == "Filled":
                fill_price = trade.orderStatus.avgFillPrice
                pos = Position(
                    contract=contract,
                    entry_price=fill_price,
                    entry_time=time.time(),
                    size=ORDER_SIZE,
                    trade=trade
                )
                self.positions[pos.con_id] = pos

                self.journal.log_trade(
                    symbol=contract.symbol,
                    expiry=contract.lastTradeDateOrContractMonth,
                    strike=contract.strike,
                    right=contract.right,
                    action="BUY",
                    quantity=ORDER_SIZE,
                    price=fill_price,
                    reason="entry_signal"
                )
                logger.info(f"FILLED BUY: {{contract.localSymbol}} @ {{fill_price:.2f}}")
                return pos
            else:
                self.ib.cancelOrder(order)
                logger.warning(
                    f"BUY not filled, cancelled: {{contract.localSymbol}} "
                    f"status={{trade.orderStatus.status}}"
                )
                return None

        except Exception as e:
            logger.error(f"Error placing BUY order: {{e}}")
            return None

    def sell(self, position: Position, current_mid: float, reason: str = "") -> float:
        """Close an open position. Returns realized PnL."""
        try:
            contract = position.contract

            if USE_LIMIT_ORDERS:
                limit_price = round(current_mid - LIMIT_OFFSET, 2)
                order = LimitOrder("SELL", position.size, limit_price)
                order.tif = "IOC"
            else:
                order = MarketOrder("SELL", position.size)

            trade = self.ib.placeOrder(contract, order)
            logger.info(
                f"SELL order placed: {{contract.localSymbol}} "
                f"@ ~{{current_mid:.2f}} reason={{reason}}"
            )

            self.ib.sleep(1)

            if trade.orderStatus.status == "Filled":
                fill_price = trade.orderStatus.avgFillPrice
                pnl = (fill_price - position.entry_price) * position.size * 100
                self._remove_position(position.con_id)

                self.journal.log_trade(
                    symbol=contract.symbol,
                    expiry=contract.lastTradeDateOrContractMonth,
                    strike=contract.strike,
                    right=contract.right,
                    action="SELL",
                    quantity=position.size,
                    price=fill_price,
                    pnl=pnl,
                    reason=reason
                )
                logger.info(
                    f"FILLED SELL: {{contract.localSymbol}} @ {{fill_price:.2f}} "
                    f"PnL=${{pnl:.2f}} ({{reason}})"
                )
                return pnl
            else:
                self.ib.cancelOrder(order)
                logger.warning(f"SELL not filled, will retry: {{contract.localSymbol}}")
                return 0.0

        except Exception as e:
            logger.error(f"Error placing SELL order: {{e}}")
            return 0.0

    def _remove_position(self, con_id: str):
        if con_id in self.positions:
            del self.positions[con_id]

    def get_position(self, con_id: str) -> Optional[Position]:
        return self.positions.get(con_id)

    @property
    def open_count(self) -> int:
        return len(self.positions)
