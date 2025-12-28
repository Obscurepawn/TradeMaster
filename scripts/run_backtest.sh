#!/bin/bash
# scripts/run_backtest.sh

# Ensure we are in the project root
cd "$(dirname "$0")/.."

# Default to config_mvp.yaml if not provided
CONFIG=${1:-config_mvp.yaml}

export PYTHONPATH="${PYTHONPATH}:$(pwd)"

echo "Running Backtest with config: $CONFIG"
python3 src/main.py --config "$CONFIG"
