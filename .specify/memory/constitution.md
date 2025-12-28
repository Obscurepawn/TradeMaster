<!--
Sync Impact Report:
- Version Change: N/A -> 1.0.0 (Initial Ratification)
- Principles Added:
    - Backtesting First (Core Goal)
    - Tech Stack & Standards (Python, Type Hints, English)
    - Real-World Testing (High coverage, Real APIs)
    - Agent-Friendly Documentation (Agents.md, Structured Context)
    - Context-Aware Development (Mandatory Context Loading)
- Sections Added:
    - Module Architecture (src/test separation)
    - Development Workflow
- Templates:
    - .specify/templates/plan-template.md: ✅ Compatible
    - .specify/templates/spec-template.md: ✅ Compatible
    - .specify/templates/tasks-template.md: ✅ Compatible
-->
# TradeMaster Constitution

## Core Principles

### I. Backtesting First
The primary goal of TradeMaster is to provide a robust, quantitative backtesting framework. It must support downloading stock data from third-party sources (A-shares, US shares, HK shares) and executing clear, quantifiable strategies (e.g., "Select top 50 low PE stocks, buy/sell on MACD crossover"). All features must serve this quantifiable, data-driven core.

### II. Tech Stack & Standards
**Language:** Python 3.12+ using `pyvenv` for isolation.
**Typing:** Type hints are MANDATORY for all code.
**Documentation:** Public APIs must use Google Style Docstrings. All comments and documentation must be in English.
**Logging:** Use the built-in `logging` module.

### III. Real-World Testing
**Coverage:** Test coverage must exceed 70%.
**Approach:** Focus on standard execution paths over exhaustive error branching. Tests MUST run against real interfaces and APIs; do NOT use Mocks unless absolutely necessary.
**Tools:** Use the built-in `unittest` framework.

### IV. Agent-Friendly Documentation
Every component directory MUST contain an `Agents.md` file. This file acts as a structured context bridge for Coding Agents, strictly containing:
1. Concise component function introduction.
2. Current implementation details.
3. Testing guide and purpose.
The root `README.md` must adhere to high-quality Github open-source standards (English), explicitly detailing how to run tests and how to add a new strategy.

### V. Context-Aware Development
Before commencing any coding task, the Agent/Developer MUST read all `README.md` and `Agents.md` files relevant to the scope. This ensures a complete understanding of the project's purpose, component responsibilities, and testing status before a single line of code is written.

## Module Architecture

**Structure:**
- Source code resides in `src/`.
- Tests reside in `test/` and must strictly mirror the `src/` directory structure (e.g., `src/data` -> `test/data`).
- **Separation:** Strict modular division with high cohesion and low coupling. Each module must have a single responsibility.

## Development Workflow

1.  **Read Context**: Load `README.md` and relevant `Agents.md`.
2.  **Plan**: Define the strategy or feature with clear, quantifiable rules.
3.  **Implement**: Write code in `src/` with type hints and docstrings.
4.  **Test**: Write real-world tests in `test/` using `unittest`.
5.  **Verify**: Ensure coverage > 70% and all tests pass.

## Governance

This Constitution supersedes all other project documents. Amendments require a documented pull request, justification, and version increment.

**Rules:**
- All PRs must verify compliance with the 5 Core Principles.
- `Agents.md` must be updated with every feature change.
- Complexity must be justified by the quantitative needs of the strategy.

**Version**: 1.0.0 | **Ratified**: 2025-12-28 | **Last Amended**: 2025-12-28