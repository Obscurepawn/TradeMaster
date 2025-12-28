from dataclasses import dataclass
from datetime import date
from typing import List, Optional, Union

@dataclass
class BacktestConfig:
    start_date: date
    end_date: date
    initial_cash: float
    strategy_name: str
    baseline: Union[str, List[str]]
    universe: Optional[List[str]] = None
