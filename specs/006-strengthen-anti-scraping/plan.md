# Implementation Plan: Strengthen anti-anti-scraping mechanism

**Branch**: `006-strengthen-anti-scraping` | **Date**: 2025-12-29 | **Spec**: /specs/006-strengthen-anti-scraping/spec.md
**Input**: Feature specification from `/specs/006-strengthen-anti-scraping/spec.md`

## Summary
Strengthen the TradeMaster backtest framework's anti-anti-scraping capability by implementing a saturated and redundant defense mechanism. This includes advanced header rotation, request jitter, and a global hook to ensure all `requests` (including those from `akshare`) are routed through a rotating Clash proxy with proper browser simulation headers.

## Technical Context

**Language/Version**: Python 3.12+
**Primary Dependencies**: `requests`, `akshare`, `pyyaml`
**Storage**: N/A
**Testing**: `unittest`
**Target Platform**: Linux
**Project Type**: Single project
**Performance Goals**: Prioritize bypass success and stability over raw fetching speed.
**Constraints**: Must integrate with local Clash instance; must not break `akshare` internal logic.
**Scale/Scope**: Global impact on all remote data fetching modules.

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

1. **Backtesting First**: YES - High-quality data fetching is the foundation of backtesting.
2. **Tech Stack & Standards**: YES - Python 3.12, type hints, Google Docstrings.
3. **Real-World Testing**: YES - Integration tests will verify proxy and header injection.
4. **Agent-Friendly Documentation**: YES - `src/proxy/Agents.md` will be updated.
5. **Context-Aware Development**: YES - All relevant context files have been analyzed.

## Project Structure

### Documentation (this feature)

```text
specs/006-strengthen-anti-scraping/
├── plan.md              # This file
├── research.md          # Phase 0 output
├── data-model.md        # Phase 1 output
├── quickstart.md        # Phase 1 output
├── contracts/           # Phase 1 output
└── tasks.md             # Phase 2 output (generated later)
```

### Source Code (repository root)

```text
src/
├── proxy/
│   ├── manager.py       # Updated: Advanced Clash rotation and health checks
│   ├── hook.py          # Updated: Global requests monkeypatch implementation
│   └── headers.py       # New: Database of realistic browser headers (UA, Sec-Fetch, etc.)
├── data_loader/
│   └── akshare_loader.py # Updated: Ensure initialization of global hooks
test/
└── proxy/
    ├── test_manager.py
    ├── test_hook.py
    └── test_headers.py  # New: Verify header generation variety
```

**Structure Decision**: Single project structure maintained. New `headers.py` added to `proxy` module to decouple header logic from hook logic.

## Complexity Tracking

> **Fill ONLY if Constitution Check has violations that must be justified**

N/A