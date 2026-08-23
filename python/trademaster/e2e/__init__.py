"""Strict orchestration from Python research outputs to the Rust event runtime."""

from __future__ import annotations

import hashlib
import json
import subprocess
import tempfile
from dataclasses import dataclass
from datetime import datetime
from decimal import ROUND_DOWN, Decimal
from itertools import pairwise
from pathlib import Path
from typing import Literal, Self

import pyarrow as pa
import pyarrow.parquet as pq
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from trademaster.contracts import SnapshotManifest, _require_utc
from trademaster.factors.management import FactorDefinition
from trademaster.factors.storage import FactorArtifact, FactorMaterialization
from trademaster.reporting import (
    BenchmarkSeries,
    ExposureObservation,
    PerformanceSeries,
    ReportIdentity,
    TradeSummary,
    build_html_report,
)
from trademaster.signals import signal_output_schema

_SCALE = Decimal(100_000_000)


class E2ERunnerError(RuntimeError):
    """The runner process or its identity-bound output failed closed."""


def _canonical_integer(value: str) -> str:
    if not isinstance(value, str):
        raise TypeError("scaled integers must use strings")
    try:
        parsed = int(value)
    except ValueError as error:
        raise ValueError("scaled integer is invalid") from error
    if str(parsed) != value:
        raise ValueError("scaled integer is not canonical")
    return value


class _FrozenModel(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)


class RunnerInstrument(_FrozenModel):
    instrument_id: str = Field(min_length=1)
    asset_class: Literal["stock", "etf", "index"]
    venue: Literal["sse", "szse", "bse", "hkex", "nyse", "nasdaq", "other"]
    currency: str = Field(min_length=3, max_length=3)
    buy_lot_size: int = Field(gt=0)
    tick_size_scaled: str
    settlement: Literal["t0", "t1"]

    _integer = field_validator("tick_size_scaled")(_canonical_integer)


class RunnerFeeSchedule(_FrozenModel):
    schedule_id: str = Field(min_length=1)
    commission_ppm: int = Field(gt=0, le=1_000_000)
    minimum_commission_scaled: str
    sell_stamp_duty_ppm: int = Field(ge=0, le=1_000_000)
    sse_transfer_fee_ppm: int = Field(ge=0, le=1_000_000)
    rounding_unit_scaled: str

    _integers = field_validator("minimum_commission_scaled", "rounding_unit_scaled")(
        _canonical_integer
    )


class RunnerBar(_FrozenModel):
    instrument_id: str = Field(min_length=1)
    open_scaled: str
    high_scaled: str
    low_scaled: str
    close_scaled: str
    volume_units: str
    up_limit_scaled: str
    down_limit_scaled: str
    trading_status: Literal["tradable", "suspended"]
    status_evidence_id: str = Field(min_length=1)

    _integers = field_validator(
        "open_scaled",
        "high_scaled",
        "low_scaled",
        "close_scaled",
        "volume_units",
        "up_limit_scaled",
        "down_limit_scaled",
    )(_canonical_integer)


class RunnerEvent(_FrozenModel):
    event_time: datetime
    kind: Literal["session_open", "bar_close", "settlement"]
    bars: tuple[RunnerBar, ...]

    _utc = field_validator("event_time")(_require_utc)

    @model_validator(mode="after")
    def validate_bar_order(self) -> Self:
        ids = tuple(item.instrument_id for item in self.bars)
        if ids != tuple(sorted(set(ids))):
            raise ValueError("runner bars must be unique and sorted")
        return self


class RunnerSignal(_FrozenModel):
    signal_id: str = Field(min_length=1)
    instrument_id: str = Field(min_length=1)
    signal_time: datetime
    eligible_execution_time: datetime
    intent_type: Literal["quantity", "target_weight"]
    value_scaled: str
    reason: str
    snapshot_id: str = Field(pattern=r"^[0-9a-f]{64}$")

    _utc = field_validator("signal_time", "eligible_execution_time")(_require_utc)
    _integer = field_validator("value_scaled")(_canonical_integer)

    @model_validator(mode="after")
    def validate_timing(self) -> Self:
        if self.eligible_execution_time <= self.signal_time:
            raise ValueError("eligible execution time must follow signal time")
        return self


class RunnerRequest(_FrozenModel):
    schema_id: Literal["trademaster.e2e-runner/v1"]
    run_id: str = Field(min_length=1)
    strategy_id: str = Field(min_length=1)
    snapshot_id: str = Field(pattern=r"^[0-9a-f]{64}$")
    initial_cash_scaled: str
    initial_time: datetime
    instruments: tuple[RunnerInstrument, ...]
    fee_schedule: RunnerFeeSchedule
    slippage_ppm: int = Field(ge=0, le=1_000_000)
    session_opens: tuple[datetime, ...]
    events: tuple[RunnerEvent, ...]
    signals: tuple[RunnerSignal, ...]

    _integer = field_validator("initial_cash_scaled")(_canonical_integer)
    _utc = field_validator("initial_time")(_require_utc)

    @field_validator("session_opens")
    @classmethod
    def validate_session_opens(cls, values: tuple[datetime, ...]) -> tuple[datetime, ...]:
        for value in values:
            _require_utc(value)
        if not values or any(left >= right for left, right in pairwise(values)):
            raise ValueError("session opens must be nonempty and ordered")
        return values

    @model_validator(mode="after")
    def validate_identity_and_order(self) -> Self:
        instrument_ids = tuple(item.instrument_id for item in self.instruments)
        if not instrument_ids or instrument_ids != tuple(sorted(set(instrument_ids))):
            raise ValueError("runner instruments must be nonempty, unique and sorted")
        if not self.events or any(
            (left.event_time, _event_phase(left.kind))
            >= (right.event_time, _event_phase(right.kind))
            for left, right in zip(self.events, self.events[1:])
        ):
            raise ValueError("runner events must be strictly ordered")
        if any(
            bar.instrument_id not in instrument_ids for event in self.events for bar in event.bars
        ) or any(signal.instrument_id not in instrument_ids for signal in self.signals):
            raise ValueError("runner input references an unknown instrument")
        signal_ids = tuple(signal.signal_id for signal in self.signals)
        if len(signal_ids) != len(set(signal_ids)):
            raise ValueError("runner signal IDs must be unique")
        return self


def _event_phase(kind: str) -> int:
    return {"settlement": 0, "session_open": 1, "bar_close": 2}[kind]


class _PositionResult(_FrozenModel):
    instrument_id: str
    quantity: str
    sellable_quantity: str
    market_value_scaled: str

    _integers = field_validator("quantity", "sellable_quantity", "market_value_scaled")(
        _canonical_integer
    )


class _NavResult(_FrozenModel):
    event_time: datetime
    event_kind: Literal["session_open", "bar_close", "settlement"]
    cash_scaled: str
    market_value_scaled: str
    net_asset_value_scaled: str
    state_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    positions: tuple[_PositionResult, ...]

    _utc = field_validator("event_time")(_require_utc)
    _integers = field_validator("cash_scaled", "market_value_scaled", "net_asset_value_scaled")(
        _canonical_integer
    )


class _FillResult(_FrozenModel):
    fill_id: str
    order_id: str
    instrument_id: str
    side: Literal["buy", "sell"]
    event_time: datetime
    quantity: str
    price_scaled: str
    commission_scaled: str
    tax_scaled: str
    transfer_fee_scaled: str
    slippage_scaled: str

    _utc = field_validator("event_time")(_require_utc)
    _integers = field_validator(
        "quantity",
        "price_scaled",
        "commission_scaled",
        "tax_scaled",
        "transfer_fee_scaled",
        "slippage_scaled",
    )(_canonical_integer)


class _RejectionResult(_FrozenModel):
    order_id: str
    event_time: datetime
    code: str
    message: str

    _utc = field_validator("event_time")(_require_utc)


class _RunnerResult(_FrozenModel):
    schema_id: Literal["trademaster.e2e-result/v1"]
    run_id: str
    strategy_id: str
    snapshot_id: str = Field(pattern=r"^[0-9a-f]{64}$")
    request_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    event_count: int = Field(ge=1)
    signal_count: int = Field(ge=0)
    order_count: int = Field(ge=0)
    accepted_order_count: int = Field(ge=0)
    rejection_count: int = Field(ge=0)
    execution_count: int = Field(ge=0)
    ledger_effect_count: int = Field(ge=0)
    signals: tuple[dict[str, object], ...]
    orders: tuple[dict[str, object], ...]
    accepted_orders: tuple[dict[str, object], ...]
    executions: tuple[dict[str, object], ...]
    ledger_effects: tuple[dict[str, object], ...]
    account_snapshots: tuple[dict[str, object], ...]
    nav: tuple[_NavResult, ...]
    fills: tuple[_FillResult, ...]
    rejections: tuple[_RejectionResult, ...]
    final_positions: tuple[_PositionResult, ...]


@dataclass(frozen=True, slots=True)
class E2ERunBundle:
    request_path: Path
    result_path: Path
    report_input_path: Path
    manifest_path: Path
    report_path: Path
    request_sha256: str
    result_sha256: str


@dataclass(frozen=True, slots=True)
class ClassificationObservation:
    """One event-time-valid industry and market-cap label for an instrument."""

    instrument_id: str
    effective_at: datetime
    industry_name: str
    market_cap_bucket: str

    def __post_init__(self) -> None:
        _require_utc(self.effective_at)
        if not self.instrument_id or not self.industry_name or not self.market_cap_bucket:
            raise ValueError("classification observation fields cannot be empty")


ClassificationInput = dict[str, tuple[str, str]] | tuple[ClassificationObservation, ...]


def _classification_timeline(
    request: RunnerRequest,
    classifications: ClassificationInput,
) -> tuple[ClassificationObservation, ...]:
    if isinstance(classifications, dict):
        timeline = tuple(
            ClassificationObservation(
                instrument_id=instrument_id,
                effective_at=request.initial_time,
                industry_name=labels[0],
                market_cap_bucket=labels[1],
            )
            for instrument_id, labels in sorted(classifications.items())
        )
    else:
        timeline = classifications
    keys = tuple((item.effective_at, item.instrument_id) for item in timeline)
    instrument_ids = {item.instrument_id for item in request.instruments}
    if (
        not timeline
        or keys != tuple(sorted(set(keys)))
        or {item.instrument_id for item in timeline} != instrument_ids
        or any(item.instrument_id not in instrument_ids for item in timeline)
    ):
        raise E2ERunnerError("classification timeline must canonically cover runner instruments")
    return timeline


def _classification_payload(
    timeline: tuple[ClassificationObservation, ...],
) -> list[dict[str, str]]:
    return [
        {
            "instrument_id": item.instrument_id,
            "effective_at": item.effective_at.isoformat(),
            "industry_name": item.industry_name,
            "market_cap_bucket": item.market_cap_bucket,
        }
        for item in timeline
    ]


def _exact_weights(values: dict[str, int], total: int) -> tuple[tuple[str, Decimal], ...]:
    if not values or total <= 0 or sum(values.values()) != total:
        raise E2ERunnerError("exposure values do not reconcile to NAV")
    labels = sorted(values)
    assigned = Decimal(0)
    weights: list[tuple[str, Decimal]] = []
    for label in labels[:-1]:
        weight = (Decimal(values[label]) / Decimal(total)).quantize(Decimal("0.00000001"))
        weights.append((label, weight))
        assigned += weight
    weights.append((labels[-1], Decimal(1) - assigned))
    return tuple(weights)


def _authoritative_exposures(
    result: _RunnerResult,
    classifications: tuple[ClassificationObservation, ...],
) -> tuple[ExposureObservation, ...]:
    by_instrument: dict[str, list[ClassificationObservation]] = {}
    for classification in classifications:
        by_instrument.setdefault(classification.instrument_id, []).append(classification)
    observations: list[ExposureObservation] = []
    for item in result.nav:
        if item.event_kind != "bar_close":
            continue
        industry: dict[str, int] = {}
        market_cap: dict[str, int] = {}
        cash = int(item.cash_scaled)
        if cash > 0:
            industry["cash"] = cash
            market_cap["cash"] = cash
        for position in item.positions:
            eligible = [
                value
                for value in by_instrument.get(position.instrument_id, [])
                if value.effective_at <= item.event_time
            ]
            if not eligible:
                raise E2ERunnerError("position lacks event-time exposure classification")
            classification = eligible[-1]
            industry_name = classification.industry_name
            market_cap_name = classification.market_cap_bucket
            value = int(position.market_value_scaled)
            industry[industry_name] = industry.get(industry_name, 0) + value
            market_cap[market_cap_name] = market_cap.get(market_cap_name, 0) + value
        nav = int(item.net_asset_value_scaled)
        observations.append(
            ExposureObservation(
                event_time=item.event_time,
                industry_weights=_exact_weights(industry, nav),
                market_cap_weights=_exact_weights(market_cap, nav),
            )
        )
    return tuple(observations)


def _performance_and_trade(
    result: _RunnerResult,
) -> tuple[PerformanceSeries, TradeSummary]:
    close_nav = tuple(item for item in result.nav if item.event_kind == "bar_close")
    if len(close_nav) < 2:
        raise E2ERunnerError("report requires at least two close NAV observations")
    performance = PerformanceSeries(
        event_times=tuple(item.event_time for item in close_nav),
        net_asset_values=tuple(Decimal(item.net_asset_value_scaled) / _SCALE for item in close_nav),
        cash_values=tuple(Decimal(item.cash_scaled) / _SCALE for item in close_nav),
        market_values=tuple(Decimal(item.market_value_scaled) / _SCALE for item in close_nav),
    )
    gross_notional = sum(int(fill.price_scaled) * int(fill.quantity) for fill in result.fills)
    average_nav = sum(int(item.net_asset_value_scaled) for item in close_nav) // len(close_nav)
    trade_summary = TradeSummary.from_scaled_values(
        gross_traded_notional_scaled=gross_notional,
        commission_scaled=sum(int(fill.commission_scaled) for fill in result.fills),
        tax_scaled=sum(int(fill.tax_scaled) for fill in result.fills),
        transfer_fee_scaled=sum(int(fill.transfer_fee_scaled) for fill in result.fills),
        slippage_scaled=sum(int(fill.slippage_scaled) for fill in result.fills),
        average_nav_scaled=average_nav,
    )
    return performance, trade_summary


def _exposure_payload(
    exposures: tuple[ExposureObservation, ...],
) -> list[dict[str, object]]:
    return [
        {
            "event_time": item.event_time.isoformat(),
            "industry_weights": [[name, str(value)] for name, value in item.industry_weights],
            "market_cap_weights": [[name, str(value)] for name, value in item.market_cap_weights],
        }
        for item in exposures
    ]


def _canonical_json(payload: object) -> bytes:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode(
        "utf-8"
    )


def _write_atomic(path: Path, payload: bytes) -> None:
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_bytes(payload)
    temporary.replace(path)


class E2ERunnerClient:
    def __init__(self, *, command: tuple[str, ...]) -> None:
        if not command or any(not part for part in command):
            raise ValueError("runner command cannot be empty")
        self.command = command

    def execute(
        self,
        request: RunnerRequest,
        *,
        output_dir: Path,
        benchmarks: tuple[BenchmarkSeries, ...],
        classifications: ClassificationInput,
        snapshot_manifests: tuple[SnapshotManifest, ...],
        factor_artifacts: tuple[FactorArtifact, ...] = (),
        snapshot_data_root: Path | None = None,
        annual_sessions: int = 252,
    ) -> E2ERunBundle:
        if annual_sessions <= 0:
            raise ValueError("annual sessions must be positive")
        request_bytes = _canonical_json(request.model_dump(mode="json"))
        request_sha256 = hashlib.sha256(request_bytes).hexdigest()
        completed = subprocess.run(
            self.command,
            input=request_bytes,
            capture_output=True,
            check=False,
        )
        if completed.returncode != 0:
            diagnostic = completed.stderr.decode("utf-8", errors="replace")[-2000:]
            raise E2ERunnerError(f"Rust runner exited with {completed.returncode}: {diagnostic}")
        try:
            result = _RunnerResult.model_validate_json(completed.stdout, strict=True)
        except ValueError as error:
            raise E2ERunnerError("Rust runner returned an invalid result") from error
        if (
            result.run_id != request.run_id
            or result.strategy_id != request.strategy_id
            or result.snapshot_id != request.snapshot_id
            or result.request_sha256 != request_sha256
            or result.event_count != len(result.nav)
            or result.signal_count != len(request.signals)
            or result.rejection_count != len(result.rejections)
            or result.signal_count != len(result.signals)
            or result.order_count != len(result.orders)
            or result.accepted_order_count != len(result.accepted_orders)
            or result.execution_count != len(result.executions)
            or result.ledger_effect_count != len(result.ledger_effects)
            or result.event_count != len(result.account_snapshots)
        ):
            raise E2ERunnerError("Rust result identity or counts do not match request")

        expected_snapshot_ids = (
            {request.snapshot_id}
            | {signal.snapshot_id for signal in request.signals}
            | {item.manifest.input_snapshot_id for item in factor_artifacts}
        )
        manifests = {item.snapshot_id: item for item in snapshot_manifests}
        if set(manifests) != expected_snapshot_ids or len(manifests) != len(snapshot_manifests):
            raise E2ERunnerError("snapshot manifests do not exactly cover runner provenance")
        classification_timeline = _classification_timeline(request, classifications)

        performance, trade_summary = _performance_and_trade(result)
        report_times = performance.event_times
        if any(item.event_times != report_times for item in benchmarks):
            raise E2ERunnerError("benchmark sessions do not match close NAV")
        exposures = _authoritative_exposures(result, classification_timeline)
        if tuple(item.event_time for item in exposures) != report_times:
            raise E2ERunnerError("authoritative exposure sessions do not match close NAV")

        output_dir.mkdir(parents=True, exist_ok=True)
        request_path = output_dir / "runner-request.json"
        result_path = output_dir / "runner-result.json"
        report_path = output_dir / "report.html"
        report_input_path = output_dir / "report-input.json"
        manifest_path = output_dir / "bundle-manifest.json"
        snapshots_dir = output_dir / "snapshots"
        snapshot_objects_dir = output_dir / "snapshot-objects"
        factor_definitions_dir = output_dir / "factor-definitions"
        factor_materializations_dir = output_dir / "factor-materializations"
        factor_objects_dir = output_dir / "factor-objects"
        snapshots_dir.mkdir(parents=True, exist_ok=True)
        snapshot_objects_dir.mkdir(parents=True, exist_ok=True)
        factor_definitions_dir.mkdir(parents=True, exist_ok=True)
        factor_materializations_dir.mkdir(parents=True, exist_ok=True)
        factor_objects_dir.mkdir(parents=True, exist_ok=True)
        _write_atomic(request_path, request_bytes)
        _write_atomic(result_path, completed.stdout)
        snapshot_descriptors: list[dict[str, object]] = []
        object_descriptors: dict[str, dict[str, object]] = {}
        for snapshot_id in sorted(manifests):
            snapshot = manifests[snapshot_id]
            snapshot_bytes = _canonical_json(snapshot.model_dump(mode="json"))
            snapshot_path = snapshots_dir / f"{snapshot_id}.json"
            _write_atomic(snapshot_path, snapshot_bytes)
            snapshot_descriptors.append(
                {
                    "snapshot_id": snapshot_id,
                    "path": snapshot_path.relative_to(output_dir).as_posix(),
                    "sha256": hashlib.sha256(snapshot_bytes).hexdigest(),
                }
            )
            for obj in snapshot.objects:
                if snapshot_data_root is None:
                    raise E2ERunnerError("object-backed snapshots require a data root")
                source = (snapshot_data_root / obj.uri).resolve()
                try:
                    source.relative_to(snapshot_data_root.resolve())
                except ValueError as error:
                    raise E2ERunnerError("snapshot object escapes data root") from error
                payload = source.read_bytes()
                if hashlib.sha256(payload).hexdigest() != obj.sha256:
                    raise E2ERunnerError("snapshot object content hash mismatch")
                target = snapshot_objects_dir / f"{obj.sha256}.parquet"
                if not target.exists():
                    temporary = target.with_name(f".{target.name}.tmp")
                    temporary.write_bytes(payload)
                    temporary.replace(target)
                object_descriptors[obj.sha256] = {
                    "sha256": obj.sha256,
                    "path": target.relative_to(output_dir).as_posix(),
                    "row_count": obj.row_count,
                }
        factor_definition_descriptors: dict[str, dict[str, object]] = {}
        factor_materialization_descriptors: dict[str, dict[str, object]] = {}
        factor_object_descriptors: dict[str, dict[str, object]] = {}
        for artifact in factor_artifacts:
            definition_bytes = _canonical_json(artifact.definition.model_dump(mode="json"))
            definition_path = (
                factor_definitions_dir / f"{artifact.definition.definition_sha256}.json"
            )
            if not definition_path.exists():
                _write_atomic(definition_path, definition_bytes)
            elif definition_path.read_bytes() != definition_bytes:
                raise E2ERunnerError("factor definition identity collision")
            factor_definition_descriptors[artifact.definition.definition_sha256] = {
                "factor_id": artifact.definition.factor_id,
                "factor_version": artifact.definition.version,
                "definition_sha256": artifact.definition.definition_sha256,
                "path": definition_path.relative_to(output_dir).as_posix(),
                "sha256": hashlib.sha256(definition_bytes).hexdigest(),
            }

            materialization_bytes = _canonical_json(artifact.manifest.model_dump(mode="json"))
            materialization_path = (
                factor_materializations_dir / f"{artifact.manifest.materialization_id}.json"
            )
            _write_atomic(materialization_path, materialization_bytes)
            factor_materialization_descriptors[artifact.manifest.materialization_id] = {
                "materialization_id": artifact.manifest.materialization_id,
                "path": materialization_path.relative_to(output_dir).as_posix(),
                "sha256": hashlib.sha256(materialization_bytes).hexdigest(),
            }

            factor_payload = artifact.path.read_bytes()
            if hashlib.sha256(factor_payload).hexdigest() != artifact.manifest.output_sha256:
                raise E2ERunnerError("factor artifact content hash mismatch")
            factor_path = factor_objects_dir / f"{artifact.manifest.output_sha256}.parquet"
            if not factor_path.exists():
                _write_atomic(factor_path, factor_payload)
            elif factor_path.read_bytes() != factor_payload:
                raise E2ERunnerError("factor artifact identity collision")
            factor_object_descriptors[artifact.manifest.output_sha256] = {
                "sha256": artifact.manifest.output_sha256,
                "path": factor_path.relative_to(output_dir).as_posix(),
                "row_count": artifact.manifest.output_row_count,
            }
        report_input = {
            "annual_sessions": annual_sessions,
            "benchmarks": [
                {
                    "benchmark_id": item.benchmark_id,
                    "event_times": [value.isoformat() for value in item.event_times],
                    "close_values": [str(value) for value in item.close_values],
                }
                for item in benchmarks
            ],
            "exposures": _exposure_payload(exposures),
            "classification_timeline": _classification_payload(classification_timeline),
        }
        report_input_bytes = _canonical_json(report_input)
        _write_atomic(report_input_path, report_input_bytes)
        build_html_report(
            report_path,
            identity=ReportIdentity(
                run_id=request.run_id,
                strategy_id=request.strategy_id,
                snapshot_id=request.snapshot_id,
                benchmark_ids=tuple(sorted(item.benchmark_id for item in benchmarks)),
            ),
            strategy=performance,
            benchmarks=benchmarks,
            trade_summary=trade_summary,
            exposures=exposures,
            annual_sessions=annual_sessions,
        )
        result_sha256 = hashlib.sha256(completed.stdout).hexdigest()
        report_input_sha256 = hashlib.sha256(report_input_bytes).hexdigest()
        report_sha256 = hashlib.sha256(report_path.read_bytes()).hexdigest()
        bundle_manifest = {
            "schema_id": "trademaster.e2e-bundle/v2",
            "run_id": request.run_id,
            "strategy_id": request.strategy_id,
            "snapshot_id": request.snapshot_id,
            "signal_snapshot_ids": sorted({signal.snapshot_id for signal in request.signals}),
            "snapshots": snapshot_descriptors,
            "snapshot_objects": [object_descriptors[key] for key in sorted(object_descriptors)],
            "factor_definitions": [
                factor_definition_descriptors[key] for key in sorted(factor_definition_descriptors)
            ],
            "factor_materializations": [
                factor_materialization_descriptors[key]
                for key in sorted(factor_materialization_descriptors)
            ],
            "factor_objects": [
                factor_object_descriptors[key] for key in sorted(factor_object_descriptors)
            ],
            "request_sha256": request_sha256,
            "result_sha256": result_sha256,
            "report_sha256": report_sha256,
            "request": {"path": request_path.name, "sha256": request_sha256},
            "result": {"path": result_path.name, "sha256": result_sha256},
            "report_input": {
                "path": report_input_path.name,
                "sha256": report_input_sha256,
            },
            "report": {"path": report_path.name, "sha256": report_sha256},
        }
        _write_atomic(manifest_path, _canonical_json(bundle_manifest))
        return E2ERunBundle(
            request_path=request_path,
            result_path=result_path,
            report_input_path=report_input_path,
            manifest_path=manifest_path,
            report_path=report_path,
            request_sha256=request_sha256,
            result_sha256=result_sha256,
        )


def attach_e2e_bundle_artifacts(
    manifest_path: Path,
    *,
    artifacts: dict[str, Path],
) -> None:
    """Hash-bind strategy summaries and human reports after the core run completes."""

    if (
        not artifacts
        or tuple(artifacts) != tuple(sorted(set(artifacts)))
        or any(not name for name in artifacts)
    ):
        raise ValueError("additional bundle artifact names must be nonempty and sorted")
    root = manifest_path.resolve().parent
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise E2ERunnerError("bundle manifest is unreadable") from error
    if (
        not isinstance(manifest, dict)
        or manifest.get("schema_id") != "trademaster.e2e-bundle/v2"
        or "additional_artifacts" in manifest
    ):
        raise E2ERunnerError("only a v2 bundle can be finalized once")
    descriptors: dict[str, dict[str, str]] = {}
    for name, raw_path in artifacts.items():
        path = raw_path.resolve()
        try:
            relative = path.relative_to(root)
        except ValueError as error:
            raise E2ERunnerError("additional artifact escapes bundle root") from error
        payload = path.read_bytes()
        descriptors[name] = {
            "path": relative.as_posix(),
            "sha256": hashlib.sha256(payload).hexdigest(),
        }
    manifest["schema_id"] = "trademaster.e2e-bundle/v3"
    manifest["additional_artifacts"] = descriptors
    _write_atomic(manifest_path, _canonical_json(manifest))


def verify_e2e_bundle(manifest_path: Path, *, runner_command: tuple[str, ...]) -> E2ERunBundle:
    """Re-read snapshots, replay Rust, and rebuild the report for a bundle."""
    root = manifest_path.resolve().parent
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise E2ERunnerError("bundle manifest is unreadable") from error
    v1_keys = {
        "schema_id",
        "run_id",
        "strategy_id",
        "snapshot_id",
        "signal_snapshot_ids",
        "snapshots",
        "snapshot_objects",
        "request_sha256",
        "result_sha256",
        "report_sha256",
        "request",
        "result",
        "report_input",
        "report",
    }
    v2_keys = v1_keys | {
        "factor_definitions",
        "factor_materializations",
        "factor_objects",
    }
    v3_keys = v2_keys | {"additional_artifacts"}
    if not isinstance(manifest, dict) or (
        (manifest.get("schema_id") == "trademaster.e2e-bundle/v1" and set(manifest) != v1_keys)
        or (manifest.get("schema_id") == "trademaster.e2e-bundle/v2" and set(manifest) != v2_keys)
        or (manifest.get("schema_id") == "trademaster.e2e-bundle/v3" and set(manifest) != v3_keys)
        or manifest.get("schema_id")
        not in {
            "trademaster.e2e-bundle/v1",
            "trademaster.e2e-bundle/v2",
            "trademaster.e2e-bundle/v3",
        }
    ):
        raise E2ERunnerError("bundle manifest schema is invalid")
    bundle_with_factors = manifest["schema_id"] in {
        "trademaster.e2e-bundle/v2",
        "trademaster.e2e-bundle/v3",
    }
    bundle_v3 = manifest["schema_id"] == "trademaster.e2e-bundle/v3"

    def verified_path(name: str) -> tuple[Path, str]:
        descriptor = manifest[name]
        if not isinstance(descriptor, dict) or set(descriptor) != {"path", "sha256"}:
            raise E2ERunnerError(f"bundle {name} descriptor is invalid")
        relative = Path(str(descriptor["path"]))
        if relative.is_absolute():
            raise E2ERunnerError(f"bundle {name} path must be relative")
        path = (root / relative).resolve()
        try:
            path.relative_to(root)
        except ValueError as error:
            raise E2ERunnerError(f"bundle {name} path escapes its root") from error
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        if digest != descriptor["sha256"]:
            raise E2ERunnerError(f"bundle {name} hash mismatch")
        return path, digest

    request_path, request_sha256 = verified_path("request")
    result_path, result_sha256 = verified_path("result")
    report_input_path, _ = verified_path("report_input")
    report_path, report_sha256 = verified_path("report")
    if bundle_v3:
        additional = manifest["additional_artifacts"]
        if (
            not isinstance(additional, dict)
            or not additional
            or tuple(additional) != tuple(sorted(additional))
        ):
            raise E2ERunnerError("additional bundle artifacts are invalid")
        for name, descriptor in additional.items():
            if not isinstance(name, str) or not name or not isinstance(descriptor, dict):
                raise E2ERunnerError("additional bundle artifact is invalid")
            if set(descriptor) != {"path", "sha256"}:
                raise E2ERunnerError("additional bundle artifact descriptor is invalid")
            relative = Path(str(descriptor["path"]))
            path = (root / relative).resolve()
            try:
                path.relative_to(root)
            except ValueError as error:
                raise E2ERunnerError("additional artifact path escapes bundle") from error
            if hashlib.sha256(path.read_bytes()).hexdigest() != descriptor["sha256"]:
                raise E2ERunnerError("additional bundle artifact hash mismatch")
    try:
        request = RunnerRequest.model_validate_json(request_path.read_bytes(), strict=True)
        result = _RunnerResult.model_validate_json(result_path.read_bytes(), strict=True)
    except ValueError as error:
        raise E2ERunnerError("bundle request or result is invalid") from error
    signal_snapshot_ids = sorted({item.snapshot_id for item in request.signals})
    expected_snapshot_ids = {request.snapshot_id, *signal_snapshot_ids}
    snapshot_manifests: dict[str, SnapshotManifest] = {}
    if not isinstance(manifest["snapshots"], list):
        raise E2ERunnerError("bundle snapshots must be a list")
    for descriptor in manifest["snapshots"]:
        if not isinstance(descriptor, dict) or set(descriptor) != {
            "snapshot_id",
            "path",
            "sha256",
        }:
            raise E2ERunnerError("snapshot descriptor is invalid")
        snapshot_id = str(descriptor["snapshot_id"])
        relative = Path(str(descriptor["path"]))
        path = (root / relative).resolve()
        try:
            path.relative_to(root)
        except ValueError as error:
            raise E2ERunnerError("snapshot path escapes bundle") from error
        payload = path.read_bytes()
        if hashlib.sha256(payload).hexdigest() != descriptor["sha256"]:
            raise E2ERunnerError("snapshot manifest hash mismatch")
        try:
            snapshot = SnapshotManifest.model_validate_json(payload, strict=True)
        except ValueError as error:
            raise E2ERunnerError("snapshot manifest is invalid") from error
        if snapshot.snapshot_id != snapshot_id or snapshot_id in snapshot_manifests:
            raise E2ERunnerError("snapshot identity is duplicated or inconsistent")
        snapshot_manifests[snapshot_id] = snapshot
    if not bundle_with_factors and set(snapshot_manifests) != expected_snapshot_ids:
        raise E2ERunnerError("bundle snapshots do not cover request provenance")

    object_descriptors: dict[str, dict[str, object]] = {}
    if not isinstance(manifest["snapshot_objects"], list):
        raise E2ERunnerError("bundle snapshot objects must be a list")
    for descriptor in manifest["snapshot_objects"]:
        if not isinstance(descriptor, dict) or set(descriptor) != {
            "sha256",
            "path",
            "row_count",
        }:
            raise E2ERunnerError("snapshot object descriptor is invalid")
        sha256 = str(descriptor["sha256"])
        if sha256 in object_descriptors:
            raise E2ERunnerError("snapshot object descriptor is duplicated")
        relative = Path(str(descriptor["path"]))
        path = (root / relative).resolve()
        try:
            path.relative_to(root)
        except ValueError as error:
            raise E2ERunnerError("snapshot object path escapes bundle") from error
        payload = path.read_bytes()
        if hashlib.sha256(payload).hexdigest() != sha256:
            raise E2ERunnerError("snapshot object hash mismatch")
        if pq.read_table(path).num_rows != descriptor["row_count"]:
            raise E2ERunnerError("snapshot object row count mismatch")
        object_descriptors[sha256] = descriptor
    referenced_objects = {
        obj.sha256 for snapshot in snapshot_manifests.values() for obj in snapshot.objects
    }
    if set(object_descriptors) != referenced_objects or any(
        object_descriptors[obj.sha256]["row_count"] != obj.row_count
        for snapshot in snapshot_manifests.values()
        for obj in snapshot.objects
    ):
        raise E2ERunnerError("snapshot object evidence is incomplete")

    if bundle_with_factors:
        raw_definitions = manifest["factor_definitions"]
        raw_materializations = manifest["factor_materializations"]
        raw_factor_objects = manifest["factor_objects"]
        if not all(
            isinstance(value, list)
            for value in (raw_definitions, raw_materializations, raw_factor_objects)
        ):
            raise E2ERunnerError("factor bundle descriptors must be lists")

        factor_definitions: dict[str, FactorDefinition] = {}
        for descriptor in raw_definitions:
            if not isinstance(descriptor, dict) or set(descriptor) != {
                "factor_id",
                "factor_version",
                "definition_sha256",
                "path",
                "sha256",
            }:
                raise E2ERunnerError("factor definition descriptor is invalid")
            definition_sha256 = str(descriptor["definition_sha256"])
            relative = Path(str(descriptor["path"]))
            path = (root / relative).resolve()
            try:
                path.relative_to(root)
            except ValueError as error:
                raise E2ERunnerError("factor definition path escapes bundle") from error
            payload = path.read_bytes()
            if hashlib.sha256(payload).hexdigest() != descriptor["sha256"]:
                raise E2ERunnerError("factor definition file hash mismatch")
            try:
                definition = FactorDefinition.model_validate_json(payload, strict=True)
            except ValueError as error:
                raise E2ERunnerError("factor definition is invalid") from error
            if (
                definition.definition_sha256 != definition_sha256
                or definition.factor_id != descriptor["factor_id"]
                or definition.version != descriptor["factor_version"]
                or definition_sha256 in factor_definitions
            ):
                raise E2ERunnerError("factor definition identity is inconsistent")
            factor_definitions[definition_sha256] = definition

        factor_object_tables: dict[str, pa.Table] = {}
        for descriptor in raw_factor_objects:
            if not isinstance(descriptor, dict) or set(descriptor) != {
                "sha256",
                "path",
                "row_count",
            }:
                raise E2ERunnerError("factor object descriptor is invalid")
            sha256 = str(descriptor["sha256"])
            relative = Path(str(descriptor["path"]))
            path = (root / relative).resolve()
            try:
                path.relative_to(root)
            except ValueError as error:
                raise E2ERunnerError("factor object path escapes bundle") from error
            payload = path.read_bytes()
            if hashlib.sha256(payload).hexdigest() != sha256:
                raise E2ERunnerError("factor object hash mismatch")
            try:
                table = pq.read_table(path)
            except Exception as error:
                raise E2ERunnerError("factor object is unreadable") from error
            if table.num_rows != descriptor["row_count"] or sha256 in factor_object_tables:
                raise E2ERunnerError("factor object rows or identity are inconsistent")
            factor_object_tables[sha256] = table

        factor_materializations: dict[str, FactorMaterialization] = {}
        for descriptor in raw_materializations:
            if not isinstance(descriptor, dict) or set(descriptor) != {
                "materialization_id",
                "path",
                "sha256",
            }:
                raise E2ERunnerError("factor materialization descriptor is invalid")
            materialization_id = str(descriptor["materialization_id"])
            relative = Path(str(descriptor["path"]))
            path = (root / relative).resolve()
            try:
                path.relative_to(root)
            except ValueError as error:
                raise E2ERunnerError("factor materialization path escapes bundle") from error
            payload = path.read_bytes()
            if hashlib.sha256(payload).hexdigest() != descriptor["sha256"]:
                raise E2ERunnerError("factor materialization file hash mismatch")
            try:
                materialization = FactorMaterialization.model_validate_json(payload, strict=True)
            except ValueError as error:
                raise E2ERunnerError("factor materialization is invalid") from error
            if (
                materialization.materialization_id != materialization_id
                or materialization_id in factor_materializations
                or materialization.definition_sha256 not in factor_definitions
                or materialization.input_snapshot_id not in snapshot_manifests
                or materialization.output_sha256 not in factor_object_tables
                or factor_object_tables[materialization.output_sha256].num_rows
                != materialization.output_row_count
            ):
                raise E2ERunnerError("factor materialization lineage is incomplete")
            metadata = factor_object_tables[materialization.output_sha256].schema.metadata or {}
            expected_factor_metadata = {
                b"trademaster.factor.definition_sha256": (
                    materialization.definition_sha256.encode()
                ),
                b"trademaster.factor.code_sha256": materialization.code_sha256.encode(),
                b"trademaster.factor.parameters_sha256": (
                    materialization.parameters_sha256.encode()
                ),
                b"trademaster.factor.request_sha256": (materialization.request_sha256.encode()),
            }
            if any(metadata.get(key) != value for key, value in expected_factor_metadata.items()):
                raise E2ERunnerError("factor object metadata differs from lineage")
            factor_materializations[materialization_id] = materialization

        if set(factor_definitions) != {
            item.definition_sha256 for item in factor_materializations.values()
        } or set(factor_object_tables) != {
            item.output_sha256 for item in factor_materializations.values()
        }:
            raise E2ERunnerError("factor bundle contains unreferenced evidence")
        for materialization in factor_materializations.values():
            for parent in materialization.parents:
                actual_parent = factor_materializations.get(parent.materialization_id)
                if (
                    actual_parent is None
                    or actual_parent.factor_id != parent.factor_id
                    or actual_parent.factor_version != parent.factor_version
                    or actual_parent.definition_sha256 != parent.definition_sha256
                    or actual_parent.output_sha256 != parent.output_sha256
                ):
                    raise E2ERunnerError("factor parent lineage is incomplete")
        expected_snapshot_ids.update(
            item.input_snapshot_id for item in factor_materializations.values()
        )
        if set(snapshot_manifests) != expected_snapshot_ids:
            raise E2ERunnerError("bundle snapshots do not cover factor provenance")

    replay = subprocess.run(
        runner_command,
        input=request_path.read_bytes(),
        capture_output=True,
        check=False,
    )
    if replay.returncode != 0 or replay.stdout != result_path.read_bytes():
        raise E2ERunnerError("persisted result differs from authoritative Rust replay")

    try:
        report_input = json.loads(report_input_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise E2ERunnerError("report input is invalid") from error
    legacy_report_keys = {
        "annual_sessions",
        "benchmarks",
        "exposures",
        "classifications",
    }
    timeline_report_keys = {
        "annual_sessions",
        "benchmarks",
        "exposures",
        "classification_timeline",
    }
    if not isinstance(report_input, dict) or frozenset(report_input) not in {
        frozenset(legacy_report_keys),
        frozenset(timeline_report_keys),
    }:
        raise E2ERunnerError("report input schema is invalid")
    annual_sessions = report_input["annual_sessions"]
    if (
        not isinstance(annual_sessions, int)
        or isinstance(annual_sessions, bool)
        or annual_sessions <= 0
    ):
        raise E2ERunnerError("report annual sessions are invalid")
    if "classifications" in report_input:
        raw_classifications = report_input["classifications"]
        if not isinstance(raw_classifications, dict):
            raise E2ERunnerError("report classifications are invalid")
        classifications: dict[str, tuple[str, str]] = {}
        for instrument_id, labels in raw_classifications.items():
            if (
                not isinstance(instrument_id, str)
                or not isinstance(labels, list)
                or len(labels) != 2
                or not all(isinstance(value, str) and value for value in labels)
            ):
                raise E2ERunnerError("report classification entry is invalid")
            classifications[instrument_id] = (labels[0], labels[1])
        classification_timeline = _classification_timeline(request, classifications)
    else:
        raw_timeline = report_input["classification_timeline"]
        if not isinstance(raw_timeline, list):
            raise E2ERunnerError("report classification timeline is invalid")
        try:
            classification_timeline = _classification_timeline(
                request,
                tuple(
                    ClassificationObservation(
                        instrument_id=item["instrument_id"],
                        effective_at=datetime.fromisoformat(item["effective_at"]),
                        industry_name=item["industry_name"],
                        market_cap_bucket=item["market_cap_bucket"],
                    )
                    for item in raw_timeline
                    if isinstance(item, dict)
                    and set(item)
                    == {
                        "instrument_id",
                        "effective_at",
                        "industry_name",
                        "market_cap_bucket",
                    }
                ),
            )
        except (KeyError, TypeError, ValueError) as error:
            raise E2ERunnerError("report classification timeline entry is invalid") from error
        if len(classification_timeline) != len(raw_timeline):
            raise E2ERunnerError("report classification timeline entry is invalid")
    exposures = _authoritative_exposures(result, classification_timeline)
    if report_input["exposures"] != _exposure_payload(exposures):
        raise E2ERunnerError("report exposures differ from authoritative positions")
    benchmarks: list[BenchmarkSeries] = []
    if not isinstance(report_input["benchmarks"], list):
        raise E2ERunnerError("report benchmarks are invalid")
    try:
        for item in report_input["benchmarks"]:
            benchmarks.append(
                BenchmarkSeries(
                    benchmark_id=str(item["benchmark_id"]),
                    event_times=tuple(
                        datetime.fromisoformat(value) for value in item["event_times"]
                    ),
                    close_values=tuple(Decimal(value) for value in item["close_values"]),
                )
            )
    except (KeyError, TypeError, ValueError) as error:
        raise E2ERunnerError("report benchmark entry is invalid") from error
    performance, trade_summary = _performance_and_trade(result)
    with tempfile.TemporaryDirectory(prefix="trademaster-report-verify-") as temporary:
        rebuilt = Path(temporary) / "report.html"
        build_html_report(
            rebuilt,
            identity=ReportIdentity(
                run_id=request.run_id,
                strategy_id=request.strategy_id,
                snapshot_id=request.snapshot_id,
                benchmark_ids=tuple(sorted(item.benchmark_id for item in benchmarks)),
            ),
            strategy=performance,
            benchmarks=tuple(benchmarks),
            trade_summary=trade_summary,
            exposures=exposures,
            annual_sessions=annual_sessions,
        )
        if rebuilt.read_bytes() != report_path.read_bytes():
            raise E2ERunnerError("report differs from authoritative rebuild")

    if (
        request.run_id != result.run_id
        or request.strategy_id != result.strategy_id
        or request.snapshot_id != result.snapshot_id
        or request_sha256 != result.request_sha256
        or manifest["run_id"] != request.run_id
        or manifest["strategy_id"] != request.strategy_id
        or manifest["snapshot_id"] != request.snapshot_id
        or manifest["signal_snapshot_ids"] != signal_snapshot_ids
        or manifest["request_sha256"] != request_sha256
        or manifest["result_sha256"] != result_sha256
        or manifest["report_sha256"] != report_sha256
    ):
        raise E2ERunnerError("bundle identities disagree")
    return E2ERunBundle(
        request_path=request_path,
        result_path=result_path,
        report_input_path=report_input_path,
        manifest_path=manifest_path.resolve(),
        report_path=report_path,
        request_sha256=request_sha256,
        result_sha256=result_sha256,
    )


def allocate_target_weights(
    signals: pa.Table,
    *,
    target_nav_scaled: int,
    open_prices_scaled: dict[str, int],
    lot_sizes: dict[str, int],
) -> pa.Table:
    """Convert target weights to deterministic lot-sized target quantities."""
    if signals.schema.remove_metadata() != signal_output_schema():
        raise ValueError("target-weight signal schema drift")
    if target_nav_scaled <= 0:
        raise ValueError("target NAV must be positive")
    rows = signals.to_pylist()
    instruments = tuple(str(row["instrument_id"]) for row in rows)
    if len(instruments) != len(set(instruments)):
        raise ValueError("target-weight instruments must be unique")
    if set(open_prices_scaled) != set(instruments) or set(lot_sizes) != set(instruments):
        raise ValueError("allocator prices and lot sizes must exactly cover signals")
    weights = tuple(Decimal(row["value"]) for row in rows)
    if (
        any(row["intent_type"] != "target_weight" for row in rows)
        or any(weight < 0 or not weight.is_finite() for weight in weights)
        or sum(weights, Decimal(0)) != Decimal(1)
        or any(open_prices_scaled[item] <= 0 or lot_sizes[item] <= 0 for item in instruments)
    ):
        raise ValueError("target weights or allocation inputs are invalid")
    output: list[dict[str, object]] = []
    quantum = Decimal("0.00000001")
    for row, weight in zip(rows, weights, strict=True):
        instrument_id = str(row["instrument_id"])
        raw_quantity = int(
            (
                Decimal(target_nav_scaled) * weight / Decimal(open_prices_scaled[instrument_id])
            ).to_integral_value(rounding=ROUND_DOWN)
        )
        lot_size = lot_sizes[instrument_id]
        quantity = raw_quantity // lot_size * lot_size
        identity = {
            "source_signal_id": str(row["signal_id"]),
            "target_quantity": quantity,
        }
        output.append(
            {
                **row,
                "signal_id": hashlib.sha256(_canonical_json(identity)).hexdigest(),
                "intent_type": "quantity",
                "value": Decimal(quantity).quantize(quantum),
                "reason": f"allocated_lot_quantity:{row['reason']}",
            }
        )
    return pa.Table.from_pylist(output, schema=signal_output_schema()).replace_schema_metadata(
        signals.schema.metadata
    )


__all__ = [
    "ClassificationObservation",
    "E2ERunBundle",
    "E2ERunnerClient",
    "E2ERunnerError",
    "RunnerBar",
    "RunnerEvent",
    "RunnerFeeSchedule",
    "RunnerInstrument",
    "RunnerRequest",
    "RunnerSignal",
    "allocate_target_weights",
    "attach_e2e_bundle_artifacts",
    "verify_e2e_bundle",
]
