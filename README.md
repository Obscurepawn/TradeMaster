# TradeMaster

TradeMaster is a stock data acquisition and analysis system designed to fetch A-share historical data from websites like Eastmoney and calculate technical indicators.

## Features

- Fetch A-share historical data from Eastmoney website
- Calculate common technical indicators (MA, MACD, RSI, Bollinger Bands, etc.)
- Anti-web-scraping mechanisms (IP proxy rotation, User-Agent rotation, request interval randomization)
- Data storage in CSV format

## Project Structure

```
TradeMaster/
├── config/                # Configuration module
│   ├── config.yaml        # Configuration file
│   ├── config_loader.py   # Configuration loader
│   └── __init__.py        # Package initialization
├── data_loader/           # Data loading module
│   ├── ak_share/          # Data loader based on akshare
│   │   ├── impl.py        # Main data fetching script
│   │   └── constant.py     # Constant definitions
│   └── data_loader.py     # Data loader interface
├── logger/                # Logger module
│   └── logger.py          # Logger utility
├── proxy/                 # Proxy module
│   └── clash/             # Clash proxy controller
│   │   └── proxy.py       # Clash proxy control implementation
├── stock_data/            # Stock data storage directory
├── tests/                 # Unit test directory
│   ├── proxy/             # Proxy module tests
│   ├── data_loader/       # Data loading module tests
│   ├── logger/            # Logger module tests
│   └── run_tests.py       # Test runner script
├── utils/                 # Utility module
│   └── request_hook.py    # HTTP request hook module
├── requirements.txt       # Project dependencies
└── README.md             # Project documentation
```

## Installation

```bash
pip install -r requirements.txt
```

## Usage

```bash
cd data_loader/ak_share
python impl.py
```

## Running Tests

```bash
cd tests
python run_tests.py
```

Or run tests for specific modules:

```bash
# Run proxy module tests
python -m unittest tests.proxy.test_proxy

# Run request hook module tests
python -m unittest tests.data_loader.ak_share.test_request_hook

# Run data loading module tests
python -m unittest tests.data_loader.ak_share.test_stock_data_fetcher

# Run logger module tests
python -m unittest tests.logger.test_logger
```

## Anti-Web-Scraping Mechanisms

This project implements multiple anti-web-scraping avoidance strategies:

1. **IP Proxy Rotation**: Automatically rotate IP proxies using Clash proxy controller
2. **User-Agent Rotation**: Randomly select User-Agent header information
3. **Request Interval Randomization**: Add ±20% random variation to request intervals
4. **HTTP Request Hooks**: Globally intercept and modify HTTP request headers

## Configuration

The project uses a YAML configuration file located at `config/config.yaml`. This file contains settings for:

- Clash proxy settings (config path, host, port, secret)
- Data fetching parameters (start date, end date, retry times, sleep seconds, stock limit)
- Logging configuration

Please update the values in this file according to your environment before running the application.
