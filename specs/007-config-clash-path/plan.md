# Implementation Plan: Move Clash Config Path to YAML

**Branch**: `007-config-clash-path` | **Date**: 2025-12-29 | **Spec**: /specs/007-config-clash-path/spec.md
**Input**: Feature specification from `/specs/007-config-clash-path/spec.md`

## Summary
Refactor the Clash configuration path management to use the project's YAML configuration file instead of an environment variable. This involves updating the configuration schema, the loading logic, and the proxy manager initialization.

## Technical Context

**Language/Version**: Python 3.12+
**Primary Dependencies**: `pyyaml`, `requests`
**Storage**: N/A
**Testing**: `unittest`
**Target Platform**: Linux
**Project Type**: Single project
**Performance Goals**: N/A
**Constraints**: Maintain backward compatibility (optional) or fully transition to YAML.
**Scale/Scope**: Impacts configuration management and proxy initialization.

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

1. **Backtesting First**: YES - Proper configuration management supports reliable backtesting.
2. **Tech Stack & Standards**: YES - Python 3.12, dataclasses, type hints.
3. **Real-World Testing**: YES - Will verify that ProxyManager correctly loads proxies from the path in YAML.
4. **Agent-Friendly Documentation**: YES - Will update Agents.md if necessary.
5. **Context-Aware Development**: YES.

## Project Structure

### Documentation (this feature)

```text
specs/007-config-clash-path/
├── plan.md              # This file
├── research.md          # Phase 0 output
├── data-model.md        # Phase 1 output
├── quickstart.md        # Phase 1 output
└── tasks.md             # Phase 2 output
```

### Source Code (repository root)

```text
src/
├── config/
│   ├── schema.py        # Updated: Added ProxyConfig
│   └── settings.py      # Updated: Parsing proxy section
├── proxy/
│   └── manager.py       # Updated: ProxyManager initialization
└── main.py              # Updated: Integration of config into proxy setup
```

**Structure Decision**: No new files, just updating existing configuration and proxy modules.

## Complexity Tracking

N/A