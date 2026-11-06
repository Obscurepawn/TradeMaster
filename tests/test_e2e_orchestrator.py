from __future__ import annotations

import hashlib
import json
import shutil
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Literal
from unittest.mock import patch

import pyarrow as pa
import pytest
from trademaster.contracts import AvailabilityPolicy, SnapshotManifest, SnapshotRequest
from trademaster.e2e import (
    ClassificationObservation,
    E2ERunnerClient,
    E2ERunnerError,
    RunnerBar,
    RunnerEvent,
    RunnerFeeSchedule,
    RunnerInstrument,
    RunnerRequest,
    RunnerSignal,
    allocate_target_weights,
    attach_e2e_bundle_artifacts,
    verify_e2e_bundle,
)
from trademaster.reporting import BenchmarkSeries, MonthlyReturnBasis, build_html_report
from trademaster.signals import signal_output_schema

UNIT = 100_000_000
DAY1_CLOSE = datetime(2025, 1, 2, 7, tzinfo=UTC)
DAY2_OPEN = datetime(2025, 1, 3, 1, 30, tzinfo=UTC)
DAY2_CLOSE = datetime(2025, 1, 3, 7, tzinfo=UTC)
DAY3_OPEN = datetime(2025, 1, 6, 1, 30, tzinfo=UTC)
DAY3_CLOSE = datetime(2025, 1, 6, 7, tzinfo=UTC)
DAY4_OPEN = datetime(2025, 1, 7, 1, 30, tzinfo=UTC)
DAY4_CLOSE = datetime(2025, 1, 7, 7, tzinfo=UTC)
DAY5_OPEN = datetime(2025, 1, 8, 1, 30, tzinfo=UTC)
DAY5_CLOSE = datetime(2025, 1, 8, 7, tzinfo=UTC)
DAY6_OPEN = datetime(2025, 1, 9, 1, 30, tzinfo=UTC)


def _empty_snapshot(as_of: datetime) -> SnapshotManifest:
    return SnapshotManifest.build(
        request=SnapshotRequest(datasets=(), as_of=as_of),
        availability_policy=AvailabilityPolicy(
            policy_id="synthetic-e2e/v1",
            known_at_field="known_at",
            publication_lag_policy_id="synthetic-e2e/v1",
        ),
        objects=(),
        coverages=(),
    )


BUY_SNAPSHOT = _empty_snapshot(DAY1_CLOSE)
SELL_SNAPSHOT = _empty_snapshot(DAY2_CLOSE)
MARKET_SNAPSHOT = _empty_snapshot(DAY3_CLOSE)


def _bar(instrument_id: str, price: int) -> RunnerBar:
    return RunnerBar(
        instrument_id=instrument_id,
        open_scaled=str(price * UNIT),
        high_scaled=str((price + 1) * UNIT),
        low_scaled=str((price - 1) * UNIT),
        close_scaled=str(price * UNIT),
        volume_units="100000",
        up_limit_scaled=str((price + 2) * UNIT),
        down_limit_scaled=str((price - 2) * UNIT),
        trading_status="tradable",
        status_evidence_id=f"status:{instrument_id}",
    )


def _event(
    at: datetime,
    kind: Literal["session_open", "bar_close", "settlement"],
    price: int = 10,
) -> RunnerEvent:
    return RunnerEvent(event_time=at, kind=kind, bars=(_bar("600000.SH", price),))


def _request() -> RunnerRequest:
    return RunnerRequest(
        schema_id="trademaster.e2e-runner/v1",
        run_id="single-round-trip",
        strategy_id="single-timing",
        snapshot_id=MARKET_SNAPSHOT.snapshot_id,
        initial_cash_scaled=str(2_000 * UNIT),
        initial_time=datetime(2025, 1, 1, tzinfo=UTC),
        instruments=(
            RunnerInstrument(
                instrument_id="600000.SH",
                asset_class="stock",
                venue="sse",
                currency="CNY",
                buy_lot_size=100,
                tick_size_scaled="1000000",
                settlement="t1",
            ),
        ),
        fee_schedule=RunnerFeeSchedule(
            schedule_id="cn-a-share-v1",
            commission_ppm=300,
            minimum_commission_scaled=str(5 * UNIT),
            sell_stamp_duty_ppm=500,
            sse_transfer_fee_ppm=10,
            rounding_unit_scaled="1000000",
        ),
        slippage_ppm=1_000,
        session_opens=(DAY2_OPEN, DAY3_OPEN, DAY4_OPEN),
        events=(
            _event(DAY1_CLOSE, "bar_close"),
            _event(DAY2_OPEN, "settlement"),
            _event(DAY2_OPEN, "session_open"),
            _event(DAY2_CLOSE, "bar_close", 11),
            _event(DAY3_OPEN, "settlement", 11),
            _event(DAY3_OPEN, "session_open", 11),
            _event(DAY3_CLOSE, "bar_close", 11),
        ),
        signals=(
            RunnerSignal(
                signal_id="buy",
                instrument_id="600000.SH",
                signal_time=DAY1_CLOSE,
                eligible_execution_time=DAY2_OPEN,
                intent_type="quantity",
                value_scaled=str(100 * UNIT),
                reason="buy threshold",
                snapshot_id=BUY_SNAPSHOT.snapshot_id,
            ),
            RunnerSignal(
                signal_id="sell",
                instrument_id="600000.SH",
                signal_time=DAY2_CLOSE,
                eligible_execution_time=DAY3_OPEN,
                intent_type="quantity",
                value_scaled="0",
                reason="sell threshold",
                snapshot_id=SELL_SNAPSHOT.snapshot_id,
            ),
        ),
    )


def test_event_price_limit_policy_preserves_default_serialization() -> None:
    original = _request()
    assert original.enforce_price_limits is True
    payload = original.model_dump(mode="json")
    assert "enforce_price_limits" not in payload
    payload["enforce_price_limits"] = False
    configured = RunnerRequest.model_validate_json(json.dumps(payload))
    assert configured.model_dump(mode="json")["enforce_price_limits"] is False


def test_event_price_limits_are_optional_only_with_enforcement_disabled() -> None:
    payload = _request().model_dump(mode="json")
    for event in payload["events"]:
        for bar in event["bars"]:
            bar.pop("up_limit_scaled")
            bar.pop("down_limit_scaled")
    with pytest.raises(ValueError, match="price limits are required"):
        RunnerRequest.model_validate_json(json.dumps(payload))
    payload["enforce_price_limits"] = False
    request = RunnerRequest.model_validate_json(json.dumps(payload))
    assert request.events[0].bars[0].up_limit_scaled is None
    assert "up_limit_scaled" not in request.model_dump(mode="json")["events"][0]["bars"][0]
    payload["events"][0]["bars"][0]["up_limit_scaled"] = "1100000000"
    with pytest.raises(ValueError, match="price limits must be a positive ordered pair"):
        RunnerRequest.model_validate_json(json.dumps(payload))


def test_event_fee_schedule_accepts_zero_commission() -> None:
    payload = _request().fee_schedule.model_dump(mode="json")
    payload["commission_ppm"] = 0
    payload["minimum_commission_scaled"] = "0"
    assert RunnerFeeSchedule.model_validate_json(json.dumps(payload)).commission_ppm == 0


def test_configured_event_run_persists_limit_switch_and_replays(tmp_path: Path) -> None:
    payload = _request().model_dump(mode="json")
    payload["enforce_price_limits"] = False
    payload["instruments"][0].update(venue="nasdaq", currency="USD", buy_lot_size=1, settlement="t0")
    payload["signals"][0]["value_scaled"] = str(UNIT)
    payload["slippage_ppm"] = 0
    payload["fee_schedule"].update(
        commission_ppm=0,
        minimum_commission_scaled="0",
        sell_stamp_duty_ppm=0,
        sse_transfer_fee_ppm=0,
    )
    for event in payload["events"]:
        for bar in event["bars"]:
            bar.pop("up_limit_scaled")
            bar.pop("down_limit_scaled")
    configured = RunnerRequest.model_validate_json(json.dumps(payload).replace("600000.SH", "AAPL.US"))
    bundle = E2ERunnerClient().execute(
        configured,
        output_dir=tmp_path / "configured",
        benchmarks=(),
        classifications={"AAPL.US": ("technology", "large")},
        snapshot_manifests=(BUY_SNAPSHOT, SELL_SNAPSHOT, MARKET_SNAPSHOT),
    )
    saved = json.loads(bundle.request_path.read_text())
    assert saved["enforce_price_limits"] is False
    assert "up_limit_scaled" not in saved["events"][0]["bars"][0]
    result = json.loads(bundle.result_path.read_text())
    assert [fill["quantity"] for fill in result["fills"]] == ["1", "1"]
    assert result["nav"][-1]["cash_scaled"] == str(2_001 * UNIT)
    verified = verify_e2e_bundle(bundle.manifest_path)
    assert verified.result_sha256 == bundle.result_sha256


def test_target_weight_allocator_emits_lot_sized_quantity_signals() -> None:
    table = pa.Table.from_pylist(
        [
            {
                "signal_id": "weight-a",
                "strategy_id": "weekly",
                "instrument_id": "600000.SH",
                "signal_time": DAY1_CLOSE,
                "eligible_execution_time": DAY2_OPEN,
                "intent_type": "target_weight",
                "value": Decimal("0.60000000"),
                "reason": "top_n_factor_rank",
                "snapshot_id": "a" * 64,
            },
            {
                "signal_id": "weight-b",
                "strategy_id": "weekly",
                "instrument_id": "000001.SZ",
                "signal_time": DAY1_CLOSE,
                "eligible_execution_time": DAY2_OPEN,
                "intent_type": "target_weight",
                "value": Decimal("0.40000000"),
                "reason": "top_n_factor_rank",
                "snapshot_id": "a" * 64,
            },
        ],
        schema=signal_output_schema(),
    )
    allocated = allocate_target_weights(
        table,
        target_nav_scaled=10_000 * UNIT,
        open_prices_scaled={"000001.SZ": 20 * UNIT, "600000.SH": 10 * UNIT},
        lot_sizes={"000001.SZ": 100, "600000.SH": 100},
    )

    assert allocated["intent_type"].to_pylist() == ["quantity", "quantity"]
    assert allocated["value"].to_pylist() == [
        Decimal("600.00000000"),
        Decimal("200.00000000"),
    ]
    assert allocated.schema.metadata == table.schema.metadata


def test_real_rust_runner_round_trip_builds_reproducible_report_bundle(
    tmp_path: Path,
) -> None:
    report_times = (DAY1_CLOSE, DAY2_CLOSE, DAY3_CLOSE)
    benchmark = BenchmarkSeries(
        benchmark_id="000016.SH",
        event_times=report_times,
        close_values=(Decimal(100), Decimal(101), Decimal(102)),
    )
    client = E2ERunnerClient()
    bundle = client.execute(
        _request(),
        output_dir=tmp_path / "run",
        benchmarks=(benchmark,),
        classifications={"600000.SH": ("bank", "large")},
        snapshot_manifests=(BUY_SNAPSHOT, SELL_SNAPSHOT, MARKET_SNAPSHOT),
    )

    result = json.loads(bundle.result_path.read_text(encoding="utf-8"))
    manifest = json.loads(bundle.manifest_path.read_text(encoding="utf-8"))
    report_input = json.loads(bundle.report_input_path.read_text(encoding="utf-8"))
    assert report_input["monthly_return_basis"] == "previous_month_close"
    assert result["execution_count"] == 2
    assert result["final_positions"] == []
    assert manifest["request_sha256"] == result["request_sha256"]
    assert bundle.report_path.read_text(encoding="utf-8").startswith("<!doctype html>")
    verified = verify_e2e_bundle(bundle.manifest_path)
    assert verified.result_sha256 == bundle.result_sha256

    forged_root = tmp_path / "forged"
    shutil.copytree(bundle.manifest_path.parent, forged_root)
    forged_result_path = forged_root / "runner-result.json"
    forged_result = json.loads(forged_result_path.read_text(encoding="utf-8"))
    forged_result["fills"][0]["tax_scaled"] = "999999999"
    forged_result_bytes = json.dumps(forged_result, sort_keys=True, separators=(",", ":")).encode()
    forged_result_path.write_bytes(forged_result_bytes)
    forged_manifest_path = forged_root / "bundle-manifest.json"
    forged_manifest = json.loads(forged_manifest_path.read_text(encoding="utf-8"))
    forged_digest = hashlib.sha256(forged_result_bytes).hexdigest()
    forged_manifest["result_sha256"] = forged_digest
    forged_manifest["result"]["sha256"] = forged_digest
    forged_manifest_path.write_text(
        json.dumps(forged_manifest, sort_keys=True, separators=(",", ":")),
        encoding="utf-8",
    )
    with pytest.raises(E2ERunnerError, match="Rust replay"):
        verify_e2e_bundle(forged_manifest_path)

    second = client.execute(
        _request(),
        output_dir=tmp_path / "run-2",
        benchmarks=(benchmark,),
        classifications={"600000.SH": ("bank", "large")},
        snapshot_manifests=(BUY_SNAPSHOT, SELL_SNAPSHOT, MARKET_SNAPSHOT),
    )
    assert bundle.request_sha256 == second.request_sha256
    assert bundle.result_sha256 == second.result_sha256
    assert bundle.report_path.read_bytes() == second.report_path.read_bytes()


def test_bundle_replays_the_bytes_checked_against_its_manifest(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    bundle = E2ERunnerClient().execute(
        _request(),
        output_dir=tmp_path,
        benchmarks=(),
        classifications={"600000.SH": ("bank", "large")},
        snapshot_manifests=(BUY_SNAPSHOT, SELL_SNAPSHOT, MARKET_SNAPSHOT),
    )
    paths = {bundle.request_path, bundle.result_path, bundle.report_input_path, bundle.report_path}
    original_read = Path.read_bytes

    def replace_after_read(path: Path) -> bytes:
        data = original_read(path)
        if path in paths:
            path.write_bytes(b"file replaced by another writer")
        return data

    monkeypatch.setattr(Path, "read_bytes", replace_after_read)
    verified = verify_e2e_bundle(bundle.manifest_path)
    assert verified.request_sha256 == bundle.request_sha256
    assert verified.result_sha256 == bundle.result_sha256


def test_unversioned_report_input_reproduces_the_archived_monthly_heatmap(
    tmp_path: Path,
) -> None:
    request = _request()
    feb_close = _event(datetime(2025, 2, 3, 7, tzinfo=UTC), "bar_close", 11)
    request = request.model_copy(update={"events": (*request.events[:-1], feb_close)})
    with patch("trademaster.e2e.build_html_report", wraps=build_html_report) as render:
        bundle = E2ERunnerClient().execute(
            request,
            output_dir=tmp_path,
            benchmarks=(),
            classifications={"600000.SH": ("bank", "large")},
            snapshot_manifests=(BUY_SNAPSHOT, SELL_SNAPSHOT, MARKET_SNAPSHOT),
        )
    legacy_options = dict(render.call_args.kwargs)
    legacy_options["monthly_return_basis"] = MonthlyReturnBasis.FIRST_OBSERVATION
    build_html_report(bundle.report_path, **legacy_options)
    report_input = json.loads(bundle.report_input_path.read_bytes())
    del report_input["monthly_return_basis"]
    input_bytes = json.dumps(report_input, sort_keys=True, separators=(",", ":")).encode()
    bundle.report_input_path.write_bytes(input_bytes)

    # SHA-256 from this fixture rendered by the original reporter with uv.lock.
    archived_sha256 = "1f73a33ef7375c07f56698b1d5ad4572c64383e3180e6e50b0be5fd0173ade3c"
    manifest = json.loads(bundle.manifest_path.read_bytes())
    manifest["report_sha256"] = manifest["report"]["sha256"] = archived_sha256
    manifest["report_input"]["sha256"] = hashlib.sha256(input_bytes).hexdigest()
    bundle.manifest_path.write_text(json.dumps(manifest))

    verified = verify_e2e_bundle(bundle.manifest_path)
    assert hashlib.sha256(verified.report_path.read_bytes()).hexdigest() == archived_sha256


def test_report_exposure_uses_latest_classification_available_at_each_event(
    tmp_path: Path,
) -> None:
    benchmark = BenchmarkSeries(
        benchmark_id="000016.SH",
        event_times=(DAY1_CLOSE, DAY2_CLOSE, DAY3_CLOSE),
        close_values=(Decimal(100), Decimal(101), Decimal(102)),
    )
    bundle = E2ERunnerClient().execute(
        _request(),
        output_dir=tmp_path / "classification-timeline",
        benchmarks=(benchmark,),
        classifications=(
            ClassificationObservation(
                instrument_id="600000.SH",
                effective_at=datetime(2025, 1, 1, tzinfo=UTC),
                industry_name="bank",
                market_cap_bucket="small",
            ),
            ClassificationObservation(
                instrument_id="600000.SH",
                effective_at=DAY2_CLOSE,
                industry_name="bank",
                market_cap_bucket="large",
            ),
        ),
        snapshot_manifests=(BUY_SNAPSHOT, SELL_SNAPSHOT, MARKET_SNAPSHOT),
    )

    report_input = json.loads(bundle.report_input_path.read_text(encoding="utf-8"))
    assert "classification_timeline" in report_input
    day_two = next(
        item for item in report_input["exposures"] if item["event_time"] == DAY2_CLOSE.isoformat()
    )
    assert "large" in dict(day_two["market_cap_weights"])
    assert "small" not in dict(day_two["market_cap_weights"])
    verify_e2e_bundle(bundle.manifest_path)
    strategy_report = bundle.manifest_path.parent / "strategy-report.md"
    strategy_summary = bundle.manifest_path.parent / "strategy-summary.json"
    strategy_report.write_text("# report\n", encoding="utf-8")
    strategy_summary.write_text('{"ok":true}', encoding="utf-8")
    attach_e2e_bundle_artifacts(
        bundle.manifest_path,
        artifacts={
            "strategy_report": strategy_report,
            "strategy_summary": strategy_summary,
        },
    )
    assert json.loads(bundle.manifest_path.read_text())["schema_id"] == (
        "trademaster.e2e-bundle/v3"
    )
    verify_e2e_bundle(bundle.manifest_path)


def test_three_instrument_periodic_rebalance_and_exit_share_one_runtime(
    tmp_path: Path,
) -> None:
    instruments = ("000001.SZ", "510300.SH", "600000.SH")
    rebalance_one = _empty_snapshot(DAY1_CLOSE)
    rebalance_two = _empty_snapshot(DAY3_CLOSE)
    exit_snapshot = _empty_snapshot(DAY4_CLOSE)
    market_snapshot = _empty_snapshot(DAY5_CLOSE)

    def event(
        at: datetime, kind: Literal["session_open", "bar_close", "settlement"]
    ) -> RunnerEvent:
        return RunnerEvent(
            event_time=at,
            kind=kind,
            bars=()
            if kind == "settlement"
            else tuple(_bar(instrument_id, 10) for instrument_id in instruments),
        )

    signals: list[RunnerSignal] = []
    schedules = (
        (
            "r1",
            DAY1_CLOSE,
            DAY2_OPEN,
            rebalance_one.snapshot_id,
            {"000001.SZ": 100, "510300.SH": 100, "600000.SH": 0},
        ),
        (
            "r2",
            DAY3_CLOSE,
            DAY4_OPEN,
            rebalance_two.snapshot_id,
            {"000001.SZ": 0, "510300.SH": 100, "600000.SH": 100},
        ),
        (
            "exit",
            DAY4_CLOSE,
            DAY5_OPEN,
            exit_snapshot.snapshot_id,
            {instrument_id: 0 for instrument_id in instruments},
        ),
    )
    for prefix, signal_time, eligible, snapshot_id, targets in schedules:
        for instrument_id in instruments:
            signals.append(
                RunnerSignal(
                    signal_id=f"{prefix}:{instrument_id}",
                    instrument_id=instrument_id,
                    signal_time=signal_time,
                    eligible_execution_time=eligible,
                    intent_type="quantity",
                    value_scaled=str(targets[instrument_id] * UNIT),
                    reason=prefix,
                    snapshot_id=snapshot_id,
                )
            )
    request = RunnerRequest(
        schema_id="trademaster.e2e-runner/v1",
        run_id="synthetic-periodic-rebalance",
        strategy_id="periodic-top-n",
        snapshot_id=market_snapshot.snapshot_id,
        initial_cash_scaled=str(5_000 * UNIT),
        initial_time=datetime(2025, 1, 1, tzinfo=UTC),
        instruments=(
            RunnerInstrument(
                instrument_id="000001.SZ",
                asset_class="stock",
                venue="szse",
                currency="CNY",
                buy_lot_size=100,
                tick_size_scaled="1000000",
                settlement="t1",
            ),
            RunnerInstrument(
                instrument_id="510300.SH",
                asset_class="etf",
                venue="sse",
                currency="CNY",
                buy_lot_size=100,
                tick_size_scaled="100000",
                settlement="t1",
            ),
            RunnerInstrument(
                instrument_id="600000.SH",
                asset_class="stock",
                venue="sse",
                currency="CNY",
                buy_lot_size=100,
                tick_size_scaled="1000000",
                settlement="t1",
            ),
        ),
        fee_schedule=_request().fee_schedule,
        slippage_ppm=0,
        session_opens=(DAY2_OPEN, DAY3_OPEN, DAY4_OPEN, DAY5_OPEN, DAY6_OPEN),
        events=(
            event(DAY1_CLOSE, "bar_close"),
            event(DAY2_OPEN, "settlement"),
            event(DAY2_OPEN, "session_open"),
            event(DAY2_CLOSE, "bar_close"),
            event(DAY3_OPEN, "settlement"),
            event(DAY3_OPEN, "session_open"),
            event(DAY3_CLOSE, "bar_close"),
            event(DAY4_OPEN, "settlement"),
            event(DAY4_OPEN, "session_open"),
            event(DAY4_CLOSE, "bar_close"),
            event(DAY5_OPEN, "settlement"),
            event(DAY5_OPEN, "session_open"),
            event(DAY5_CLOSE, "bar_close"),
        ),
        signals=tuple(signals),
    )
    report_times = (DAY1_CLOSE, DAY2_CLOSE, DAY3_CLOSE, DAY4_CLOSE, DAY5_CLOSE)
    bundle = E2ERunnerClient().execute(
        request,
        output_dir=tmp_path / "periodic",
        benchmarks=(
            BenchmarkSeries(
                benchmark_id="synthetic-index",
                event_times=report_times,
                close_values=(Decimal(100), Decimal(101), Decimal(102), Decimal(103), Decimal(104)),
            ),
        ),
        classifications={
            "000001.SZ": ("bank", "large"),
            "510300.SH": ("broad_market_etf", "large_blend"),
            "600000.SH": ("bank", "large"),
        },
        snapshot_manifests=(
            rebalance_one,
            rebalance_two,
            exit_snapshot,
            market_snapshot,
        ),
    )
    result = json.loads(bundle.result_path.read_text(encoding="utf-8"))
    assert result["order_count"] == result["execution_count"] == 6
    assert result["rejection_count"] == 0
    assert result["final_positions"] == []
