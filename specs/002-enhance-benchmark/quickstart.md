# Quickstart: Baseline Comparison

## Configuration
In your `config.yaml`, you can now specify multiple baselines for comparison:

```yaml
start_date: 2023-01-01
end_date: 2023-06-01
initial_cash: 100000.0
strategy_name: PESmallCap
baseline: 
  - sh000300 # HS300
  - sh000905 # CSI500
universe:
  - sh600000
```

## Running
Execute as usual:
```bash
./scripts/run_backtest.sh
```

## Results
The chart in `results/equity_curve.png` will now show:
1.  **Strategy Equity**: Your strategy's performance.
2.  **sh000300**: HS300 performance.
3.  **sh000905**: CSI500 performance.
All curves are normalized to start at 1.0.
