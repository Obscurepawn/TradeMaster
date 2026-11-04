# Data and full-market research

For one point-in-time query, start with `DataPortal`. For a multi-year acquisition, start with `FullAResearchPlanner`. Both use Parquet and DuckDB. Each has its own catalog and a different unit of work. The portal asks whether requested keys are covered. The bulk planner records the outcome of each provider request, including verified empty results.

## From a request to a point-in-time table

`DatasetRequest` selects a dataset, universe, fields and date range. `ensure(request)` resolves coverage and fills missing data where a provider is configured. `query(request, as_of=...)` returns an Arrow table after applying the cutoff. `snapshot(SnapshotRequest(...))` records the requests and source objects. `load_snapshot(snapshot_id)` reads the saved manifest. A download date, a statement period end, and the first date its value was available to a strategy can differ. The availability policy controls the last of these. [Python contracts](../../python/trademaster/contracts.py) · [Public data imports](../../python/trademaster/data/__init__.py) · [Data contribution rules](../../python/trademaster/data/AGENTS.md)

| Where to work | Responsibility and diagnostic use |
|---|---|
| [config.py](../../python/trademaster/data/config.py) | `DataConfig` and `DataPaths` resolve the dataset, cache and log locations. Start here for unexpected roots or ETF routing configuration. |
| [registry.py](../../python/trademaster/data/registry.py) | `DatasetSpec` defines fields, key coverage and partitions. `default_dataset_registry` and `normalize_request` give equivalent requests a canonical representation. |
| [coverage.py](../../python/trademaster/data/coverage.py) | `CacheFirstDataPortal` checks catalog coverage before asking a `CanonicalDataProvider` for missing pages. Inspect coverage keys and page boundaries when a request refetches. |
| [catalog.py](../../python/trademaster/data/catalog.py) | `DuckDbCatalog` records object identities and coverage in transactions. Conflicting publication rolls back. |
| [object_store.py](../../python/trademaster/data/object_store.py) | `ParquetObjectStore` verifies and atomically publishes content-addressed tables. The file digest covers the stored bytes. |
| [tushare_provider.py](../../python/trademaster/data/tushare_provider.py) | `TushareProvider` maps canonical requests to provider endpoints and keeps raw and staging evidence for normalized rows. |
| [tushare_replay.py](../../python/trademaster/data/tushare_replay.py) | `validate_tushare_raw_request_scope` and `validate_tushare_staging_replay` compare stored provenance with the actual request and raw values. |
| [status.py](../../python/trademaster/data/status.py) | `latest_status_rows` resolves competing revisions of a daily trading status. |
| [research.py](../../python/trademaster/data/research.py) | `ResearchDataSource` returns a `ResearchQueryResult` with `ResearchObjectEvidence`. `TushareQueryClient` describes the provider call made by the research cache. |

Tests isolate [request validation](../../tests/test_contract_validation.py), [paths and field registration](../../tests/test_data_config_registry.py), [coverage and pagination](../../tests/test_coverage_portal.py), [catalog transactions](../../tests/test_duckdb_catalog.py), [object integrity](../../tests/test_parquet_object_store.py), [as-of snapshot reads](../../tests/test_pit_snapshot.py) and [provider normalization and source replay](../../tests/test_tushare_provider.py).

## From a download plan to an evaluation

A plan names the instruments, sessions and requests. Its SHA identifies that set. A completed task also names the hash of its Parquet file. Panel manifests bind the acquired data to derived observations and labels. Evaluation uses those manifests to identify its inputs. This lets a researcher follow a reported metric back to the underlying request. [Research package](../../python/trademaster/research/__init__.py) · [Research contribution rules](../../python/trademaster/research/AGENTS.md) · [Walkthrough and historical results](../full-a-factor-research.md)

| Where to work | Responsibility and diagnostic use |
|---|---|
| [full_a.py](../../python/trademaster/research/full_a.py) | `FullAResearchConfig` specifies scope. `FullAResearchPlanner` produces the `DownloadPlanManifest` and its `DownloadTask` records. `FullAResearchDownloader` resumes the work. `FullAResearchStore` maintains state and returns completed evidence. Distinguish blocked access, an empty response and an unfinished task. |
| [tushare.py](../../python/trademaster/research/tushare.py) | `RateLimitedTushareClient` spaces calls and classifies retryable provider errors. `CacheOnlyTushareClient` supports paths that must use previously acquired inputs. |
| [full_a_panel.py](../../python/trademaster/research/full_a_panel.py) | `FactorResearchPanelBuilder` builds a dense instrument/session panel, observation dates, public factor values and future-return labels. Coverage counts separate a known suspension from an unexplained missing bar. |
| [full_a_fundamental.py](../../python/trademaster/research/full_a_fundamental.py) | `FullAFundamentalPanelBuilder` combines statement versions and industry intervals with a panel. The manifest records which fundamental factors had enough evidence to produce output. |
| [factor_evaluation.py](../../python/trademaster/research/factor_evaluation.py) | `FullAForwardReturnBuilder` and `ForwardReturnPolicy` resolve entry, horizon and invalid labels. `FullAFactorEvaluator` is the small-table reference for universe coverage, correlations and quantiles. |
| [factor_evaluation_duckdb.py](../../python/trademaster/research/factor_evaluation_duckdb.py) | `DuckDBFullAFactorEvaluator` runs the same diagnostics against large Parquet inputs without loading them all into Python memory. |
| [factor_report.py](../../python/trademaster/research/factor_report.py) | `FullAFactorReportStore` publishes metrics, manifests and a self-contained report. `FullAFactorReportIdentity` binds inputs and evaluation settings. |
| [Command dispatcher](../../python/trademaster/research/__main__.py) | Maps CLI arguments to plan, acquire, verify, materialize, evaluate and report verification operations. [Command reference](commands.md) |

Regression coverage includes [plan identity and resumption](../../tests/test_full_a_research.py), [client retry behavior](../../tests/test_research_tushare_client.py), [dense panels](../../tests/test_full_a_panel.py), [statement availability](../../tests/test_full_a_fundamental.py), [forward returns and coverage](../../tests/test_factor_evaluation_v2.py), [DuckDB agreement with the reference](../../tests/test_factor_evaluation_duckdb.py) and [report identities and integrity](../../tests/test_factor_report_v2.py).
