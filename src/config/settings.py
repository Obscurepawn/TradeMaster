import yaml
from datetime import datetime
from src.config.schema import BacktestConfig, LoggingConfig


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

        return BacktestConfig(
            start_date=data["start_date"],
            end_date=data["end_date"],
            initial_cash=float(data["initial_cash"]),
            strategy_name=data["strategy_name"],
            baseline=baseline,
            universe=data.get("universe"),
            logging=logging_config,
        )

    except KeyError as e:
        raise ValueError(f"Missing required config field: {e}")
