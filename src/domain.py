from dataclasses import dataclass
from datetime import date
from enum import Enum
from typing import List, Optional, Dict

class TradeDirection(Enum):
    BUY = "BUY"
    SELL = "SELL"

@dataclass
class Position:
    code: str
    quantity: int
    avg_cost: float
    current_price: float

    @property
    def market_value(self) -> float:
        return self.quantity * self.current_price

    @property
    def unrealized_pnl(self) -> float:
        return (self.current_price - self.avg_cost) * self.quantity

@dataclass
class Trade:
    date: date
    code: str
    direction: TradeDirection
    quantity: int
    price: float
    cost: float  # Includes commission
    commission: float

@dataclass

class BacktestResult:

    total_return: float

    max_drawdown: float

    sharpe_ratio: float

    equity_curve: List[float]  # Simple list of floats for now, indexed by date implicitly or matched with a date list

    baselines: Dict[str, List[float]]

    dates: List[date]
