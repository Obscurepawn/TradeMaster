import unittest
from unittest.mock import MagicMock
from datetime import date
import pandas as pd
from src.backtest.engine import BacktestEngine
from src.config.schema import BacktestConfig
from src.contracts.interfaces import Strategy
from src.data_loader.base import DataLoader
from src.backtest.portfolio import Portfolio


class TestBacktestEngine(unittest.TestCase):
    def test_run_simple_flow(self):
        # Config
        config = BacktestConfig(
            start_date=date(2023, 1, 1),
            end_date=date(2023, 1, 2),
            initial_cash=10000.0,
            strategy_name="TestStrategy",
            baseline=["sh000300"],
            universe=["sh600000"]
        )

        # Mock DataLoader
        loader = MagicMock(spec=DataLoader)

        d1 = date(2023, 1, 1)
        d2 = date(2023, 1, 2)

        def get_bars(code, start, end):
            # Engine calls get_daily_bars(code, start_date, end_date)
            df = pd.DataFrame({
                'open': [10.0, 10.5],
                'high': [11.0, 12.0],
                'low': [9.0, 10.0],
                'close': [10.5, 11.0],
                'volume': [100, 200]
            }, index=[d1, d2])
            return df

        loader.get_daily_bars.side_effect = get_bars
        loader.get_index_daily.return_value = pd.DataFrame({'close': [1.0, 1.1]}, index=[d1, d2])

        # Mock Strategy
        strategy = MagicMock(spec=Strategy)

        engine = BacktestEngine(config, loader, strategy)
        result = engine.run()

        # Verify strategy hooks called
        strategy.on_init.assert_called_once()
        self.assertEqual(strategy.on_bar.call_count, 2)  # Called for d1 and d2

        # Verify Portfolio updated
        # End price is 11.0. No trades were made (Strategy mock does nothing), so cash is 10000.
        # Total value should be 10000, normalized to 1.0.
        self.assertEqual(result.total_return, 0.0)
        self.assertEqual(len(result.equity_curve), 2)
        self.assertEqual(result.equity_curve[-1], 1.0)


if __name__ == '__main__':
    unittest.main()
