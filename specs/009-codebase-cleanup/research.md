# Research: Project-wide Codebase Cleanup

## Decision: Manual Removal of Dead Code
**Rationale**: The codebase is compact enough for reliable manual inspection using `grep` and `search_file_content`. Automatic tools like `vulture` might flag valid strategy implementations if they are only referenced via strings in config files (which they are in `STRATEGY_MAP`).
**Findings**:
- `RandomPickStrategy` in `src/main.py` is a placeholder and can be removed.
- `src/proxy/hook.py` had redundant `RequestHook` which was already removed in previous turns.
- `src/proxy/manager.py` had `load_proxies_from_file` which was also removed.

## Decision: Formatting Standard
**Rationale**: Adhere to PEP 8 for Python and standard shell script formatting.
**Rules**:
- Remove double blank lines between methods.
- Ensure 2 blank lines before top-level classes/functions.
- Remove trailing whitespace.
- Remove redundant imports.

## Decision: Data Model and Contracts
**Rationale**: Existing interfaces in `src/contracts/interfaces.py` and models in `src/domain.py` are strictly what's needed for the current backtesting engine. No changes needed to the core design, just formatting.
