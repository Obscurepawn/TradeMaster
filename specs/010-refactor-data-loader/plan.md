# Implementation Plan: Refactor DataLoader and Decouple Caching

**Branch**: `010-refactor-data-loader` | **Date**: 2025-12-29 | **Spec**: /specs/010-refactor-data-loader/spec.md
**Input**: Feature specification from `/specs/010-refactor-data-loader/spec.md`

## Summary
Decouple caching and remote fetching logic by introducing a generic `DataLoader` that coordinates between a `CacheManager` and a `DataSource` implementation. `AkshareLoader` will be simplified to handle only remote requests, and the `DataLoader` will provide the unified interface for the rest of the system.

## Technical Context

**Language/Version**: Python 3.12+
**Primary Dependencies**: `pandas`, `akshare`, `duckdb`
**Storage**: DuckDB (Centralized)
**Testing**: `unittest`
**Target Platform**: N/A
**Project Type**: Single project
**Performance Goals**: Avoid redundant network calls by efficient gap detection.
**Constraints**: Must maintain the same public interface for data retrieval.
**Scale/Scope**: Refactoring `src/data_loader/` and `src/main.py`.

## Constitution Check

1. **Backtesting First**: YES.
2. **Tech Stack & Standards**: YES.
3. **Real-World Testing**: YES.
4. **Agent-Friendly Documentation**: YES.
5. **Context-Aware Development**: YES.

## Project Structure

### Documentation (this feature)

```text
specs/010-refactor-data-loader/
├── plan.md              # This file
├── research.md          # Phase 0 output
├── data-model.md        # Phase 1 output
├── quickstart.md        # Phase 1 output
└── tasks.md             # Phase 2 output
```

### Source Code (repository root)

```text
src/
├── data_loader/
│   ├── base.py          # New: Generic DataLoader implementation
│   ├── akshare_loader.py # Updated: Simplified, only remote fetch
│   └── cache.py         # Updated: Minor adjustments if needed for decoupling
└── main.py              # Updated: Use the generic DataLoader
```

## Complexity Tracking

N/A