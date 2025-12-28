import matplotlib.pyplot as plt
from src.domain import BacktestResult
import os

class Plotter:
    def plot_result(self, result: BacktestResult, output_dir: str = "results"):
        if not os.path.exists(output_dir):
            os.makedirs(output_dir)
            
        plt.figure(figsize=(10, 6))
        plt.plot(result.dates, result.equity_curve, label="Strategy Equity", linewidth=2)
        
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
        print(f"Chart saved to {output_path}")
        plt.close()