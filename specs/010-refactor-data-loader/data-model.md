# Data Model & Contracts: DataLoader Refactoring

## 1. DataSource (Interface Refinement)
Located in `src/contracts/interfaces.py`.

```python
class DataSource(ABC):
    @abstractmethod
    def fetch_daily_bars(self, code: str, start_date: date, end_date: date) -> pd.DataFrame:
        """Fetch raw data from remote."""
        pass

    @abstractmethod
    def fetch_index_daily(self, code: str, start_date: date, end_date: date) -> pd.DataFrame:
        """Fetch index data from remote."""
        pass
```

## 2. DataLoader (Generic Orchestrator)
Located in `src/data_loader/base.py`.

**Fields**:
- `data_source: DataSource`
- `cache: CacheManager`
- `use_cache: bool`

**Methods**:
- `get_daily_bars(code, start, end)`: Orchestration logic.
- `get_index_daily(code, start, end)`: Orchestration logic.
- `_fill_gaps(...)`: Common internal gap filling logic.
