# Quickstart Guide

## Prerequisites

- **OS**: Linux / macOS (Windows w/ WSL)
- **Python**: 3.12+
- **Git**
- **Clash** (Optional, for proxy support)

## Installation

1. **Clone the repository**:
   ```bash
   git clone <repo-url>
   cd TradeMaster
   ```

2. **Set up Virtual Environment**:
   ```bash
   python3 -m venv .venv
   source .venv/bin/activate
   ```

3. **Install Dependencies**:
   ```bash
   pip install -r requirements.txt
   ```

4. **(Optional) Configure Proxy**:
   If using Clash for anti-scraping:
   ```bash
   export CLASH_CONFIG_PATH="/path/to/your/config.yaml"
   ```

## Running the Demo

1. **Execute the run script**:
   ```bash
   ./scripts/run_backtest.sh
   ```
   *Note: The first run will be slow as it downloads data. Subsequent runs will use the local DuckDB cache.*

2. **View Results**:
   The script will generate a plot file (check script output for location, typically `results/`).
   Open this file to view the Equity Curve.

## Development

### Running Tests
```bash
./scripts/run_tests.sh
```

### Adding a New Strategy
1. Create a new file in `src/strategy/` (e.g., `my_strategy.py`).
2. Inherit from `src.strategy.base.Strategy`.
3. Implement `on_init()` and `on_bar()`.
4. Update `config.yaml` to reference your strategy class.

## Project Layout

- `src/`: Source code
  - `data_loader`: Data fetching and caching (DuckDB).
  - `strategy`: Strategy logic.
  - `backtest`: Execution engine.
  - `proxy`: Anti-scraping logic.
- `scripts/`: Operational scripts.
- `test/`: Unit tests.
