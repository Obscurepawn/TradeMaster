# Quickstart: Using the New DataLoader

## Standard Usage
The `DataLoader` now acts as a high-level manager. You inject a specific `DataSource` (like `AkshareSource`) and it handles the caching for you.

```python
from src.data_loader.base import DataLoader
from src.data_loader.akshare_loader import AkshareSource
from src.data_loader.cache import CacheManager

# 1. Setup components
cache = CacheManager()
source = AkshareSource()

# 2. Initialize manager
loader = DataLoader(source, cache, use_cache=True)

# 3. Use as before
df = loader.get_daily_bars("sh600000", start_date, end_date)
```

## Creating a New Source
Simply implement the `DataSource` interface:

```python
class MyNewSource(DataSource):
    def fetch_daily_bars(self, code, start, end):
        # Your custom fetch logic
        return df
```
Then pass it to `DataLoader`. No need to touch caching code!
