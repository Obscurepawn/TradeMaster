import sys
import argparse
from src.config.settings import load_config
from src.data_loader.akshare_loader import AkshareLoader
from src.backtest.engine import BacktestEngine
from src.drawing.plotter import Plotter
from src.strategy.pe_small_cap import PESmallCapStrategy

# Simple Dummy Strategy for MVP if PE not ready
from src.contracts.interfaces import Strategy
class RandomPickStrategy(Strategy):
    def on_init(self, context):
        print("Strategy Initialized")
    def on_bar(self, context, bar_dict):
        pass

STRATEGY_MAP = {
    "RandomPick": RandomPickStrategy,
    "PESmallCap": PESmallCapStrategy
}

def main():
    parser = argparse.ArgumentParser(description="TradeMaster Backtest CLI")
    parser.add_argument("--config", type=str, required=True, help="Path to config.yaml")
    args = parser.parse_args()

    try:
        config = load_config(args.config)
        print(f"Loaded config for strategy: {config.strategy_name}")
        
        loader = AkshareLoader()
        
        # Strategy Loader
        strategy_class = STRATEGY_MAP.get(config.strategy_name)
        if not strategy_class:
            raise ValueError(f"Unknown strategy: {config.strategy_name}")
            
        strategy = strategy_class()
        
        engine = BacktestEngine(config, loader, strategy)
        print("Starting Backtest...")
        result = engine.run()
        
        print(f"Backtest Completed. Total Return: {result.total_return:.2%}")
        
        plotter = Plotter()
        plotter.plot_result(result)
        
    except Exception as e:
        print(f"Error: {e}")
        sys.exit(1)

if __name__ == "__main__":
    main()