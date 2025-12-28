# Research: TradeMaster Framework Implementation

## Decisions & Rationale

### 1. Data Storage: DuckDB
- **Decision**: Use DuckDB for local disk caching of daily stock data.
- **Rationale**: 
    - **Performance**: Columnar storage is optimized for time-series analytical queries (e.g., "Get all closes for Stock X in Range Y").
    - **Efficiency**: Better compression than CSV/JSON.
    - **Usability**: Single-file database (easier than setting up Postgres/MySQL for a local tool).
- **Alternatives Considered**: 
    - *CSV*: Too slow for parsing large datasets, no query capability.
    - *SQLite*: Good, but DuckDB is faster for OLAP-style workloads (backtesting).
    - *Pickle*: Python-version dependent and security risk.

### 2. Charting Library: Matplotlib
- **Decision**: Use Matplotlib for generating backtest result charts.
- **Rationale**: 
    - **Standard**: The de-facto standard in Python data science; robust and widely understood.
    - **Stability**: Mature and less likely to break than newer web-based wrappers for a core CLI tool.
    - **Requirements**: Capable of saving high-quality static images or interactive GUI windows (Backend dependent).
- **Alternatives Considered**: 
    - *Plotly*: Better web interactivity, but adds a large dependency and HTML output management complexity.
    - *Bokeh*: Overkill for simple equity curves.

### 3. Proxy Rotation: Random Selection
- **Decision**: Implement a Random selection strategy for proactive proxy rotation from the Clash config.
- **Rationale**: 
    - **Simplicity**: Easiest to implement and stateless.
    - **Effectiveness**: Sufficiently reduces the probability of sequential requests coming from the same IP, satisfying the anti-scraping requirement.
- **Alternatives Considered**: 
    - *Round-Robin*: Requires shared state if parallelized (though currently single-threaded).
    - *Least-Used*: Over-engineering for a local single-user tool.

### 4. Configuration: Environment Variables & YAML
- **Decision**: Use `CLASH_CONFIG_PATH` env var for infrastructure paths, and `config.yaml` for backtest run definition.
- **Rationale**: Separates "User Environment" (machine-specific) from "Backtest Logic" (shareable strategy config).
- **Alternatives Considered**: Hardcoding paths (Fragile), Monolithic config (Mixes secrets with logic).

### 5. Strategy Frequency: Daily
- **Decision**: The engine will operate on a daily (End-of-Day) tick loop.
- **Rationale**: Matches the resolution of free data sources (Akshare/Tushare) and simpler strategies suitable for an initial framework.
- **Alternatives Considered**: Minute/Tick (Data acquisition becomes the bottleneck).

## Dependencies Review
- `akshare`: No API key required, good A-share coverage.
- `requests`: Standard for HTTP.
- `duckdb`: Binary wheel available for Linux.
- `pyyaml`: Standard for YAML.
- `matplotlib`: Standard for plotting.

## Conclusion
The stack is solid, standard, and fits the "Backtesting First" principle by prioritizing data access and analytical capability.
