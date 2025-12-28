# Feature Specification: TradeMaster Initial Framework

**Feature Branch**: `001-initial-framework`
**Created**: 2025-12-28
**Status**: Draft
**Input**: User description: Implement TraderMaster script for backtesting with data download, caching, anti-scraping, and interactive plotting.

## Clarifications

### Session 2025-12-28
- Q: What format should be used for the local disk cache? → A: DuckDB
- Q: What is the primary execution frequency for the Strategy interface? → A: Daily (End of Day)
- Q: How should the Clash configuration file path be provided to the system? → A: Environment Variable (CLASH_CONFIG_PATH)
- Q: Which library should be used for interactive charting? → A: Matplotlib
- Q: How should the proxy node be selected during proactive rotation? → A: Randomly

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Backtesting Configuration & Execution (Priority: P1)

The developer defines a backtest configuration via a YAML file, specifying the data source, market, time range, strategy, and benchmarks. They then execute a shell script to run the backtest, which downloads data, runs the strategy logic, and generates a results chart.

**Why this priority**: This is the core MVP loop. Without this, there is no backtesting framework.

**Independent Test**:
1. Create a `config_mvp.yaml` with a simple "Small Market Cap" strategy and "沪深300" benchmark.
2. Run the execution script.
3. Verify that data is downloaded (or cached), the backtest runs without error, and a chart file is generated in a `.gitignore`d directory.

**Acceptance Scenarios**:
1. **Given** a valid YAML config and internet access, **When** the script is run, **Then** it proceeds through Data Download -> Backtest -> Plotting without crashing.
2. **Given** an invalid YAML config (missing fields), **When** the script is run, **Then** it exits immediately with a clear error message.
3. **Given** a strategy requiring specific data fields, **When** the data source schema does not match, **Then** the system throws an error immediately.

---

### User Story 2 - Interactive Results Visualization (Priority: P2)

The developer views the generated backtest results as an interactive chart where they can hover over points to see exact values for the strategy and benchmark yields against time.

**Why this priority**: Quantitative analysis requires precise inspection of data points, not just a static image.

**Independent Test**: Open the generated HTML/interactive chart file in a browser and verify tooltips appear on hover.

**Acceptance Scenarios**:
1. **Given** a completed backtest, **When** the user opens the result chart, **Then** the X-axis shows time, Y-axis shows yield.
2. **Given** the chart is displayed, **When** the mouse hovers over a data point, **Then** a tooltip displays the date and yield value.

---

### User Story 3 - Robust Data Acquisition (Priority: P3)

The system manages data download stability via an HTTP hook that mimics a browser (User-Agent), implements a local disk cache to prevent redundant downloads, and optionally switches proxy nodes (Clash) upon download failures.

**Why this priority**: Ensures reliability and speed. Frequent re-downloads or blocking by data sources renders the tool unusable.

**Independent Test**:
1. Run a data fetch. Verify data is saved to disk.
2. Run the fetch again. Verify no network request is made (cache hit).
3. Simulate a network block (or configure a Clash path). Verify the system attempts to switch nodes/proxy.

**Acceptance Scenarios**:
1. **Given** a fresh run, **When** data is requested, **Then** it is downloaded and cached to disk.
2. **Given** existing cached data (e.g., Akshare daily data), **When** the same data is requested, **Then** the system reads from disk.
3. **Given** a download failure and a valid Clash config, **When** retry logic triggers, **Then** the system attempts to switch the global proxy node.

---

### User Story 4 - Extensible Strategy Development (Priority: P4)

The developer creates a new strategy by implementing logic based on an abstract data schema, defining stock selection, buy/sell signals, and initial capital.

**Why this priority**: The framework must be extensible beyond hardcoded strategies.

**Independent Test**: Implement a dummy "Random Pick" strategy, register it, and run it via config.

**Acceptance Scenarios**:
1. **Given** a new strategy class/function implementing the required interface, **When** referenced in the YAML config, **Then** the backtest engine executes it correctly.

---

### User Story 5 - Anti-Scraping Proxy Layer (Priority: P2)

The system automatically handles anti-scraping measures by routing requests through a local proxy (Clash) and mimicking browser headers (User-Agent). Crucially, it must support **Proactive Proxy Rotation**, switching the proxy node before *every* external request to minimize IP bans.

**Why this priority**: Essential for reliable data acquisition from strict sources.

**Independent Test**:
1. Configure a mock upstream proxy or verify IP change.
2. Verify headers in outgoing requests contain valid User-Agent.
3. **Proactive Rotation Test**:
   - Parse the user's Clash config (`/mnt/c/Users/10255/.config/clash/config.yaml`).
   - Run a test sequence of 3 requests.
   - Verify that `ProxyManager` attempted to switch the node before each request.

**Acceptance Scenarios**:
1. **Given** the proxy module is enabled, **When** any HTTP request is made, **Then** it includes randomized Browser Headers.
2. **Given** Proactive Rotation is enabled, **When** a sequence of requests is made, **Then** the system switches the proxy node *before* each request.
3. **Given** a download failure, **When** retry logic triggers, **Then** the system attempts to switch the proxy node again.

---

### Edge Cases

- **Network Failure**: If data download fails after retries, the process must exit with a clear error, not hang or produce partial results.
- **Data Gaps**: If the requested time range has missing data for a stock, the backtest engine should handle it gracefully (e.g., skip or use last available).
- **Proxy Failure**: If Clash config is provided but the proxy is unresponsive, the system should log the error and attempt to proceed or fail fast depending on configuration.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: System MUST accept a YAML configuration file defining Source, Market, Time Range, Strategy, and Benchmarks.
- **FR-002**: System MUST support `Akshare` and `Tushare` as data sources (via an abstraction layer).
- **FR-003**: System MUST execute the full backtest workflow: Data Fetch -> Strategy Execution -> Result Visualization.
- **FR-004**: System MUST generate an interactive chart using **Matplotlib** (e.g., via interactive backend or basic HTML export) showing Strategy vs. Benchmark yields over time.
- **FR-005**: Results (charts/data) MUST be saved in a directory ignored by git.
- **FR-006**: System MUST implement a global HTTP request hook to spoof User-Agent headers and mimic browser behavior.
- **FR-007**: System MUST implement local disk caching for data using **DuckDB** (Granularity: Stock/Day or Report/Quarter).
- **FR-008**: System MUST support Clash proxy switching via a dedicated Proxy Module.
- **FR-009**: System MUST fail fast with descriptive errors for invalid configs or schema mismatches.
- **FR-010**: System MUST include a `README.md` and `Agents.md` detailing how to add strategies and run the tool.
- **FR-011**: Proxy Module MUST support **Proactive Rotation** using a **Random** node selection strategy before each request.
- **FR-012**: Proxy Module MUST be testable using a Clash config file path provided via the `CLASH_CONFIG_PATH` environment variable.

### Key Entities

- **Strategy**: Encapsulates selection logic, buy/sell rules, and capital management. Operates on a **Daily** (End of Day) frequency.
- **DataSource**: Abstract interface for fetching market data (impl: Akshare, Tushare).
- **BacktestContext**: Holds the state of the simulation (Cash, Positions, History).
- **Config**: Typed representation of the user's YAML input.
- **ProxyManager**: Manages HTTP hooks and Clash API interactions for node switching.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: A user can run a complete backtest (Scenario 1) with a single shell command.
- **SC-002**: Data re-fetching for the same scope takes < 1s (Cache Hit).
- **SC-003**: Visual charts support mouse hover interactions for precise data reading.
- **SC-004**: Test coverage exceeds 70% as per Constitution.
