# Tasks: Project-wide Codebase Cleanup

## Phase 1: Dead Code Removal
- [X] Remove `RandomPickStrategy` from `src/main.py` and update `STRATEGY_MAP`.
- [X] Scan all files for unused imports and remove them.
- [X] Scan all files for commented-out code blocks and remove them.

## Phase 2: Global Formatting
- [X] Standardize Python file formatting (PEP 8, consistent blank lines).
- [X] Standardize Shell scripts (remove redundant spaces/lines).
- [X] Review and clean up `src/contracts/interfaces.py` and `src/domain.py` docstrings.

## Phase 3: Validation
- [X] Run all tests to ensure zero regressions.
- [X] Run a full backtest with `config_mvp.yaml`.
- [X] Verify test coverage still meets constitution requirements.
