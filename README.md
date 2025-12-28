# TradeMaster

TradeMaster is a quantitative trading backtesting framework.

## Quickstart

1. **Install Dependencies**:
   ```bash
   pip install -r requirements.txt
   ```

2. **Run Demo Backtest**:
   ```bash
   ./scripts/run_backtest.sh
   ```

3. **View Results**:
   Check `results/equity_curve.png`.

## Configuration
Edit `config_mvp.yaml` to change:
- Start/End Dates
- Strategy (`PESmallCap` or `RandomPick`)
- Universe (Stock Codes)
- Initial Cash

## Modules
- **Backtest**: Core engine.
- **Data Loader**: Akshare integration with DuckDB caching.
- **Proxy**: Clash integration for anti-scraping.
- **Strategy**: Extensible strategy interface.

## Testing
Run unit tests via:
```bash
./scripts/run_tests.sh
```