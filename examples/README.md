# TradeMaster Examples

This directory contains example scripts demonstrating how to use various modules of the TradeMaster project.

## Configuration

All examples use the configuration system provided by TradeMaster. The configuration files are located in the `examples/config/` directory. Examples will first look for configuration in `examples/config/config.yaml`, and if not found, will fall back to the project root configuration.

To customize the configuration for examples, modify the `examples/config/config.yaml` file.
## Directory Structure

- `proxy/clash/` - Examples for Clash proxy controller
- `data_loader/ak_share/` - Examples for Chinese A-share stock data loading

## Usage

To run any example script, execute it from the project root directory:

```bash
# Run proxy example
python examples/proxy/clash/proxy_example.py

# Run data loader example
python examples/data_loader/ak_share/data_fetcher_example.py
```

## Examples

### Proxy Controller Example
- File: `proxy/clash/proxy_example.py`
- Demonstrates how to:
  - Parse Clash configuration file
  - Connect to Clash API
  - Get available proxy nodes
  - Switch to a random proxy node

### Stock Data Fetcher Example
- File: `data_loader/ak_share/data_fetcher_example.py`
- Demonstrates how to:
  - Get list of Chinese A-share stocks
  - Fetch historical data for stocks
  - Calculate technical indicators
  - Save data to CSV files
  - Use proxy rotation during data fetching

### Stock Information Example
- File: `data_loader/ak_share/stock_info_example.py`
- Demonstrates how to:
  - Get basic information for a stock (price, market cap, industry, etc.)
  - Get detailed profile information for a stock (company profile, business scope, etc.)
