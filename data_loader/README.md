# Data Loader Module

## Overview

The data loader module provides a unified interface for loading stock data from different sources. This module uses the factory pattern to create instances of different types of data loaders.

## Class Structure

### DataLoader (Abstract Base Class)
Base class for all data loaders, defining a unified interface.

### AkshareDataLoader (DataLoader subclass)
Data loader implementation based on the Akshare library.

### DataLoaderFactory
Factory class for creating data loader instances.

#### Methods
- `create_data_loader(loader_type)` - Create a data loader of the specified type

## Usage Example

```python
from data_loader import DataLoaderFactory

# Create Akshare data loader
data_loader = DataLoaderFactory.create_data_loader("akshare")

# Get stock list
stock_list = data_loader.get_stock_list()

# Get historical data for a single stock
history = data_loader.get_stock_history(
    symbol="000001",
    name="Ping An Bank",
    period="daily",
    adjust="qfq",
    start_date="20230807",
    end_date="20250811"
)

# Get financial indicators for a stock
financial_data = data_loader.get_financial_indicators(
    symbol="000001",
    indicator="by_report"
)

# Get shareholder surplus data
shareholder_surplus_data = data_loader.get_shareholder_surplus(
    symbol="000001"
)
```

## Supported Data Sources

Currently supported data sources:
- Akshare (akshare)

Planned support for future data sources:
- Tushare
- Futu
- TongDaXin
- JoinQuant
- ?...?

## Extending with New Data Sources

To add a new data source, you need to:

1. Create a new data loader class that inherits from DataLoader
2. Implement all abstract methods
3. Add support for the new data loader in DataLoaderFactory
