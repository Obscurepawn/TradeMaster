# Feature Spec: Project-wide Codebase Cleanup

## Clarifications
### Session 2025-12-29
- Q: Which directories should be excluded from the project-wide cleanup? → A: Exclude `.gemini/`, `.specify/`, and `.git/` from cleanup.
- Q: Which cleanup strategy/tooling should be used? → A: Use a formatting tool (e.g., autopep8).
- Q: How should commented-out code blocks be handled? → A: Remove ALL commented-out code blocks (keep only TODOs/Docs).
- Q: What is the priority for logic changes during cleanup? → A: Strict functional parity (Backtest results must remain identical).
- Q: Should documentation like `Agents.md` be updated? → A: Update all `Agents.md` files to reflect the cleaned structure.

## User Requirements
- Remove all unnecessary code, including unused variables, functions, dead code, and abandoned interfaces/design artifacts.
- Review all files for proper formatting using automation tools where applicable.
- Remove redundant empty lines.
- Ensure consistent code style across the project.

## Current State
- `src/proxy/hook.py`: Contains `RequestHook` class which was superseded by `GlobalRequestHook` but might still be there.
- `src/main.py`: Contains a `RandomPickStrategy` dummy class which might be unnecessary if we only use `PESmallCapStrategy`.
- Some files might have inconsistent indentation or excessive blank lines due to multiple iterations of changes.
- `src/contracts/interfaces.py`: Might have imports or methods no longer in use.

## Goals
1. Identify and remove all dead code (unused imports, variables, functions, classes).
2. Clean up `src/contracts/interfaces.py` to match only what is currently used.
3. Standardize file formatting (Python: PEP 8 using tools like `autopep8`, Shell: standard bash).
4. Remove excessive blank lines (more than 1 between logic blocks, more than 2 between top-level classes/functions).
5. Verify that the project still builds and tests pass after cleanup.
6. **Scope**: The cleanup applies to `src/`, `test/`, `scripts/`, and root configuration files (e.g., `config_mvp.yaml`, `README.md`). Operations must EXCLUDE `.gemini/`, `.specify/`, and `.git/` directories.
7. **Comment Policy**: Remove ALL commented-out code blocks during the cleanup. Only documentation comments and explicitly marked TODOs should be retained.
8. **Integrity**: Maintain strict functional parity. Backtest results generated before and after cleanup must be identical.
9. **Documentation**: Synchronize all `Agents.md` and `README.md` files with the final cleaned codebase structure and implementation details.