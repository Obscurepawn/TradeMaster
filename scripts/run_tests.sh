#!/bin/bash
# scripts/run_tests.sh
# Discovers and runs all tests in the test/ directory

# Ensure we are in the project root
cd "$(dirname "$0")/.."

echo "Running TradeMaster Test Suite..."
python3 -m unittest discover -s test -p "test_*.py" -v
