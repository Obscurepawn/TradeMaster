# Open-source repository publication

- Status: complete
- Owner: Codex
- Updated: 2026-08-24

## Release scope

Publish the local TradeMaster implementation to GitHub `main`. The baseline includes source, tests, shared schemas, public architecture and usage documentation, development specifications, lockfiles, and package metadata. The release work changes how the project is distributed. Backtesting, factor and trading behavior are outside its scope.

- Group the root `.gitignore` rules by data and artifacts, language toolchains, test caches, IDE and OS files, and credentials.
- Provide a root `README.md` covering purpose, capabilities, architecture, repository layout, installation, configuration, usage, verification, limitations and licensing. Include two editable SVGs covering the architecture and end-to-end data flow, checked at high resolution. Identify Tushare as the only external data provider and distinguish it from local runtime dependencies.
- Add the full Apache License 2.0 to `LICENSE`, consistent with the Rust workspace. Complete Python README and license metadata.
- Audit the publication candidates for credentials, ignored material and oversized files. Push the verified commit to `origin/main` by fast-forward.

## Repository rules

Keep Tushare tokens, `.env` and other credentials out of the Git index and history. Exclude provider data, backtest artifacts, build caches, virtual environments, candidate repositories in `research/`, and internal evidence with local paths in `docs/research/`. Retain local data and artifacts on disk. Publish `Cargo.lock` and `uv.lock` for reproducibility, together with the source, schemas, tests, public docs, `AGENTS.md` and `specs/`. Verify that local `main` is based on the latest `origin/main` before pushing. The release preserves history and uses ordinary fast-forward updates.

## Tasks

| Task | Owner | Status | Evidence |
| --- | --- | --- | --- |
| Audit Git state, licensing and publication scope | Codex | completed | Local and remote main started at `fd656a0` with only `.gitignore` tracked. |
| Organize ignore rules and verify the exclusions | Codex | completed | Large data, artifacts, research repositories and caches matched their rules. |
| Write README, license and package metadata | Codex | completed | README, two SVGs, Apache-2.0 and wheel metadata. |
| Run security, documentation, build and test checks | Codex | completed | 248 Python tests passed, along with Rust, static, package, documentation and security checks. |
| Commit and push to origin/main | Codex | completed | Baseline `7b12346` was pushed by fast-forward. |

## Acceptance criteria

- [x] `git status --short` contained only source, tests, public documentation and repository metadata intended for publication.
- [x] Rules explicitly ignored data, artifacts, research, internal research docs, dist, target, virtual environments, caches and secrets.
- [x] README commands, links, architecture and limitations matched the implementation.
- [x] Both SVGs passed structural, rendering and crop inspection. Labels and connectors remained legible at different scales.
- [x] Python and Rust package metadata declared Apache-2.0. The full license was present.
- [x] Secret, file-size, Markdown link, build, test and lint checks passed.
- [x] The working tree was clean after the local main commit, and remote main pointed to the same commit.

## Release decisions

Use the Apache-2.0 license already declared in `Cargo.toml`. Publish `docs/architecture` and general usage guides. Keep `docs/research/` private to the workspace. Document how to regenerate data and reports and where the commands write them.

## Changed files

- `specs/INDEX.md`
- `specs/20260823-open-source-repository-publication.md`
- `.gitignore`
- `README.md`
- `LICENSE`
- `pyproject.toml`
- `Cargo.toml`
- `crates/tm-core/Cargo.toml`
- `crates/tm-engine/Cargo.toml`
- `crates/tm-runner/Cargo.toml`
- `docs/architecture/trademaster-overview.svg`
- `docs/architecture/trademaster-data-flow.svg`
- `docs/full-a-factor-research.md`
- `docs/reviews/20260823-m9-full-a-quality-review.md`
- `python/trademaster/strategies/industry_fundamental/README.md`


## Initial state

The reset local and remote `main` branches both pointed to `fd656a0` with only `.gitignore` tracked. The implementation was untracked. The excluded directories occupied about 3 GB (`data/`), 8.1 GB (`artifacts/`), 5.7 GB (`research/`), 4.8 GB (`target/`) and 504 MB (`.venv/`). There were 155 candidate files. The largest was `uv.lock` at about 245 KB. The candidates contained no copy of the local Tushare token.

## Verification evidence

| Check | Result recorded at publication |
| --- | --- |
| Documentation | Pandoc parsed the README as GitHub Flavored Markdown. All relative links across 44 candidate Markdown files resolved to files eligible for publication. |
| Diagrams | Both passed `validate-svg.sh` and `rsvg-convert`. The reviewer inspected 4000 px renders and 11 cropped details. |
| License and package | `diff -w` matched the full system Apache-2.0 text. The wheel included `LICENSE` and reported `License-Expression: Apache-2.0`. The sdist included the README and license. |
| Security and repository | Scans for the exact local Tushare token and common GitHub, AWS and private-key patterns passed. `git check-ignore -v` confirmed exclusions for the large directories. |
| Python | With a real token, `248 passed in 87.44s`. Ruff, strict Mypy over 44 source files and compileall passed. |
| Rust | Formatting, Clippy with `-D warnings`, workspace tests and doc tests passed. |
| Packaging | `uv build` passed. A clean virtual environment could install the wheel and run both CLI `--help` commands. |
| Remote | Published baseline `7b12346` by fast-forward. Local and remote commits were checked again after the final specification update. |

No open questions remained. The user had authorized the cleanup, commit and push to `main`.
