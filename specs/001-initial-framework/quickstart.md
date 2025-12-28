# Quickstart Guide

## Installation
```bash
pip install -r requirements.txt
```

## Configuration (config_mvp.yaml)
```yaml
start_date: 2023-01-01
end_date: 2023-03-01
initial_cash: 100000.0
strategy_name: PESmallCap
baseline: 
  - sh000300 # HS300
  - sh000905 # CSI500
universe:
  - sh600000
```

## Running
```bash
./scripts/run_backtest.sh
```

## Results
- **Console**: View BUY/SELL logs with "Equal Weight" quantities.
- **Chart**: `results/equity_curve.png` showing multiple normalized comparison curves.
- **Performance**: Subsequent runs use **Incremental Caching**, only downloading new date ranges.