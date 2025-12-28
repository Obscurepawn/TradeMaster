import yaml
from src.config.schema import BacktestConfig, LoggingConfig, ProxyConfig


def load_config(file_path: str) -> BacktestConfig:
    """Loads and validates backtest configuration from a YAML file.

    Args:
        file_path: Filesystem path to the YAML configuration file.

    Returns:
        A validated BacktestConfig object.

    Raises:
        ValueError: If a required configuration field is missing.
    """
    with open(file_path, "r") as f:
        data = yaml.safe_load(f)

    # Basic validation and type conversion
    try:
        baseline = data.get("baseline")
        if baseline is None:
            raise KeyError("baseline")

        if isinstance(baseline, str):
            baseline = [baseline]

        # Parse logging config
        log_data = data.get("logging", {})
        logging_config = LoggingConfig(
            level=log_data.get("level", "INFO"),
            file_path=log_data.get("file_path"),
            console=log_data.get("console", True),
        )

        # Parse proxy config
        proxy_data = data.get("proxy", {})
        proxy_config = ProxyConfig(
            clash_config_path=proxy_data.get("clash_config_path"),
            api_url=proxy_data.get("api_url"),
            api_secret=proxy_data.get("api_secret"),
            selector_name=proxy_data.get("selector_name", "Proxy"),
        )

        return BacktestConfig(
            start_date=data["start_date"],
            end_date=data["end_date"],
            initial_cash=float(data["initial_cash"]),
            strategy_name=data["strategy_name"],
            baseline=baseline,
            universe=data.get("universe"),
            logging=logging_config,
            proxy=proxy_config,
            use_cache=data.get("use_cache", True),
        )

    except KeyError as e:
        raise ValueError(f"Missing required config field: {e}")
