#!/bin/bash

# Test runner script for running all unit tests and saving output to a file

# Create test output directory if it doesn't exist
tests_log_path="./tests/test_output"
mkdir -p ${tests_log_path}

# Run tests and redirect output to a file
echo "Running tests and saving output to ${tests_log_path}/test_results.log..."
python -m tests.run_tests > ${tests_log_path}/test_results.log 2>&1

# Check exit code
if [ $? -eq 0 ]; then
    echo "All tests passed! Check ${tests_log_path}/test_results.log for details."
else
    echo "Some tests failed! Check ${tests_log_path}/test_results.log for details."
fi
