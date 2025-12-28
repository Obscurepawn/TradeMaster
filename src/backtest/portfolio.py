from typing import Dict, List
from src.domain import Position, Trade, TradeDirection

class Portfolio:
    """Tracks cash, positions, and trade history for a backtest.

    Attributes:
        cash: Current available cash balance.
        positions: Dictionary mapping security codes to Position objects.
        trades: List of all executed Trade objects.
        history: Historical record of total portfolio value.
    """
    def __init__(self, initial_cash: float):
        """Initializes the Portfolio.

        Args:
            initial_cash: The starting cash balance.
        """
        self.cash = initial_cash
        self.positions: Dict[str, Position] = {}
        self.trades: List[Trade] = []
        self.history: List[float] = []

    @property
    def total_value(self) -> float:
        """Calculates the current total equity (cash + market value of positions).

        Returns:
            Total portfolio value as a float.
        """
        pos_value = sum(p.market_value for p in self.positions.values())
        return self.cash + pos_value

    def update_price(self, code: str, price: float):
        """Updates the current market price for a held security.

        Args:
            code: The security identifier.
            price: The new market price.
        """
        if code in self.positions:
            self.positions[code].current_price = price

    def record_daily_value(self):
        """Appends the current total value to the history log."""
        self.history.append(self.total_value)

    def execute_trade(self, trade: Trade):
        """Updates the portfolio state based on a trade execution.

        Handles both BUY and SELL directions, updating cash and positions.

        Args:
            trade: The Trade object to be executed.
        """
        self.trades.append(trade)
        total_cost = trade.cost
        
        if trade.direction == TradeDirection.BUY:
            self.cash -= total_cost
            if trade.code in self.positions:
                pos = self.positions[trade.code]
                total_qty = pos.quantity + trade.quantity
                total_spend = (pos.avg_cost * pos.quantity) + total_cost
                pos.quantity = total_qty
                pos.avg_cost = total_spend / total_qty
            else:
                self.positions[trade.code] = Position(
                    code=trade.code,
                    quantity=trade.quantity,
                    avg_cost=trade.cost / trade.quantity, # approximate unit cost incl comm
                    current_price=trade.price
                )
        elif trade.direction == TradeDirection.SELL:
            self.cash += (trade.quantity * trade.price) - trade.commission
            if trade.code in self.positions:
                pos = self.positions[trade.code]
                pos.quantity -= trade.quantity
                if pos.quantity <= 0:
                    del self.positions[trade.code]