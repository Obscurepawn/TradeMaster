import yaml
from datetime import datetime
from src.config.schema import BacktestConfig

def load_config(file_path: str) -> BacktestConfig:
    with open(file_path, 'r') as f:
        data = yaml.safe_load(f)
    
    # Basic validation and type conversion
    try:
        return BacktestConfig(
            start_date=data['start_date'], # yaml parser handles YYYY-MM-DD as date objects usually, or we parse
            end_date=data['end_date'],
            initial_cash=float(data['initial_cash']),
            strategy_name=data['strategy_name'],
            benchmark=data['benchmark'],
            universe=data.get('universe')
        )
    except KeyError as e:
        raise ValueError(f"Missing required config field: {e}")