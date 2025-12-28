import unittest
from unittest.mock import MagicMock
import pandas as pd
from datetime import date
from src.strategy.pe_small_cap import PESmallCapStrategy
from src.backtest.portfolio import Portfolio
from src.backtest.engine import BacktestContext
from src.domain import TradeDirection

class TestPESmallCapStrategy(unittest.TestCase):
    def test_on_bar_buy(self):
        strategy = PESmallCapStrategy()
        
        # Mock Context
        pf = Portfolio(initial_cash=100000.0)
        context = BacktestContext(pf)
        
        strategy.on_init(context)
        
        # Bar Data: Price 10.0
        bar = pd.Series({'close': 10.0}, name=date(2023,1,1))
        bar_dict = {'600000': bar}
        
        strategy.on_bar(context, bar_dict)
        
        # Should have bought 1000 shares (default size)
        self.assertEqual(len(pf.trades), 1)
        trade = pf.trades[0]
        self.assertEqual(trade.direction, TradeDirection.BUY)
        self.assertEqual(trade.quantity, 1000)
        self.assertEqual(trade.cost, 10000.0) # 10 * 1000

if __name__ == '__main__':
    unittest.main()