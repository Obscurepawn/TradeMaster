import logging
import matplotlib.pyplot as plt
from src.domain import BacktestResult
import os

logger = logging.getLogger(__name__)


class Plotter:
    """Handles visualization of backtest results.

    Generates charts comparing strategy performance against benchmarks.
    """

    def plot_result(self, result: BacktestResult, output_dir: str = "results"):
        """Generates and saves an equity curve plot.

        Args:
            result: The BacktestResult object containing performance data.
            output_dir: Directory where the chart image will be saved.
        """
        if not os.path.exists(output_dir):
            os.makedirs(output_dir)

        plt.figure(figsize=(10, 6))
        plt.plot(result.dates, result.equity_curve,
                 label="Strategy Equity", linewidth=2)

        # Plot baselines
        for code, curve in result.baselines.items():
            plt.plot(result.dates, curve, label=code, linestyle='--')

        plt.title("Backtest Performance Comparison")
        plt.xlabel("Date")
        plt.ylabel("Normalized Yield")
        plt.legend()
        plt.grid(True)

        output_path = os.path.join(output_dir, "equity_curve.png")
        plt.savefig(output_path)
        logger.info(f"Chart saved to {output_path}")
        plt.close()
