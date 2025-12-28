import yaml
from datetime import datetime
from src.config.schema import BacktestConfig

def load_config(file_path: str) -> BacktestConfig:
    with open(file_path, 'r') as f:
        data = yaml.safe_load(f)
    
    # Basic validation and type conversion
    try:
        baseline = data.get('baseline')
        if baseline is None:
            raise KeyError('baseline')
            
        if isinstance(baseline, str):
            baseline = [baseline]

        return BacktestConfig(
            start_date=data['start_date'],
            end_date=data['end_date'],
            initial_cash=float(data['initial_cash']),
            strategy_name=data['strategy_name'],
            baseline=baseline,
            universe=data.get('universe')
        )
    except KeyError as e:
        raise ValueError(f"Missing required config field: {e}")