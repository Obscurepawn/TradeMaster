#!/bin/bash
# scripts/clear_cache.sh
# Deletes the local DuckDB database to clear all cached market data.

# Ensure we are in the project root
cd "$(dirname "$0")/.."

DB_PATH="data/market_data.duckdb"

if [ -f "$DB_PATH" ]; then
    echo "Clearing DuckDB cache at $DB_PATH..."
    rm "$DB_PATH"
    echo "Cache cleared successfully."
else
    echo "No cache found at $DB_PATH. Nothing to clear."
fi
