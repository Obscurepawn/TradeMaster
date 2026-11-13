# Configurable Trading Rules and Price-Limit Enforcement

**Status:** Completed | **Date:** 2026-11-06 | **Scope:** Optional daily price-limit enforcement, explicit limit-block markers, zero-commission event runs, and cross-market snapshot examples (`trademaster.backtest`, `tm-core`, `tm-engine`, `tm-runner`) | **Updates:** [M3: A-Share Execution Rules and Lot Accounting in Rust](20260811-m3-rust-rules-ledger.md)

## Motivation and Scope

This specification generalizes TradeMaster's rule configuration so researchers can backtest markets without daily price-limit bands (such as whole-share US equities) while preserving strict A-share defaults across existing workflows and saved replays. Per-instrument `buy_lot_size`, price tick, currency, and `T+0`/`T+1` share availability already existed alongside configurable commission, stamp-duty, and transfer-fee rates; this change adds explicit price-limit enforcement switches across both execution engines and enables zero-commission schedules in the event runner.

### Non-Goals and Modeling Boundaries

This specification does not introduce a full US brokerage or clearinghouse model: short selling, fractional shares, margin accounts, T+1/T+2 cash-settlement restrictions, US regulatory (SEC/FINRA TAF) fee schedules, and multi-currency accounts remain out of scope. Here `settlement="t0"` means purchased shares are immediately eligible for resale rather than modeling clearinghouse cash/security settlement.

## Specification and Execution Rules

- **Price-limit enforcement switch:** Both `Policy.enforce_price_limits` in the daily snapshot SDK and `enforce_price_limits` in event requests default to `True`. Non-default settings are persisted explicitly in saved JSON requests for deterministic `tm-replay`.
- **Single source of truth for lot size and settlement:** Per-instrument `buy_lot_size=1` enables whole-share orders and `settlement="t0"` enables same-session resale; no global override duplicates instrument specifications.
- **Zero-fee schedules:** Setting stamp-duty or SSE transfer-fee rates to `0` disables those charges, and setting both `commission_ppm=0` and `minimum_commission=0` yields zero commission in both daily and event runs while keeping slippage and configured fees active in pre-trade cash reservations.
- **Explicit daily limit-block markers:** Daily market rows gain optional booleans `buy_blocked_by_price_limit` and `sell_blocked_by_price_limit` (defaulting to `False`). A `True` marker asserts—from documented source evidence rather than string matching on human-readable reasons—that the corresponding `"no"` opening permission was caused *solely* by a daily price limit. Setting either marker when the corresponding permission is not `"no"` fails input validation.
- **Selective relaxation & snapshot immutability:** When `enforce_price_limits=False`, Rust lifts *only* `"no"` permissions accompanied by a `True` limit-block marker. Trading suspensions, `"unknown"` permissions, missing quotes, corporate-action gaps (checked on any planned opening trade, including initial purchases of unheld symbols), other explicit blocks, and cash/lot invariants remain enforced. Existing **data snapshots** constructed without limit-block markers retain their original `"no"` restrictions; relaxing price limits on historical data requires publishing a new data snapshot with explicit markers rather than mutating an existing manifest.
- **Optional event-bar price limits:** In the event engine (`MarketBar` and `CnAshareOrderValidator::with_price_limits`), upper and lower price limits may be omitted together when `enforce_price_limits=False` so strategy views never see fabricated bounds; omitting limits when enforcement is `True` or supplying half a pair fails validation.
- **Preserved causality and sizing separation:** Signals derived from closing prices still execute no earlier than the next eligible market event. In daily runs, Rust finalizes order quantities from prior closing prices before inspecting today's open and separately sets aside the target cash reserve plus capped buy costs; proceeds from opening sales fund buys starting at the next session's open.

## Changed Files

- **Daily SDK & snapshot producers:** `python/trademaster/backtest/__init__.py`, `python/trademaster/backtest/snapshot.py`, Tushare Pro adapters in `python/trademaster/e2e/broad_etf_data.py`, `python/trademaster/e2e/long_history_data.py`, and `specs/examples/prepare_all_a_snapshot.py`.
- **Rust daily snapshot engine:** `crates/tm-engine/src/snapshot/` (`wire.rs`, `market.rs`, `mod.rs`, `execution.rs`).
- **Event contracts & runner:** `crates/tm-core/src/lib.rs`, `crates/tm-engine/src/lib.rs`, `crates/tm-runner/src/lib.rs`, `python/trademaster/e2e/__init__.py`.
- **Tests & examples:** `tests/test_snapshot_trading_rules.py`, `tests/test_e2e_orchestrator.py`, `crates/tm-engine/tests/a_share_rules.rs`, `crates/tm-engine/tests/snapshot_backtest.rs`, `crates/tm-runner/tests/wire.rs`, `specs/examples/trading_rules.py`, `specs/examples/us_stock_backtest.py`.
- **Documentation & backtesting skill:** `README.md`, `docs/reference/snapshot-backtest.md`, `docs/reference/execution.md`, `.skills/trademaster-backtest/`.

## US Equity Snapshot Example and Documentation Follow-Up

A documentation and example follow-up added `specs/examples/us_stock_backtest.py`, updated `README.md`, `.skills/trademaster-backtest/`, and `docs/reference/snapshot-backtest.md`, and demonstrated preparing and replaying a five-session synthetic USD whole-share momentum strategy (`buy_lot_size=1`, `settlement="t0"`, `enforce_price_limits=False`, zero example fees):

- **Consolidated rule references:** Detailed execution defaults (`buy_at_open`/`sell_at_open` defaulting to `"yes"` unless a loader explicitly sets `"unknown"` when trading-status evidence is absent) and cash-dividend ordering constraints (requiring an in-sample record date with `record_session < ex_session <= pay_session`, flagging unsupported schedules rather than altering source dates) are maintained once in `docs/reference/snapshot-backtest.md` and linked from `README.md` and the skill guides.
- **Reopened-snapshot workflow:** `README.md` includes a self-contained snippet that reopens a saved snapshot, configures an explicit `Policy`, runs a strategy, and saves a replayable result bundle.

## Verification and Regression Record

| Verification Area | Outcome |
|---|---|
| Rust daily & event engines | `cargo test --locked --workspace` passed **67 workspace tests** and doc-tests (including 14 `snapshot_backtest` suites covering buy/sell limit-marker toggling, first-purchase corporate-action guards, and unchanged A-share ledgers, plus `a_share_rules` and `tm-runner` `wire` suites for `with_price_limits`, optional `MarketBar` bounds, and zero commissions). |
| Python SDK, producers & replay | `uv run --no-sync pytest -q` passed **361 tests** in `59.96s` (3 live Tushare tests skipped without `TUSHARE_TOKEN`), including 75 snapshot SDK/producer tests and 10 event bundle suites (covering a single-share USD round trip and `tm-replay`). |
| Runnable examples (`trading_rules.py`, `us_stock_backtest.py`) | `trading_rules.py` produced `0` fills / `USD 100` cash with enforcement on vs. `1` seven-share fill / `USD 30` cash with enforcement off; `us_stock_backtest.py` bought `76` shares at `USD 102` on `2024-01-04`, sold at `USD 101` on `2024-01-05`, and ended at `USD 9,924` from `USD 10,000` initial cash (reproduced identically by the extracted `README.md` snippet and `tm-replay`). |
| Static analysis, packaging & links | `cargo fmt --all --check`, `cargo clippy --locked --workspace --all-targets -- -D warnings`, Ruff, strict `mypy` (103 files + examples), `uv build` (sdist + Linux `abi3` Python 3.12+ wheel), skill validation (`quick_validate.py`), `git diff --check`, all 105 local documentation links/anchors, and independent boundary reviews passed with zero open issues. |
