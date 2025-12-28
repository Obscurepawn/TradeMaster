import unittest
from unittest.mock import MagicMock, patch
from datetime import date
from src.drawing.plotter import Plotter
from src.domain import BacktestResult

class TestPlotter(unittest.TestCase):
    @patch('src.drawing.plotter.plt')
    def test_plot_result(self, mock_plt):
        plotter = Plotter()
        result = BacktestResult(
            total_return=0.1,
            max_drawdown=0.05,
            sharpe_ratio=1.5,
            equity_curve=[10000.0, 10100.0, 11000.0],
            dates=[date(2023,1,1), date(2023,1,2), date(2023,1,3)]
        )
        
        plotter.plot_result(result, output_dir="test_results")
        
        mock_plt.figure.assert_called_once()
        mock_plt.plot.assert_called()
        mock_plt.savefig.assert_called()
        # Ensure directory creation (os.makedirs) - usually needs patching os, but verify savefig path
        args, _ = mock_plt.savefig.call_args
        self.assertIn("test_results", args[0])

if __name__ == '__main__':
    unittest.main()