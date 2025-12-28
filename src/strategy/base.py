from abc import ABC, abstractmethod
from datetime import date
from typing import List, Dict
from src.domain import TradeSignal
from src.data_loader.base import DataLoader

class Strategy(ABC):
    def __init__(self, data_loader: DataLoader, capital: float):
        self.data_loader = data_loader
        self.capital = capital

    @abstractmethod
    def select_stocks(self, current_date: date) -> List[str]:
        """
        Logic for stock picking.
        """
        pass

    @abstractmethod
    def on_bar(self, current_date: date, positions: Dict[str, int], cash: float, total_equity: float) -> List[TradeSignal]:
        """
        Called daily.
        """
        pass
