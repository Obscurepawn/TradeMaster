import sys
import argparse
import logging
from src.config.settings import load_config
from src.config.logging_config import setup_logging
from src.data_loader.akshare_loader import AkshareLoader
from src.backtest.engine import BacktestEngine
from src.drawing.plotter import Plotter
from src.strategy.pe_small_cap import PESmallCapStrategy
from src.proxy.hook import init_anti_scraping
from src.proxy.manager import ProxyManager

logger = logging.getLogger(__name__)

STRATEGY_MAP = {
    "PESmallCap": PESmallCapStrategy
}


def main():
    """Main entry point for the TradeMaster Backtest CLI.

    This function orchestrates the following workflow:
    1. Parses command-line arguments.
    2. Loads the backtest configuration from YAML.
    3. Initializes logging.
    4. Initializes the data loader and selected strategy.
    5. Executes the backtest engine.
    6. Displays the total return and plots performance results.
    """
    parser = argparse.ArgumentParser(description="TradeMaster Backtest CLI")
    parser.add_argument("--config", type=str, required=True, help="Path to config.yaml")
    args = parser.parse_args()

    try:
        config = load_config(args.config)
        setup_logging(config.logging)

        # Initialize Anti-Scraping Mechanisms with YAML config
        proxy_manager = ProxyManager(
            api_url=config.proxy.api_url,
            api_secret=config.proxy.api_secret,
            config_path=config.proxy.clash_config_path,
            selector_name=config.proxy.selector_name
        )
        init_anti_scraping(proxy_manager=proxy_manager)

        logger.info(f"Loaded config for strategy: {config.strategy_name}")

        loader = AkshareLoader(use_cache=config.use_cache)

        # Strategy Loader
        strategy_class = STRATEGY_MAP.get(config.strategy_name)
        if not strategy_class:
            raise ValueError(f"Unknown strategy: {config.strategy_name}")

        strategy = strategy_class()

        engine = BacktestEngine(config, loader, strategy)
        logger.info("Starting Backtest...")
        result = engine.run()

        logger.info(f"Backtest Completed. Total Return: {result.total_return:.2%}")

        plotter = Plotter()
        plotter.plot_result(result)

    except Exception as e:
        # Fallback print if logger failed or not initialized
        print(f"CRITICAL ERROR: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
