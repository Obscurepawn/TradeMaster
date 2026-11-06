# Configurable trading rules

Status: completed

## Purpose and scope

Allow researchers to configure the trading assumptions used for a market while
preserving the current A-share defaults. Instrument lot size, tick size, currency
and T0/T1 share availability already exist. Commission, minimum commission, stamp
duty and transfer-fee rates are already run inputs. Add the missing price-limit
enforcement switch and allow zero commission in the event runner.

This change does not introduce a complete US broker/account model. Short sales,
fractional shares, cash settlement, margin, US regulatory fees and cross-currency
accounts remain outside this change. T0 here means bought shares are immediately
sellable; it does not describe cash or securities clearing settlement.

## Behavior

- `Policy.enforce_price_limits` in the daily SDK and `enforce_price_limits` in an
  event request default to `true`. Saved requests record a nondefault choice.
- Existing per-instrument `buy_lot_size=1` allows whole-share trading and
  `settlement="t0"` allows immediate resale. These remain the single source of
  truth; there is no second global lot-size or settlement override.
- Set stamp-duty or transfer-fee rates to zero to disable each charge. Zero
  commission is valid; set minimum commission to zero too for zero commission
  charges. Fees and slippage still participate in cash checks and reservations.
- Daily rows gain optional boolean `buy_blocked_by_price_limit` and
  `sell_blocked_by_price_limit` markers, defaulting to false. A true marker means
  the corresponding known `no` permission is caused only by the daily price
  limit. Producers mark only documented limit blocks. The flag is not inferred
  from human-readable rejection reasons.
- With enforcement off, only a marked price-limit block is lifted. Suspension,
  unknown permissions, absent evidence or prices, corporate-action gaps, other
  explicit blocks and account invariants still apply. A marker on a permission
  other than `no` is invalid input.
- Old data snapshots without these markers preserve their existing permissions.
  To relax a price-limit block already encoded in one, publish a new data snapshot
  with the source evidence and markers; do not rewrite an existing manifest.
- Event bars may omit both price limits when enforcement is off. Missing limits
  are invalid when enforcement is on. Supplied limits must be a valid pair.
- The event validator exposes the same switch. Timing, lot size, suspension,
  available cash and sellable quantity checks remain active.
- A close-derived decision still trades no earlier than the next eligible event.
  Daily quantities are fixed from prior closing prices before opening checks;
  cash is reserved separately. Sale proceeds enter the next open's buy budget.

## Implementation and verification

Implemented changes: daily SDK policy and market encoding, Rust daily opening checks,
daily market producers, event validation and input contracts, tests, README and
the snapshot/execution references.

Acceptance checks:

1. The same marked limit block rejects by default and fills with enforcement off;
   each side is checked. Saved replay uses the same choice and reproduces results.
2. Disabling the limit rule still blocks suspension and unmarked restrictions,
   and still reports unknown/missing evidence. Malformed markers are rejected.
3. Event replay accepts absent bands only with enforcement off, and accepts zero
   commission. Default requests retain their previous behavior and serialization.
4. A whole-share USD fixture uses explicit T0, lot size one, zero A-share taxes
   and a local session clock; existing A-share examples remain unchanged.
5. Run Rust formatting, Clippy with warnings denied, workspace tests, Python
   tests, Ruff and strict mypy. Independently review the changed rule boundaries.

Red/green results:

- `cargo test --locked -p tm-engine --test snapshot_backtest price_limit`:
  3 failures because the daily wire rejected the new markers. After the daily
  implementation, `cargo test --locked -p tm-engine --test snapshot_backtest`
  passes all 14 tests, including both sides and the unaffected A-share ledger.
- `.venv/bin/python -m pytest tests/test_snapshot_trading_rules.py -q`:
  17 failed and 1 passed before SDK changes; the missing marker/policy arguments
  caused the expected failures.
- Event red: `cargo test --locked -p tm-engine --test a_share_rules` failed on
  missing `with_price_limits`; `cargo test --locked -p tm-runner --test wire`
  had 3 passes and 3 failures for the absent switch and optional bounds;
  `uv run --no-sync pytest tests/test_e2e_orchestrator.py -k
  'event_price_limit or event_fee_schedule' -q` had 3 failures for missing
  settings and rejected zero commission.

Current state: both Rust execution paths and their Python inputs implement the
switch. Corporate-action gaps on any planned opening stop before fills,
including a first purchase; the prior guard checked existing holdings only.
Event inputs represent absent bounds with optional values in `MarketBar`, so
strategy views do not receive invented limits. The four daily data producers
publish markers without changing the default meaning of their permissions.

Changed files:

- Python daily API and publisher: `python/trademaster/backtest/__init__.py` and
  `snapshot.py`; TuShare adapters in `python/trademaster/e2e/broad_etf_data.py`,
  `long_history_data.py`, and `specs/examples/prepare_all_a_snapshot.py`.
- Daily Rust input, preparation and matching: `crates/tm-engine/src/snapshot/`
  (`wire.rs`, `market.rs`, `mod.rs`, `execution.rs`).
- Event contracts and execution: `crates/tm-core/src/lib.rs`,
  `crates/tm-engine/src/lib.rs`, `crates/tm-runner/src/lib.rs`, and
  `python/trademaster/e2e/__init__.py`.
- Behavior tests: `tests/test_snapshot_trading_rules.py`, provider preparation
  tests, `tests/test_e2e_orchestrator.py`, engine `a_share_rules.rs` and
  `snapshot_backtest.rs`, runner `wire.rs`; existing event fixtures now use
  explicit optional price limits.
- User documentation: `README.md`, `docs/reference/snapshot-backtest.md`,
  `docs/reference/execution.md`, and `specs/examples/trading_rules.py`.

Green verification:

- Fresh native installation: `uv sync --locked --extra dev` succeeded.
- Snapshot SDK and producers: 75 passed. Event SDK and bundle verification:
  10 passed, including the single-share USD round trip and saved replay.
- `uv run --no-sync pytest -q`: 361 passed, 3 skipped in 59.96 seconds. The three
  live TuShare tests were skipped because `TUSHARE_TOKEN` was not set; no live
  provider coverage is claimed.
- `cargo test --locked --workspace`: 67 tests passed; doc-tests passed.
- `cargo fmt --all --check`, `cargo clippy --locked --workspace --all-targets --
  -D warnings`, Ruff over Python/tests and the two affected examples, and strict
  mypy over Python/tests (103 files) passed. `git diff --check` passed.
- The runnable configuration example produces zero fills and USD 100 cash with
  enforcement on, versus one seven-share fill and USD 30 cash with it off. Both
  saved requests reproduced their complete results.
- Independent review of default compatibility, optional limits, permission
  boundaries, producer evidence, fee behavior and documentation found no
  actionable issues.
- `uv build` built both the source distribution and the Linux abi3 Python 3.12+
  wheel successfully. Existing Cargo package-metadata warnings about missing
  descriptions did not prevent the build.

Next step: no implementation work remains for this scope. A later market-data
adapter can publish a US data snapshot using explicit instruments, calendars,
costs and evidence; account-specific US execution rules require a separate spec.

## US backtesting documentation follow-up

Status: completed.

The README and repository backtesting skill now explain how to prepare and run
a US equity data snapshot using the existing rule settings. A synthetic daily
momentum example demonstrates close-time decisions, next-open fills, USD results
and saved replay. The README also provides a complete call that reopens a saved
snapshot, supplies a strategy and costs, and saves the new result. Detailed
execution rules, permission defaults and dividend constraints are maintained in
the daily API reference; the skill covers data preparation and links to them.
This follow-up changes documentation and an executable example; it does not add
a US data provider or new execution rules.

Changed files: `README.md`, `.skills/trademaster-backtest/`, and
`specs/examples/us_stock_backtest.py`, plus `docs/reference/snapshot-backtest.md`.

The writing review identified three changes: remove repeated rule explanations,
replace the implication that missing permissions default to unknown with an
explicit loader instruction, and supply the complete existing-snapshot call.
The README keeps one recommended US strategy example. The API reference now
contains the separate switch comparison and documents the actual `yes` defaults.

Verification:

- `uv run --no-sync python specs/examples/us_stock_backtest.py` produced a
  76-share buy on January 4, 2024 at USD 102, a sale on January 5 at USD 101,
  and final equity of USD 9,924 from USD 10,000 initial cash.
- `tm-replay artifacts/us-stock-demo/run/request.json` completed. The full
  parsed replay result equals the saved `result.json`, including both fills
  and every daily account value.
- Ruff lint and formatting checks and strict mypy passed for the new example.
- The new README Python block was extracted and executed from the repository
  root in the installed project environment. It printed
  `completed: 2 fills, 9924.00 USD`. Its saved replay matched the full result.
- The blocked-rotation and price-limit switch examples were rerun and matched
  their documented output. Complete replays of the US example, reopened-snapshot
  run and both switch settings matched all four saved results.
- The skill creator's `quick_validate.py` passed. All 105 local documentation
  links and their heading anchors resolved. `git diff --check` passed.
- The writing check passed for the README, main skill and API reference. Its
  heading-density warning for the shorter data guide was reviewed: the four
  headings remain useful entry points for distinct import workflows.
- Independent review identified the cash-dividend date-order limitation.
  The daily API reference requires an in-sample record date and
  `record_session < ex_session <= pay_session`, and explains how to flag
  unsupported distributions without changing source dates. The README and data
  guide link to that contract.
- A final independent review found no remaining issues in the revised reading
  path, shared rule references, permission wording or saved-snapshot invocation.

No red/green cycle is required for this documentation-only follow-up. The
example uses synthetic observations; it does not validate a live US provider.

Next step: no documentation work remains for this scope.
