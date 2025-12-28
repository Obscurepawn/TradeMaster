from typing import Dict, List
from src.domain import Position, Trade, TradeDirection

class Portfolio:
    def __init__(self, initial_cash: float):
        self.cash = initial_cash
        self.positions: Dict[str, Position] = {} # code -> Position
        self.trades: List[Trade] = []
        self.history: List[float] = [] # Daily total equity

    @property
    def total_value(self) -> float:
        pos_value = sum(p.market_value for p in self.positions.values())
        return self.cash + pos_value

    def update_price(self, code: str, price: float):
        if code in self.positions:
            self.positions[code].current_price = price

    def record_daily_value(self):
        self.history.append(self.total_value)

    def execute_trade(self, trade: Trade):
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