# Initial Open-Source Repository Publication

**Status:** Completed | **Date:** 2026-08-24 | **Scope:** Apache-2.0 licensing, ignore policies, package metadata, architecture diagrams, and initial public `main` baseline | **Updated-by:** [Open-Source Documentation and Repository Governance Overhaul](20261113-open-source-documentation-overhaul.md)

## Motivation and Scope

This specification records the initial open-source publication of TradeMaster to GitHub `main` (advancing from the bare `.gitignore` commit `fd656a0` to baseline commit `7b12346`). The release packaged source code, tests, shared Arrow/JSON schemas, architecture diagrams, public documentation, engineering specifications, lockfiles (`Cargo.lock`, `uv.lock`), and Apache-2.0 metadata without altering backtesting, factor, or execution behavior.

## Repository Packaging and Hygiene Rules

- **Ignore boundaries (`.gitignore`):** Grouped ignore patterns across local provider caches and backtest outputs (`data/`, `artifacts/`), reference repositories and internal research notes (`research/`, `docs/research/`), Python and Rust build artifacts (`.venv/`, `dist/`, `target/`), IDE/OS metadata, and secret files (`.env`, token files).
- **Licensing and metadata:** Added the full Apache License 2.0 text in `LICENSE` to match the Rust workspace (`Cargo.toml`) and updated `pyproject.toml` so built wheels and source distributions embed `LICENSE`, `README.md`, and `License-Expression: Apache-2.0`.
- **Public documentation and SVG diagrams:** Authored `README.md` covering system architecture, layout, installation, `TUSHARE_TOKEN` configuration, CLI usage, verification, and known modeling limits, paired with two editable high-resolution diagrams (`docs/architecture/trademaster-overview.svg` and `docs/architecture/trademaster-data-flow.svg`).
- **Credential and size audit:** Verified all 155 tracked files (largest file: `uv.lock` at ~245 KB) while keeping ~22.1 GB of local workspace directories (`data/` ~3.0 GB, `artifacts/` ~8.1 GB, `research/` ~5.7 GB, `target/` ~4.8 GB, `.venv/` ~504 MB) untracked on disk.

## Changed Files

- **Governance & packaging:** `.gitignore`, `LICENSE`, `README.md`, `pyproject.toml`, `Cargo.toml`, `crates/tm-core/Cargo.toml`, `crates/tm-engine/Cargo.toml`, `crates/tm-runner/Cargo.toml`
- **Architecture & documentation:** `docs/architecture/trademaster-overview.svg`, `docs/architecture/trademaster-data-flow.svg`, `docs/full-a-factor-research.md`, `docs/reviews/20260823-m9-full-a-quality-review.md`, `python/trademaster/strategies/industry_fundamental/README.md`
- **Specifications:** `specs/INDEX.md`, `specs/20260823-open-source-repository-publication.md`

## Verification Record

| Verification Area | Outcome at Publication (`7b12346`) |
|---|---|
| Markdown & relative links | `pandoc` parsed `README.md` cleanly as GitHub Flavored Markdown; every relative link across all 44 tracked Markdown files resolved to a tracked target. |
| Architecture diagrams | Both SVGs passed structural validation (`validate-svg.sh`) and `rsvg-convert` rendering, verified at 4000px full width and across 11 zoomed detail crops. |
| License & distribution builds | `diff -w` matched the canonical Apache-2.0 text; `uv build` produced sdist and wheel artifacts carrying `License-Expression: Apache-2.0`, verified by installing the wheel into a clean virtual environment and running CLI `--help` entry points. |
| Secret & ignore audits | Exact-match scans for the local `TUSHARE_TOKEN` and pattern scans for GitHub, AWS, and PEM private keys returned zero matches; `git check-ignore -v` verified exclusion of all data, artifact, research, and build directories. |
| Python & Rust test suites | Python passed 248 tests in `87.44s` (with live Tushare token isolated to the test process), Ruff, `compileall`, and strict `mypy` (44 source files); Rust passed `cargo fmt --check`, `cargo clippy -D warnings`, workspace tests, and doc-tests prior to fast-forward push to `origin/main`. |
