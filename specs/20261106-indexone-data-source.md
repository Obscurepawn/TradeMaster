# IndexOne data source

Status: completed.

## Purpose and scope

Researchers can download IndexOne end-of-day prices, company fundamentals,
security reference data and FX observations, keep immutable Parquet evidence,
and reuse the same request offline. The source implements the existing
`ResearchDataSource` interface. A CLI and README example provide the first call.

This does not replace the TuShare canonical provider or relax its provenance
checks. IndexOne rows are provider observations, not proof of historical trading
permission, a complete exchange calendar or original publication/revision times.
The daily snapshot loader still supplies that evidence before running a strategy.
Index creation, simulation, account changes and delivery operations are outside
this data-source integration.

## Provider contract

The official [authentication guide](https://indexone.io/docs/start/authentication)
and [execution reference](https://indexone.io/docs/reference/execution-engine),
checked against the public [operation catalog](https://api.indexone.io/schema),
define the request. The catalog is authoritative for current operation names,
parameters, projections and output types.

- Only `i1_core_eod`, `i1_core_fundamentals`, `core_securities_reference` and
  `i1_core_fx` are allowed. Public aliases are `eod`, `fundamentals`, `securities`
  and `fx`.
- A single read operation is sent to `POST /execute` without `websocket_id` or
  `team_id`. The secret value from `INDEXONE_API_KEY` is sent as `x-api-key`.
  The key ID is management metadata and is not an authentication credential.
- Validate projections and parameters against `GET /schema`. Core filters use
  `field`, `operator`, `value`; projections use `attributes`. Unsupported schema
  shapes and provider errors fail explicitly. Empty rows remain empty results.
- Accept both the documented `operations` row-array envelope and the live
  operation-ID-keyed envelope with column arrays. Live verification returned
  the latter. Require the requested node identity and equal column lengths;
  reject unfinished, failed, suspended or truncated results.
- `/execute` has no documented pagination. `page_limit` is a local response-row
  bound for this implementation; exceeding it fails without publishing a partial
  result. Users split large queries by instrument or date. Never request the
  streaming mode that truncates inline output.
- Use bounded retries for transient HTTP failures, and fail immediately on
  credential/permission and invalid-request failures. Keep credentials, provider
  error bodies and secret-bearing exception chains out of errors and artifacts.

## Cache and observable behavior

`IndexOneDataSource(root=..., client=...)` returns `ResearchQueryResult` from
`query_with_evidence`. A default client is created lazily from the environment
only on a cache miss. The normalized operation, parameters, selected fields and
row bound identify a request. DuckDB indexes successful requests and their
immutable hash-named Parquet objects. File metadata binds the request and fetch
time; reads recheck file hash, metadata, schema and row count.

An exact cached query makes no network calls and works without a credential.
`offline=True` rejects a cache miss. A failed/truncated/malformed query publishes
no successful cache entry. `refresh=True` obtains a new observation while old
Parquet evidence remains available to existing data snapshots. Cache timestamps
describe collection, never historical publication. The cache proves the returned
response, not full market coverage or point-in-time financial correctness.

Each CLI request prints the row count and evidence file path. Documentation shows
how to inspect raw/adjusted EOD columns and financial publication fields, then
links to the existing explicit daily-snapshot import workflow. Financial report
periods must not be used as feature publication times. The source supplies no
generic cash-dividend or split feed; IndexOne's index-specific applied-action
ledger is not a substitute.

## Acceptance and verification

Start with failing tests for the client and cache. Use injected HTTP responses
only at the network boundary and real temporary Parquet/DuckDB files elsewhere.

1. Validate key-only authentication, live-schema request construction, supported
   response types, empty results and typed failures. Retry only transient errors.
2. Cache hits and offline reloads return the same data/evidence without a client.
   Different requests cannot reuse each other's cached data. Refresh preserves
   old objects. Tampered bytes or catalog metadata fail closed.
3. Reject missing columns, invalid types and oversized/truncated responses before
   successful publication. Check artifacts and exception strings for test keys.
4. Exercise the CLI/README workflow against a documented fixture. If an API key
   is available in the environment, perform a small read-only live smoke query;
   otherwise report that live verification was skipped.
5. Run focused tests, the Python regression suite, Ruff, strict mypy, documentation
   link/skill validation, and an independent review of provider and cache boundaries.

## Work record

Current state: the HTTP client, evidence cache, public exports and `tm-indexone`
command are implemented. The README and source guide describe the supported
queries and the separate daily-snapshot preparation step. Existing unrelated
working-tree changes are preserved.

Decisions: use the provider-neutral research source interface and a separate
IndexOne cache, since the canonical object store verifies TuShare-specific
raw/staging normalization. Do not silently select `trusted_imports`.

Changed files:

- `python/trademaster/data/indexone_client.py`: schema-validated HTTP requests,
  key-only authentication, typed responses and bounded failure handling.
- `python/trademaster/data/indexone.py`: immutable Parquet publication, DuckDB
  request cache, integrity checks, offline/refresh modes and CLI.
- `python/trademaster/data/__init__.py`, `pyproject.toml`: public API and command.
- `tests/test_indexone_client.py`, `tests/test_indexone_source.py`: transport,
  schema, response, cache, integrity, credential-isolation and CLI tests.
- `README.md`, `docs/reference/indexone.md`, command/data references, docs index,
  `.skills/trademaster-backtest/`, and root/data `AGENTS.md`: user and contributor
  guidance.

Red/green evidence:

- Both new test modules initially failed collection with `ModuleNotFoundError`
  for the missing client/source modules.
- Client hardening then produced three failing cases for a non-ASCII key,
  unexpected transport error leakage and an ignored envelope error. All pass
  after the client corrections: 45 client tests pass.
- All 13 source/cache tests pass with real temporary Parquet and DuckDB files,
  including offline reads, refreshed evidence, corruption rejection and CLI use.
- Fresh installation with `uv sync --locked --extra dev` succeeded; the installed
  `tm-indexone --help` command works. Ruff and strict mypy over 107 Python/test
  files passed. Skill validation passed.
- Initial full regressions passed: 419 tests, with three TuShare live tests
  skipped because their environment token was absent.
- The first credentialed EOD smoke query returned HTTP 200 with four rows in an
  operation-ID-keyed, column-array response. The parser correctly rejected the
  undocumented envelope without caching it. The added regressions first
  produced nine failures and 62 passes. The corrected parser supports both
  envelopes and checks column lengths and completion state: 71 client tests and
  13 cache/source tests pass.
- Live verification with the supplied credential then succeeded for all four
  datasets: four Apple EOD observations for January 2–5, 2024; four quarterly
  financial records for 2023 including `filingDate`, `acceptedDate`, revenue and
  net income; one security-reference row; and 138 FX observations for January 2,
  2024. The README Python block and financial-guide block were executed directly.
  The key was supplied only to the test process environment and was not saved.
- The guide's request JSON was extracted and passed to the installed
  `tm-indexone ... --offline` command without a key. It returned the same
  four-row EOD Parquet object and its request/content hashes.
- Final regressions: `uv run --no-sync pytest -q` passed 445 tests in 49.46 seconds;
  three TuShare live tests were skipped because `TUSHARE_TOKEN` was not set.
  Ruff, formatting checks on the four new Python files, strict mypy over 107
  files, skill validation and `git diff --check` passed. All 180 local links and
  heading anchors in the affected guidance resolved. README/source-guide writing
  checks passed.
- Independent review found no remaining issues in the credential boundary,
  response validation, cache integrity, offline/refresh behavior or documentation.
  A follow-up review checked the live keyed/columnar response parser separately.

Next step: no work remains for this data-source scope. A complete historical US
backtest still requires the calendar, opening permissions and corporate-action
evidence described in the data preparation guide.

## Publication verification — November 7, 2026

An isolated checkout of the staged trading-rule and IndexOne changes installed
successfully with the committed lockfile. Python tests passed 442 cases; six
tests were skipped because the clean checkout had no external TuShare caches,
demo bundles or token. The Rust workspace passed 67 tests. Rust formatting,
Clippy with warnings denied, Ruff, strict mypy and the installed IndexOne command
also passed. Other strategy-research work in the shared workspace was excluded
from this publication.
