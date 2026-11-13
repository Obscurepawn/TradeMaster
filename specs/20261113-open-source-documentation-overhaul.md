# Open-Source Documentation and Repository Governance Overhaul

**Status:** Completed | **Date:** 2026-11-13 | **Scope:** Empirical study isolation (`studies/`), open-source governance (`CONTRIBUTING.md`, `.github/workflows/ci.yml`, package/crate metadata), documentation stratification (`README.md`, `docs/`, `AGENTS.md`), and RFC standardization (`specs/`) | **Updates:** [Initial Open-Source Repository Publication](20260823-open-source-repository-publication.md)

## Motivation and Scope

As TradeMaster expanded beyond its initial A-share release to support native Python/Rust snapshot execution, configurable cross-market trading rules, and IndexOne data ingestion, three repository-level documentation and governance issues accumulated:

1. **Core framework vs. exploratory study coupling:** Exploratory single-project US equity experiments and ad-hoc notes were intermixed with core framework specifications and modules rather than isolated outside the tracked engine and SDK boundary.
2. **Missing open-source contributor & CI scaffolding:** Human contributor workflows, automated GitHub Actions verification gates, and complete crate/package metadata needed first-class entry points distinct from coding-agent instructions.
3. **Repetitive prose and internal session artifacts:** Documentation across `README.md`, `docs/`, `AGENTS.md`, and `specs/` contained repetitive rule recaps, choppy staccato phrasing, inconsistent `Tushare` capitalization, and internal development-session artifacts (such as agent owner headers, conversational approval notes, and raw test-collection error logs).

This specification brings TradeMaster's repository layout, contributor governance, reference documentation, and architectural specifications up to open-source engineering standards without modifying runtime trading, accounting, or factor calculation behavior.

## Architectural and Editorial Decisions

### 1. Isolation of Exploratory Empirical Studies (`studies/`)

Exploratory market studies and strategy experiments that are not part of the supported `trademaster` SDK or canonical test/benchmark suite (including 26 exploratory US equity study documents archived under `studies/us-equities-202611/`) are isolated under the gitignored `studies/` directory (`.gitignore`). Core `python/trademaster/`, `crates/`, `docs/`, and `specs/` retain only reusable framework components, verified benchmarks, and canonical reference examples.

### 2. Audience Stratification Across Repository Documentation

To eliminate duplicated rule explanations and keep each document focused on a single reader persona, repository documentation is stratified across five clear surfaces:

| Surface | Primary Audience | Responsibility |
|---|---|---|
| [`README.md`](../README.md) | Evaluators & new users | Concise project overview, quickstart examples, architecture summary, and entry links into `docs/`. |
| [`CONTRIBUTING.md`](../CONTRIBUTING.md) & [`.github/workflows/ci.yml`](../.github/workflows/ci.yml) | Human open-source contributors | Local toolchain setup (`uv`, `maturin`, Cargo), test/lint/type-check commands, pull-request checklist, and automated CI enforcement. |
| [`AGENTS.md`](../AGENTS.md) & subsystem `AGENTS.md` files | Coding agents & maintainers | Non-negotiable domain invariants (causality, data snapshots, ledger exactness, secret isolation) and spec-first TDD workflow rules. |
| [`docs/`](../docs/README.md) (`docs/reference/`, `docs/examples/`) | Practitioners & strategy authors | Authoritative API, execution, CLI, and data-source references paired with runnable walkthroughs and benchmark guides. |
| [`specs/`](INDEX.md) | Core maintainers & reviewers | Architectural RFCs, subsystem contracts, benchmark definitions, and milestone verification/regression records. |

### 3. Terminology and Package Metadata Standardization

- **Canonical terminology:** Standardized **`Tushare`** / **`Tushare Pro`** and **`A-share`** / **`A-shares`** spelling across documentation and specifications, while preserving exact **data snapshot** (`as_of` / `decision_at`), close-to-next-open causality, and separate order-sizing vs. cash-reservation descriptions.
- **Package & crate metadata:** Completed human-facing metadata across `pyproject.toml` and workspace `Cargo.toml` manifests so Python wheels, source distributions, and Rust crates carry consistent descriptions, license identifiers (`Apache-2.0`), and repository URLs.

### 4. Uniform RFC / ADR Standards in `specs/`

- Reorganized [`specs/INDEX.md`](INDEX.md) from an uncategorized chronological list into logical thematic sections (**Active**, **Core Architecture & Execution RFCs**, **Performance & Execution Benchmarks**, **Factor Library, Research Pipeline & Reference Strategies**, and **Foundational Milestones & Release**) with one-sentence summaries for every specification.
- Standardized all 21 existing specifications under uniform `**Status:** ... | **Date:** ... | **Scope:** ...` metadata headers with explicit `**Updates:**` / `**Updated-by:**` cross-links (notably linking [Code Architecture Refactoring](20261104-code-architecture-refactor.md) and [Python and Rust Native Backtest Interface](20261105-python-rust-backtest-interface.md)), removed all internal agent/session chatter, and preserved 100% of factual design decisions, review findings, digests, benchmark numbers, and relative file links.

## Changed Files

- **Governance, CI & packaging:** `.gitignore`, `CONTRIBUTING.md`, `.github/workflows/ci.yml`, `pyproject.toml`, `Cargo.toml`, `crates/*/Cargo.toml`
- **Agent & contributor guides:** `AGENTS.md`, `crates/**/AGENTS.md`, `python/**/AGENTS.md`, `tests/AGENTS.md`, `.skills/trademaster-backtest/`
- **User & reference documentation:** `README.md`, `docs/README.md`, `docs/architecture/overview.md`, `docs/factor-management.md`, `docs/public-factor-library.md`, `docs/full-a-factor-research.md`, `docs/reference/*.md`, `docs/examples/*.md`
- **Specifications & RFCs:** `specs/INDEX.md`, `specs/20261113-open-source-documentation-overhaul.md`, and all 21 existing `specs/*.md` records

## Verification

- **Specification & RFC scope checks:**
  - Verified that [`specs/INDEX.md`](INDEX.md) indexes all 22 specification files in `specs/` with matching status classifications.
  - Verified zero remaining occurrences of internal agent owner markers, conversational approval artifacts, or inconsistent `Tushare` casing across `specs/*.md`.
  - Verified that all relative Markdown links and heading anchors across `specs/*.md` (`../README.md`, `../CONTRIBUTING.md`, `../AGENTS.md`, `../docs/...`, `../python/...`, `../crates/...`, `../tests/...`, `examples/...`, `assets/...`) resolve on disk.
- **Full-repository test, lint, type, build, and link verification:**
  - **Markdown & anchor audit (`69` files across `README.md`, `CONTRIBUTING.md`, `AGENTS.md`, `docs/`, `schemas/`, `specs/`, and `.skills/`):** `0` broken file links or heading anchors, `0` inconsistent `TuShare` occurrences, and `0` conversational session artifacts; `git diff --check` passed cleanly.
  - **Runnable `README.md` verification:** Executed `specs/examples/blocked_rotation.py` (`CNY 91,990.00` final equity), `specs/examples/us_stock_backtest.py` (`9924.00 USD` final equity), the self-contained inline `CloseMomentum` rerun (`completed: 2 fills, 9924.00 USD`), and `tm-replay` against the saved snapshot request.
  - **Python quality gates:** `uv run ruff check python tests specs/examples` passed; `uv run mypy --strict python tests` passed across `107` source files; `uv run pytest -q` passed (`445 passed, 3 skipped` for live `TUSHARE_TOKEN`-gated provider checks).
  - **Rust quality gates:** `cargo fmt --all -- --check`, `cargo clippy --locked --workspace --all-targets -- -D warnings`, and `cargo test --locked --workspace` (`67` tests + doc-tests) all passed with zero warnings.
