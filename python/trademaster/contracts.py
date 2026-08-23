"""Stable Python protocols and Arrow artifact contracts."""

from __future__ import annotations

import hashlib
import importlib.resources
import json
import re
from collections.abc import Mapping
from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from itertools import pairwise
from pathlib import Path
from typing import Literal, Protocol, Self, cast, runtime_checkable

import pyarrow as pa
import pyarrow.parquet as pq
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


def _require_utc(value: datetime) -> datetime:
    offset = value.utcoffset()
    if value.tzinfo is None or offset is None:
        raise ValueError("datetime must be timezone-aware")
    if offset.total_seconds() != 0:
        raise ValueError("datetime must use UTC")
    return value


class DatasetRequest(BaseModel):
    """An inclusive ``[start, end]`` dataset request in UTC."""

    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    dataset: str
    start: datetime
    end: datetime
    instruments: tuple[str, ...] = ()
    fields: tuple[str, ...] = ()
    coverage_keys: tuple[str, ...] = ()

    _utc_times = field_validator("start", "end")(_require_utc)

    @model_validator(mode="after")
    def validate_range(self) -> Self:
        if self.end < self.start:
            raise ValueError("end must not be earlier than start")
        if self.instruments != tuple(sorted(set(self.instruments))):
            raise ValueError("instruments must be unique and canonically sorted")
        if self.fields != tuple(sorted(set(self.fields))):
            raise ValueError("fields must be unique and canonically sorted")
        if self.coverage_keys != tuple(sorted(set(self.coverage_keys))):
            raise ValueError("coverage_keys must be unique and canonically sorted")
        return self


class SnapshotRequest(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    datasets: tuple[DatasetRequest, ...]
    as_of: datetime

    _utc_as_of = field_validator("as_of")(_require_utc)

    @model_validator(mode="after")
    def validate_canonical_datasets(self) -> Self:
        keys = tuple(
            (
                item.dataset,
                item.start,
                item.end,
                item.instruments,
                item.fields,
                item.coverage_keys,
            )
            for item in self.datasets
        )
        if keys != tuple(sorted(set(keys))):
            raise ValueError("datasets must be unique and canonically sorted")
        if any(item.end > self.as_of for item in self.datasets):
            raise ValueError("dataset request cannot extend beyond snapshot as_of")
        return self


class CoverageResult(BaseModel):
    """Coverage for the request's inclusive interval; missing ranges are inclusive."""

    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    request: DatasetRequest
    complete: bool
    missing_ranges: tuple[tuple[datetime, datetime], ...] = ()
    missing_coverage_keys: tuple[str, ...] = ()
    covered_object_sha256s: tuple[str, ...] = ()

    @field_validator("missing_ranges")
    @classmethod
    def validate_missing_ranges(
        cls, ranges: tuple[tuple[datetime, datetime], ...]
    ) -> tuple[tuple[datetime, datetime], ...]:
        for start, end in ranges:
            _require_utc(start)
            _require_utc(end)
            if end < start:
                raise ValueError("missing range end must not precede start")
        if ranges != tuple(sorted(set(ranges))):
            raise ValueError("missing ranges must be unique and canonically sorted")
        if any(current[0] <= previous[1] for previous, current in pairwise(ranges)):
            raise ValueError("inclusive missing ranges must not overlap or touch")
        return ranges

    @model_validator(mode="after")
    def validate_completeness(self) -> Self:
        if self.missing_coverage_keys != tuple(sorted(set(self.missing_coverage_keys))):
            raise ValueError("missing coverage keys must be unique and sorted")
        if not set(self.missing_coverage_keys) <= set(self.request.coverage_keys):
            raise ValueError("missing coverage keys must belong to the request")
        if self.covered_object_sha256s != tuple(sorted(set(self.covered_object_sha256s))) or any(
            len(value) != 64
            or any(char not in "0123456789abcdef" for char in value)
            for value in self.covered_object_sha256s
        ):
            raise ValueError("covered objects must be sorted lowercase sha256 values")
        missing = bool(self.missing_ranges or self.missing_coverage_keys)
        if self.complete == missing:
            raise ValueError("complete must equal absence of all missing coverage")
        if self.complete and self.request.coverage_keys and not self.covered_object_sha256s:
            raise ValueError("complete keyed coverage requires object evidence")
        if any(
            start < self.request.start or end > self.request.end
            for start, end in self.missing_ranges
        ):
            raise ValueError("missing range must be contained by its request")
        return self


class AvailabilityPolicy(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    policy_id: str
    known_at_field: str
    publication_lag_policy_id: str


class DatasetCoverage(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    request: DatasetRequest
    complete: Literal[True] = True
    covered_start: datetime
    covered_end: datetime
    fields: tuple[str, ...]
    resolved_instrument_set_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    resolved_instrument_count: int = Field(ge=0)
    object_sha256s: tuple[str, ...]
    row_count: int = Field(ge=0)
    known_at_max: datetime | None

    @field_validator("object_sha256s")
    @classmethod
    def validate_object_digests(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        if values != tuple(sorted(set(values))):
            raise ValueError("coverage object digests must be unique and canonically sorted")
        if any(len(value) != 64 or any(char not in "0123456789abcdef" for char in value) for value in values):
            raise ValueError("coverage object digest must be lowercase sha256")
        return values

    @field_validator("known_at_max")
    @classmethod
    def validate_known_at(cls, value: datetime | None) -> datetime | None:
        return None if value is None else _require_utc(value)

    _utc_coverage = field_validator("covered_start", "covered_end")(_require_utc)

    @model_validator(mode="after")
    def validate_empty_coverage(self) -> Self:
        if self.covered_start != self.request.start or self.covered_end != self.request.end:
            raise ValueError("coverage interval must exactly match its request")
        if self.fields != self.request.fields:
            raise ValueError("coverage fields must exactly match its request")
        if self.request.instruments:
            expected = hashlib.sha256(
                json.dumps(
                    self.request.instruments, separators=(",", ":")
                ).encode()
            ).hexdigest()
            if self.resolved_instrument_count != len(self.request.instruments):
                raise ValueError("resolved instrument count does not match request")
            if self.resolved_instrument_set_sha256 != expected:
                raise ValueError("resolved instrument set does not match request")
        if self.object_sha256s and self.known_at_max is None:
            raise ValueError("object-backed coverage requires known_at")
        if not self.object_sha256s and (
            self.row_count > 0 or self.known_at_max is not None
        ):
            raise ValueError("coverage without objects cannot contain rows or known_at")
        return self


class SnapshotObject(BaseModel):
    """A content-addressed immutable Parquet object included in a snapshot."""

    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    dataset: str
    partition: str
    uri: str
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    schema_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    schema_version: str
    fields: tuple[str, ...]
    coverage_keys: tuple[str, ...]
    resolved_instrument_set_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    resolved_instrument_count: int = Field(ge=0)
    row_count: int = Field(ge=0)
    event_time_start: datetime
    event_time_end: datetime
    known_at_max: datetime

    _utc_times = field_validator(
        "event_time_start", "event_time_end", "known_at_max"
    )(_require_utc)

    @model_validator(mode="after")
    def validate_coverage(self) -> Self:
        if self.event_time_end < self.event_time_start:
            raise ValueError("event_time_end must not precede event_time_start")
        if self.fields != tuple(sorted(set(self.fields))):
            raise ValueError("snapshot object fields must be unique and sorted")
        if self.coverage_keys != tuple(sorted(set(self.coverage_keys))):
            raise ValueError("snapshot object coverage keys must be unique and sorted")
        return self


class SnapshotManifest(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    manifest_version: Literal["snapshot/v1"]
    snapshot_id: str = Field(pattern=r"^[0-9a-f]{64}$")
    as_of: datetime
    request: SnapshotRequest
    availability_policy: AvailabilityPolicy
    coverages: tuple[DatasetCoverage, ...]
    objects: tuple[SnapshotObject, ...]

    _utc_as_of = field_validator("as_of")(_require_utc)

    @classmethod
    def build(
        cls,
        *,
        request: SnapshotRequest,
        availability_policy: AvailabilityPolicy,
        objects: tuple[SnapshotObject, ...],
        coverages: tuple[DatasetCoverage, ...],
    ) -> Self:
        ordered = tuple(sorted(objects, key=cls._object_key))
        ordered_coverages = tuple(
            sorted(
                coverages,
                key=lambda coverage: (
                    coverage.request.dataset,
                    coverage.request.start,
                    coverage.request.end,
                    coverage.request.instruments,
                    coverage.request.fields,
                    coverage.request.coverage_keys,
                ),
            )
        )
        snapshot_id = cls._content_hash(
            "snapshot/v1", request, availability_policy, ordered_coverages, ordered
        )
        return cls(
            manifest_version="snapshot/v1",
            snapshot_id=snapshot_id,
            as_of=request.as_of,
            request=request,
            availability_policy=availability_policy,
            coverages=ordered_coverages,
            objects=ordered,
        )

    @staticmethod
    def _object_key(obj: SnapshotObject) -> tuple[str, str, str]:
        return (obj.dataset, obj.partition, obj.uri)

    @staticmethod
    def _content_hash(
        manifest_version: str,
        request: SnapshotRequest,
        availability_policy: AvailabilityPolicy,
        coverages: tuple[DatasetCoverage, ...],
        objects: tuple[SnapshotObject, ...],
    ) -> str:
        payload = {
            "manifest_version": manifest_version,
            "request": request.model_dump(mode="json"),
            "availability_policy": availability_policy.model_dump(mode="json"),
            "coverages": [coverage.model_dump(mode="json") for coverage in coverages],
            "objects": [obj.model_dump(mode="json") for obj in objects],
        }
        encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
        return hashlib.sha256(encoded).hexdigest()

    @model_validator(mode="after")
    def validate_identity_and_cutoff(self) -> Self:
        keys = tuple(self._object_key(obj) for obj in self.objects)
        if len(set(keys)) != len(keys):
            raise ValueError("duplicate snapshot object")
        if keys != tuple(sorted(keys)):
            raise ValueError("snapshot objects must use canonical ordering")
        if any(obj.known_at_max > self.as_of for obj in self.objects):
            raise ValueError("object known_at exceeds snapshot as_of")
        if self.as_of != self.request.as_of:
            raise ValueError("manifest as_of must equal request as_of")
        coverage_requests = tuple(coverage.request for coverage in self.coverages)
        if coverage_requests != self.request.datasets:
            raise ValueError("coverage must prove every requested dataset exactly once")
        if any(
            coverage.known_at_max is not None and coverage.known_at_max > self.as_of
            for coverage in self.coverages
        ):
            raise ValueError("coverage known_at exceeds snapshot as_of")
        covered_digests = tuple(
            digest for coverage in self.coverages for digest in coverage.object_sha256s
        )
        object_digests = tuple(sorted(obj.sha256 for obj in self.objects))
        if tuple(sorted(covered_digests)) != object_digests:
            raise ValueError("coverage object digests must match snapshot objects")
        objects_by_digest = {obj.sha256: obj for obj in self.objects}
        for coverage in self.coverages:
            linked = [objects_by_digest[digest] for digest in coverage.object_sha256s]
            if any(obj.dataset != coverage.request.dataset for obj in linked):
                raise ValueError("coverage objects must match requested dataset")
            if any(
                obj.event_time_start < coverage.covered_start
                or obj.event_time_end > coverage.covered_end
                for obj in linked
            ):
                raise ValueError("coverage object exceeds the requested interval")
            if any(
                not set(coverage.fields) <= set(obj.fields)
                or obj.resolved_instrument_set_sha256
                != coverage.resolved_instrument_set_sha256
                or obj.resolved_instrument_count != coverage.resolved_instrument_count
                for obj in linked
            ):
                raise ValueError("coverage objects do not prove fields and universe")
            covered_keys = tuple(
                sorted({key for obj in linked for key in obj.coverage_keys})
            )
            if covered_keys != coverage.request.coverage_keys:
                raise ValueError("coverage objects do not prove every requested session key")
            if sum(obj.row_count for obj in linked) != coverage.row_count:
                raise ValueError("coverage row count must match its snapshot objects")
            linked_known_at = max((obj.known_at_max for obj in linked), default=None)
            if linked_known_at != coverage.known_at_max:
                raise ValueError("coverage known_at must match its snapshot objects")
        expected = self._content_hash(
            self.manifest_version,
            self.request,
            self.availability_policy,
            self.coverages,
            self.objects,
        )
        if self.snapshot_id != expected:
            raise ValueError("snapshot_id does not match canonical manifest content")
        return self


class FactorSpec(BaseModel):
    model_config = ConfigDict(frozen=True)

    factor_id: str
    version: str
    dependencies: tuple[str, ...]
    lookback_sessions: int = Field(ge=0)
    pit_required: bool = True


class FactorContext(BaseModel):
    model_config = ConfigDict(frozen=True, arbitrary_types_allowed=True)

    as_of: datetime
    snapshot: SnapshotManifest
    inputs: pa.Table

    _utc_as_of = field_validator("as_of")(_require_utc)

    @model_validator(mode="after")
    def validate_snapshot_cutoff(self) -> Self:
        if self.snapshot.as_of > self.as_of:
            raise ValueError("snapshot is later than factor cutoff")
        _require_snapshot_provenance(self.inputs, self.snapshot)
        _require_table_cutoff(self.inputs, self.as_of)
        return self


class SignalContext(BaseModel):
    model_config = ConfigDict(
        frozen=True, extra="forbid", strict=True, arbitrary_types_allowed=True
    )

    as_of: datetime
    eligible_execution_time: datetime
    snapshot: SnapshotManifest
    factors: pa.Table

    _utc_times = field_validator("as_of", "eligible_execution_time")(_require_utc)

    @model_validator(mode="after")
    def validate_snapshot_cutoff(self) -> Self:
        if self.snapshot.as_of > self.as_of:
            raise ValueError("snapshot is later than signal cutoff")
        _require_snapshot_provenance(self.factors, self.snapshot)
        _require_table_cutoff(self.factors, self.as_of)
        if self.eligible_execution_time <= self.as_of:
            raise ValueError("eligible execution time must be later than signal cutoff")
        return self


def _snapshot_request_sha256(snapshot: SnapshotManifest) -> str:
    payload = snapshot.request.model_dump(mode="json")
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def _snapshot_objects_sha256(snapshot: SnapshotManifest) -> str:
    return hashlib.sha256(
        json.dumps(
            [obj.sha256 for obj in snapshot.objects], separators=(",", ":")
        ).encode()
    ).hexdigest()


def bind_snapshot_provenance(table: pa.Table, snapshot: SnapshotManifest) -> pa.Table:
    metadata = dict(table.schema.metadata or {})
    metadata[b"trademaster.snapshot_id"] = snapshot.snapshot_id.encode()
    metadata[b"trademaster.snapshot_request_sha256"] = _snapshot_request_sha256(
        snapshot
    ).encode()
    metadata[b"trademaster.snapshot_objects_sha256"] = _snapshot_objects_sha256(
        snapshot
    ).encode()
    return table.replace_schema_metadata(metadata)


def _require_snapshot_provenance(table: pa.Table, snapshot: SnapshotManifest) -> None:
    metadata = table.schema.metadata or {}
    expected = {
        b"trademaster.snapshot_id": snapshot.snapshot_id.encode(),
        b"trademaster.snapshot_request_sha256": _snapshot_request_sha256(snapshot).encode(),
        b"trademaster.snapshot_objects_sha256": _snapshot_objects_sha256(snapshot).encode(),
    }
    if any(metadata.get(key) != value for key, value in expected.items()):
        raise ValueError("table snapshot provenance does not match context snapshot")


def _require_table_cutoff(table: pa.Table, as_of: datetime) -> None:
    if "event_time" not in table.column_names:
        raise ValueError("PIT table must contain event_time")
    field = table.schema.field("event_time")
    if not pa.types.is_timestamp(field.type) or field.type.tz != "UTC":
        raise ValueError("event_time must be a UTC Arrow timestamp")
    if any(
        value is None or value > as_of for value in table["event_time"].to_pylist()
    ):
        raise ValueError("PIT table contains null or future event_time")
    if "known_at" in table.column_names:
        known_at_field = table.schema.field("known_at")
        if (
            not pa.types.is_timestamp(known_at_field.type)
            or known_at_field.type.tz != "UTC"
        ):
            raise ValueError("known_at must be a UTC Arrow timestamp")
        if any(
            value is None or value > as_of
            for value in table["known_at"].to_pylist()
        ):
            raise ValueError("PIT table contains null or future known_at")


class Side(StrEnum):
    BUY = "buy"
    SELL = "sell"


class MarketEventKind(StrEnum):
    SESSION_OPEN = "session_open"
    BAR_CLOSE = "bar_close"
    SETTLEMENT = "settlement"


class OrderIntent(BaseModel):
    model_config = ConfigDict(frozen=True)

    order_id: str
    signal_id: str
    instrument_id: str
    side: Side
    quantity: int = Field(gt=0, lt=10**38)
    signal_time: datetime
    eligible_execution_time: datetime

    _utc_times = field_validator("signal_time", "eligible_execution_time")(_require_utc)

    @model_validator(mode="after")
    def validate_execution_time(self) -> Self:
        if self.eligible_execution_time <= self.signal_time:
            raise ValueError("eligible execution time must be later than signal time")
        return self


class PortfolioPosition(BaseModel):
    model_config = ConfigDict(frozen=True)

    instrument_id: str
    quantity: int = Field(ge=0, lt=10**38)
    sellable_quantity: int = Field(ge=0, lt=10**38)
    market_value: Decimal = Field(max_digits=38, decimal_places=8)

    @model_validator(mode="after")
    def validate_sellable(self) -> Self:
        if self.sellable_quantity > self.quantity:
            raise ValueError("sellable quantity cannot exceed total quantity")
        if self.market_value < 0:
            raise ValueError("position market value cannot be negative")
        return self


class PortfolioView(BaseModel):
    model_config = ConfigDict(frozen=True)

    event_time: datetime
    state_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    cash: Decimal = Field(max_digits=38, decimal_places=8)
    positions: tuple[PortfolioPosition, ...]

    _utc_event_time = field_validator("event_time")(_require_utc)

    @model_validator(mode="after")
    def validate_canonical_portfolio(self) -> Self:
        if self.cash < 0:
            raise ValueError("portfolio cash cannot be negative")
        ids = tuple(position.instrument_id for position in self.positions)
        if ids != tuple(sorted(set(ids))):
            raise ValueError("portfolio positions must be unique and canonically sorted")
        return self


class MarketTableView(BaseModel):
    model_config = ConfigDict(frozen=True, arbitrary_types_allowed=True)

    snapshot: SnapshotManifest
    as_of: datetime
    table: pa.Table

    _utc_as_of = field_validator("as_of")(_require_utc)

    @model_validator(mode="after")
    def validate_frozen_view(self) -> Self:
        if self.snapshot.as_of > self.as_of:
            raise ValueError("market table snapshot is later than its as_of")
        _require_snapshot_provenance(self.table, self.snapshot)
        _require_table_cutoff(self.table, self.as_of)
        return self


class StrategyView(BaseModel):
    model_config = ConfigDict(frozen=True, arbitrary_types_allowed=True)

    event_time: datetime
    event_kind: MarketEventKind
    market: MarketTableView
    history: MarketTableView
    portfolio: PortfolioView

    _utc_event_time = field_validator("event_time")(_require_utc)

    @model_validator(mode="after")
    def validate_event_cutoff(self) -> Self:
        if self.market.as_of != self.event_time:
            raise ValueError("market view must be frozen at event_time")
        if self.history.as_of > self.event_time:
            raise ValueError("history view cannot contain future data")
        if self.market.snapshot.snapshot_id != self.history.snapshot.snapshot_id:
            raise ValueError("market and history must share one snapshot")
        if self.portfolio.event_time != self.event_time:
            raise ValueError("portfolio must be frozen at event_time")
        return self


class BacktestConfig(BaseModel):
    model_config = ConfigDict(frozen=True)

    run_id: str
    strategy_id: str
    snapshot_id: str = Field(pattern=r"^[0-9a-f]{64}$")
    initial_cash: Decimal = Field(gt=0, max_digits=38, decimal_places=8)
    fee_schedule_id: str
    artifact_dir: str


def run_artifact_schema_sha256() -> str:
    return hashlib.sha256(_shared_schema_bytes("run-artifacts-v1.json")).hexdigest()


def run_manifest_schema() -> dict[str, object]:
    value: object = json.loads(_shared_schema_bytes("run-manifest-v1.json"))
    if not isinstance(value, dict):
        raise TypeError("run manifest schema must be an object")
    return value


class RunArtifactObject(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    artifact_name: str
    uri: str = Field(min_length=1)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    row_count: int = Field(ge=0, le=2**64 - 1)

    @field_validator("artifact_name")
    @classmethod
    def validate_artifact_name(cls, value: str) -> str:
        if value not in run_artifact_schemas():
            raise ValueError("unknown run artifact name")
        return value


class RunManifest(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    manifest_version: Literal["run/v1"]
    status: Literal["complete"]
    run_id: str
    created_at: datetime
    snapshot_id: str = Field(pattern=r"^[0-9a-f]{64}$")
    strategy_id: str
    engine_version: str
    config_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    artifact_schema_id: Literal["trademaster.run-artifacts/v1"]
    artifact_schema_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    artifacts: tuple[RunArtifactObject, ...]

    _utc_created_at = field_validator("created_at")(_require_utc)

    @classmethod
    def build(
        cls,
        *,
        run_id: str,
        created_at: datetime,
        snapshot_id: str,
        strategy_id: str,
        engine_version: str,
        config_hash: str,
        artifacts: tuple[RunArtifactObject, ...],
    ) -> Self:
        return cls(
            manifest_version="run/v1",
            status="complete",
            run_id=run_id,
            created_at=created_at,
            snapshot_id=snapshot_id,
            strategy_id=strategy_id,
            engine_version=engine_version,
            config_hash=config_hash,
            artifact_schema_id="trademaster.run-artifacts/v1",
            artifact_schema_sha256=run_artifact_schema_sha256(),
            artifacts=artifacts,
        )

    @model_validator(mode="after")
    def validate_schema_hash(self) -> Self:
        if self.artifact_schema_sha256 != run_artifact_schema_sha256():
            raise ValueError("run manifest artifact schema hash mismatch")
        required = tuple(sorted(run_artifact_schemas()))
        actual = tuple(artifact.artifact_name for artifact in self.artifacts)
        if actual != required:
            raise ValueError("run manifest must bind every artifact exactly once in order")
        if not self.run_id or not self.strategy_id or not self.engine_version:
            raise ValueError("run identity fields must not be empty")
        return self

    def to_wire_json(self) -> str:
        return json.dumps(
            self.model_dump(mode="json"), ensure_ascii=False, separators=(",", ":")
        )

    @classmethod
    def from_wire_json(cls, value: str | bytes) -> Self:
        raw: object = json.loads(value)
        if not isinstance(raw, dict):
            raise TypeError("run manifest wire value must be an object")
        created_at = raw.get("created_at")
        if not isinstance(created_at, str) or re.fullmatch(
            r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(\.[0-9]{1,6})?Z",
            created_at,
        ) is None:
            raise ValueError("created_at must be canonical UTC with microsecond precision")
        artifacts = raw.get("artifacts")
        if not isinstance(artifacts, list) or any(
            not isinstance(item, dict) or type(item.get("row_count")) is not int
            for item in artifacts
        ):
            raise ValueError("artifact row_count must be a JSON integer")
        return cls.model_validate_json(value)


@runtime_checkable
class DataPortal(Protocol):
    def ensure(self, request: DatasetRequest) -> CoverageResult: ...

    def query(self, request: DatasetRequest, *, as_of: datetime) -> pa.Table: ...

    def snapshot(self, request: SnapshotRequest) -> SnapshotManifest: ...

    def load_snapshot(self, snapshot_id: str) -> SnapshotManifest: ...


@runtime_checkable
class Factor(Protocol):
    spec: FactorSpec

    def compute(self, context: FactorContext) -> pa.Table: ...


@runtime_checkable
class SignalGenerator(Protocol):
    def generate(self, context: SignalContext) -> pa.Table: ...


@runtime_checkable
class EventStrategy(Protocol):
    def on_bar(self, view: StrategyView) -> list[OrderIntent]: ...


_TS: pa.DataType = pa.timestamp("us", tz="UTC")
_DECIMAL: pa.DataType = pa.decimal128(38, 8)
_INTEGER: pa.DataType = pa.decimal128(38, 0)
_SCHEMA_METADATA: dict[bytes | str, bytes | str] = {
    b"trademaster.schema_id": b"trademaster.run-artifacts/v1"
}


def _shared_schema_bytes(name: str) -> bytes:
    packaged = importlib.resources.files("trademaster").joinpath("schemas", name)
    if packaged.is_file():
        return packaged.read_bytes()
    source = Path(__file__).resolve().parents[2] / "crates" / "tm-core" / "schemas" / name
    return source.read_bytes()


_ARTIFACT_SCHEMA_BYTES = _shared_schema_bytes("run-artifacts-v1.json")
_ARTIFACT_CONTRACT: dict[str, object] = json.loads(_ARTIFACT_SCHEMA_BYTES)
_ARTIFACT_RECORDS = _ARTIFACT_CONTRACT["records"]
assert isinstance(_ARTIFACT_RECORDS, dict)
_ENUM_REGISTRY = _ARTIFACT_CONTRACT["enums"]
assert isinstance(_ENUM_REGISTRY, dict)
_ARTIFACT_ENUMS: dict[tuple[str, str], frozenset[str]] = {}
for _record_name, _fields in _ARTIFACT_RECORDS.items():
    assert isinstance(_record_name, str) and isinstance(_fields, list)
    for _field in _fields:
        assert isinstance(_field, dict)
        _enum_name = _field.get("enum")
        if _enum_name is not None:
            assert isinstance(_enum_name, str)
            _values = _ENUM_REGISTRY[_enum_name]
            assert isinstance(_values, list) and all(isinstance(value, str) for value in _values)
            _ARTIFACT_ENUMS[(_record_name, str(_field["name"]))] = frozenset(_values)
_RAW_SORT_KEYS = _ARTIFACT_CONTRACT["sort_keys"]
assert isinstance(_RAW_SORT_KEYS, dict)
_ARTIFACT_SORT_KEYS: dict[str, tuple[str, ...]] = {
    str(record_name): tuple(str(key) for key in keys)
    for record_name, keys in _RAW_SORT_KEYS.items()
    if isinstance(keys, list)
}


def _required(name: str, data_type: pa.DataType) -> pa.Field[pa.DataType]:
    return pa.field(name, data_type, nullable=False)


def _schema(fields: list[pa.Field[pa.DataType]]) -> pa.Schema:
    return pa.schema(fields, metadata=_SCHEMA_METADATA)


def _sequence_fields(*names: str) -> list[pa.Field[pa.DataType]]:
    return [_required("run_id", pa.string()), *[_required(name, _INTEGER) for name in names]]


def run_artifact_schemas() -> dict[str, pa.Schema]:
    """Return the language-neutral v1 artifact schemas; all fields are required."""

    return {
        "signals": _schema(
            [
                *_sequence_fields("event_seq", "signal_seq"),
                _required("signal_id", pa.string()),
                _required("strategy_id", pa.string()),
                _required("instrument_id", pa.string()),
                _required("signal_time", _TS),
                _required("eligible_execution_time", _TS),
                _required("intent_type", pa.string()),
                _required("value", _DECIMAL),
                _required("reason", pa.string()),
                _required("snapshot_id", pa.string()),
            ]
        ),
        "orders": _schema(
            [
                *_sequence_fields("event_seq", "order_seq"),
                _required("order_id", pa.string()),
                _required("signal_id", pa.string()),
                _required("instrument_id", pa.string()),
                _required("side", pa.string()),
                _required("requested_quantity", _INTEGER),
                _required("submitted_at", _TS),
                _required("eligible_at", _TS),
            ]
        ),
        "accepted_orders": _schema(
            [
                *_sequence_fields("event_seq", "order_seq"),
                _required("order_id", pa.string()),
                _required("accepted_at", _TS),
            ]
        ),
        "fills": _schema(
            [
                *_sequence_fields("event_seq", "order_seq", "fill_seq"),
                _required("fill_id", pa.string()),
                _required("order_id", pa.string()),
                _required("event_time", _TS),
                _required("quantity", _INTEGER),
                _required("price", _DECIMAL),
                _required("commission", _DECIMAL),
                _required("tax", _DECIMAL),
                _required("transfer_fee", _DECIMAL),
                _required("slippage", _DECIMAL),
            ]
        ),
        "rejections": _schema(
            [
                *_sequence_fields("event_seq", "order_seq"),
                _required("order_id", pa.string()),
                _required("event_time", _TS),
                _required("code", pa.string()),
                _required("message", pa.string()),
            ]
        ),
        "expiries": _schema(
            [
                *_sequence_fields("event_seq", "order_seq"),
                _required("order_id", pa.string()),
                _required("event_time", _TS),
                _required("remaining_quantity", _INTEGER),
                _required("reason", pa.string()),
            ]
        ),
        "ledger_postings": _schema(
            [
                *_sequence_fields("event_seq", "posting_seq"),
                _required("posting_id", pa.string()),
                _required("event_time", _TS),
                _required("account", pa.string()),
                _required("debit_credit", pa.string()),
                _required("unit_type", pa.string()),
                _required("unit_id", pa.string()),
                _required("raw_units", _INTEGER),
                _required("scale", pa.uint8()),
                _required("source_id", pa.string()),
            ]
        ),
        "account_states": _schema(
            [
                *_sequence_fields("event_seq", "transition_seq"),
                _required("transition_id", pa.string()),
                _required("source_id", pa.string()),
                _required("event_time", _TS),
                _required("before_state_hash", pa.string()),
                _required("after_state_hash", pa.string()),
            ]
        ),
        "position_lots": _schema(
            [
                *_sequence_fields("event_seq"),
                _required("lot_id", pa.string()),
                _required("instrument_id", pa.string()),
                _required("quantity", _INTEGER),
                _required("acquired_at", _TS),
                _required("sellable_at", _TS),
                _required("unit_cost", _DECIMAL),
            ]
        ),
        "positions": _schema(
            [
                *_sequence_fields("event_seq"),
                _required("event_time", _TS),
                _required("instrument_id", pa.string()),
                _required("quantity", _INTEGER),
                _required("sellable_quantity", _INTEGER),
                _required("market_value", _DECIMAL),
            ]
        ),
        "nav": _schema(
            [
                *_sequence_fields("event_seq"),
                _required("event_time", _TS),
                _required("cash", _DECIMAL),
                _required("market_value", _DECIMAL),
                _required("net_asset_value", _DECIMAL),
            ]
        ),
    }


def validate_artifact_table(record_name: str, table: pa.Table) -> None:
    """Fail closed when an artifact does not exactly match its frozen v1 schema."""

    schemas = run_artifact_schemas()
    if record_name not in schemas:
        raise ValueError(f"unknown artifact record: {record_name}")
    expected = schemas[record_name]
    if table.schema != expected:
        raise ValueError(f"artifact schema mismatch for {record_name}")
    invalid = [field.name for field, column in zip(expected, table.columns) if column.null_count]
    if invalid:
        raise ValueError(f"non-nullable artifact fields contain nulls: {', '.join(invalid)}")
    for field_name in (name for name in expected.names if name.endswith("_seq")):
        if any(value is None or value < 0 for value in table[field_name].to_pylist()):
            raise ValueError(f"artifact sequence must be non-negative: {field_name}")
    sort_keys = _ARTIFACT_SORT_KEYS[record_name]
    keys = [tuple(row[name] for name in sort_keys) for row in table.select(sort_keys).to_pylist()]
    if keys != sorted(keys) or len(keys) != len(set(keys)):
        raise ValueError(f"artifact rows must have unique canonical ordering by {sort_keys}")
    for (artifact, field_name), allowed in _ARTIFACT_ENUMS.items():
        if artifact != record_name:
            continue
        values = set(table[field_name].to_pylist())
        if not values <= allowed:
            raise ValueError(f"artifact enum violation for {record_name}.{field_name}")
    raw_constraints = _ARTIFACT_CONTRACT["constraints"]
    assert isinstance(raw_constraints, dict)
    constraints = raw_constraints.get(record_name, {})
    assert isinstance(constraints, dict)
    for field_name in constraints.get("positive", []):
        if any(value is None or value <= 0 for value in table[str(field_name)].to_pylist()):
            raise ValueError(f"artifact field must be positive: {field_name}")
    for field_name in constraints.get("non_negative", []):
        if any(value is None or value < 0 for value in table[str(field_name)].to_pylist()):
            raise ValueError(f"artifact field must be non-negative: {field_name}")
    fixed = constraints.get("fixed", {})
    assert isinstance(fixed, dict)
    for field_name, expected_value in fixed.items():
        if any(value != expected_value for value in table[str(field_name)].to_pylist()):
            raise ValueError(f"artifact fixed value violation: {field_name}")
    for field_name in constraints.get("sha256", []):
        if any(
            not isinstance(value, str)
            or len(value) != 64
            or any(char not in "0123456789abcdef" for char in value)
            for value in table[str(field_name)].to_pylist()
        ):
            raise ValueError(f"artifact sha256 violation: {field_name}")
    for field_name in constraints.get("unique", []):
        field_values: list[object] = table[str(field_name)].to_pylist()
        if len(field_values) != len(set(field_values)):
            raise ValueError(f"artifact field must be unique for the completed run: {field_name}")
    for earlier, later in constraints.get("time_order", []):
        if any(
            left is None or right is None or left > right
            for left, right in zip(
                table[str(earlier)].to_pylist(), table[str(later)].to_pylist()
            )
        ):
            raise ValueError(f"artifact time order violation: {earlier}, {later}")
    for earlier, later in constraints.get("strict_time_order", []):
        if any(
            left is None or right is None or left >= right
            for left, right in zip(
                table[str(earlier)].to_pylist(), table[str(later)].to_pylist()
            )
        ):
            raise ValueError(f"artifact strict time order violation: {earlier}, {later}")
    if record_name == "ledger_postings":
        balances: dict[tuple[object, ...], list[Decimal]] = {}
        balance_by = constraints["balance_by"]
        assert isinstance(balance_by, list)
        for row in table.to_pylist():
            key = tuple(row[str(field_name)] for field_name in balance_by)
            totals = balances.setdefault(key, [Decimal(0), Decimal(0)])
            index = 0 if row["debit_credit"] == "debit" else 1
            totals[index] += row["raw_units"]
        if any(debit != credit for debit, credit in balances.values()):
            raise ValueError("ledger posting group must balance debits and credits")
    if record_name == "nav" and any(
        row["cash"] + row["market_value"] != row["net_asset_value"]
        for row in table.to_pylist()
    ):
        raise ValueError("NAV must equal cash plus market value")
    if record_name == "positions" and any(
        row["sellable_quantity"] > row["quantity"] for row in table.to_pylist()
    ):
        raise ValueError("sellable quantity cannot exceed position quantity")


def validate_run_artifacts(artifacts: Mapping[str, pa.Table]) -> None:
    """Validate a completed run as one cross-table lifecycle, not isolated batches."""

    required = set(run_artifact_schemas())
    if set(artifacts) != required:
        raise ValueError("completed run must contain exactly all eleven artifact tables")
    for name in sorted(required):
        validate_artifact_table(name, artifacts[name])

    run_ids = {
        run_id
        for table in artifacts.values()
        for run_id in table["run_id"].to_pylist()
    }
    if len(run_ids) > 1:
        raise ValueError("all artifact rows must belong to one run_id")

    signals = {row["signal_id"]: row for row in artifacts["signals"].to_pylist()}
    orders = {row["order_id"]: row for row in artifacts["orders"].to_pylist()}
    accepted = {
        row["order_id"]: row for row in artifacts["accepted_orders"].to_pylist()
    }
    rejected = {row["order_id"]: row for row in artifacts["rejections"].to_pylist()}
    expiries = {row["order_id"]: row for row in artifacts["expiries"].to_pylist()}

    if any(order["signal_id"] not in signals for order in orders.values()):
        raise ValueError("every order must reference a signal in the same run")
    if any(
        order["instrument_id"] != signals[order["signal_id"]]["instrument_id"]
        or order["eligible_at"]
        != signals[order["signal_id"]]["eligible_execution_time"]
        or order["submitted_at"] < signals[order["signal_id"]]["eligible_execution_time"]
        for order in orders.values()
    ):
        raise ValueError("order must preserve its signal instrument and eligibility")
    if set(accepted) & set(rejected) or set(accepted) | set(rejected) != set(orders):
        raise ValueError("each order must be accepted or rejected exactly once")
    if any(
        row["accepted_at"] < orders[order_id]["submitted_at"]
        or row["event_seq"] != orders[order_id]["event_seq"]
        or row["order_seq"] != orders[order_id]["order_seq"]
        for order_id, row in accepted.items()
    ):
        raise ValueError("accepted order chronology violation")
    if any(
        row["event_time"] < orders[order_id]["submitted_at"]
        or row["event_seq"] != orders[order_id]["event_seq"]
        or row["order_seq"] != orders[order_id]["order_seq"]
        for order_id, row in rejected.items()
    ):
        raise ValueError("rejection chronology violation")

    fills_by_order: dict[str, list[dict[str, object]]] = {}
    for fill in artifacts["fills"].to_pylist():
        order_id = str(fill["order_id"])
        if order_id not in accepted:
            raise ValueError("fill must reference an accepted order")
        if fill["event_time"] < accepted[order_id]["accepted_at"]:
            raise ValueError("fill chronology violation")
        if (
            fill["event_seq"] != orders[order_id]["event_seq"]
            or fill["order_seq"] != orders[order_id]["order_seq"]
        ):
            raise ValueError("fill sequence must match its order")
        fills_by_order.setdefault(order_id, []).append(fill)
    if any(order_id not in accepted for order_id in expiries):
        raise ValueError("expiry must reference an accepted order")
    if any(
        row["event_seq"] != orders[order_id]["event_seq"]
        or row["order_seq"] != orders[order_id]["order_seq"]
        for order_id, row in expiries.items()
    ):
        raise ValueError("expiry sequence must match its order")

    for order_id, admission in accepted.items():
        requested = cast(Decimal, orders[order_id]["requested_quantity"])
        fills = fills_by_order.get(order_id, [])
        if any(
            cast(datetime, later["event_time"])
            < cast(datetime, earlier["event_time"])
            for earlier, later in pairwise(fills)
        ):
            raise ValueError("fill event_time must be monotonic by fill_seq")
        filled = sum(
            (cast(Decimal, fill["quantity"]) for fill in fills), start=Decimal(0)
        )
        expiry = expiries.get(order_id)
        if filled == requested:
            if expiry is not None:
                raise ValueError("fully filled order cannot also expire")
        elif filled < requested:
            if expiry is None or expiry["remaining_quantity"] != requested - filled:
                raise ValueError("expiry must conserve the exact unfilled quantity")
            if expiry["event_time"] < admission["accepted_at"] or (
                fills
                and cast(datetime, expiry["event_time"])
                < max(cast(datetime, fill["event_time"]) for fill in fills)
            ):
                raise ValueError("expiry chronology violation")
        else:
            raise ValueError("fills cannot exceed requested quantity")

    fill_rows = {row["fill_id"]: row for row in artifacts["fills"].to_pylist()}
    postings_by_source: dict[object, list[dict[str, object]]] = {}
    for posting in artifacts["ledger_postings"].to_pylist():
        source = posting["source_id"]
        if source not in fill_rows:
            raise ValueError("ledger source must reference a fill")
        if (
            posting["event_seq"] != fill_rows[source]["event_seq"]
            or posting["event_time"] != fill_rows[source]["event_time"]
        ):
            raise ValueError("ledger posting time and sequence must match its fill")
        postings_by_source.setdefault(source, []).append(posting)
    if any(len(postings_by_source.get(fill_id, [])) < 2 for fill_id in fill_rows):
        raise ValueError("every fill must produce a balanced ledger posting group")
    nav_event_seqs = set(artifacts["nav"]["event_seq"].to_pylist())
    if any(fill["event_seq"] not in nav_event_seqs for fill in fill_rows.values()):
        raise ValueError("every fill event must produce an account NAV snapshot")
    transitions = artifacts["account_states"].to_pylist()
    transitions_by_source: dict[object, list[dict[str, object]]] = {}
    for transition in transitions:
        source = transition["source_id"]
        if source not in accepted and not str(source).startswith("settlement:"):
            raise ValueError("account transition source must be an order or settlement")
        transitions_by_source.setdefault(source, []).append(transition)
    for order_id, fills in fills_by_order.items():
        candidates = transitions_by_source.get(order_id, [])
        if len(candidates) != 1:
            raise ValueError("every executed order must have one account transition")
        transition = candidates[0]
        if (
            transition["event_seq"] != orders[order_id]["event_seq"]
            or transition["event_time"]
            != max(cast(datetime, fill["event_time"]) for fill in fills)
            or transition["before_state_hash"] == transition["after_state_hash"]
        ):
            raise ValueError("account transition does not match its executed order")
    if any(
        previous["after_state_hash"] != current["before_state_hash"]
        or current["event_time"] < previous["event_time"]
        for previous, current in pairwise(transitions)
    ):
        raise ValueError("account state hash chain is broken")
    event_times = sorted(
        {
            (fill["event_seq"], cast(datetime, fill["event_time"]))
            for fill in fill_rows.values()
        }
        | {
            (transition["event_seq"], cast(datetime, transition["event_time"]))
            for transition in transitions
        }
    )
    if any(current[1] < previous[1] for previous, current in pairwise(event_times)):
        raise ValueError("event time must be monotonic by event_seq")

    lots_by_event: dict[tuple[object, object, object], list[dict[str, object]]] = {}
    for lot in artifacts["position_lots"].to_pylist():
        key = (lot["run_id"], lot["event_seq"], lot["instrument_id"])
        lots_by_event.setdefault(key, []).append(lot)
    for position in artifacts["positions"].to_pylist():
        key = (position["run_id"], position["event_seq"], position["instrument_id"])
        lots = lots_by_event.pop(key, [])
        quantity = sum(
            (cast(Decimal, lot["quantity"]) for lot in lots), start=Decimal(0)
        )
        sellable = sum(
            (
                cast(Decimal, lot["quantity"])
                for lot in lots
                if lot["acquired_at"] <= position["event_time"]
                and lot["sellable_at"] <= position["event_time"]
            ),
            start=Decimal(0),
        )
        if any(lot["acquired_at"] > position["event_time"] for lot in lots):
            raise ValueError("position snapshot contains a future lot")
        if quantity != position["quantity"] or sellable != position["sellable_quantity"]:
            raise ValueError("position quantities must equal its event lots")
    if lots_by_event:
        raise ValueError("every position lot snapshot must have a position row")
    nav_rows = {row["event_seq"]: row for row in artifacts["nav"].to_pylist()}
    positions_by_event: dict[object, list[dict[str, object]]] = {}
    lots_for_hash: dict[object, list[dict[str, object]]] = {}
    for row in artifacts["positions"].to_pylist():
        positions_by_event.setdefault(row["event_seq"], []).append(row)
    for row in artifacts["position_lots"].to_pylist():
        lots_for_hash.setdefault(row["event_seq"], []).append(row)
    transitions_by_event: dict[object, list[dict[str, object]]] = {}
    for transition in transitions:
        transitions_by_event.setdefault(transition["event_seq"], []).append(transition)
    for event_seq, event_transitions in transitions_by_event.items():
        nav = nav_rows.get(event_seq)
        if nav is None:
            raise ValueError("account transition requires a NAV snapshot")
        if nav["cash"] + nav["market_value"] != nav["net_asset_value"]:
            raise ValueError("NAV must equal cash plus market value")
        positions = positions_by_event.get(event_seq, [])
        lots = lots_for_hash.get(event_seq, [])
        if sum(
            (cast(Decimal, row["market_value"]) for row in positions),
            start=Decimal(0),
        ) != nav["market_value"]:
            raise ValueError("position market values must equal NAV market value")
        if any(
            row["event_time"] != nav["event_time"] for row in positions
        ) or any(
            row["acquired_at"] > nav["event_time"] for row in lots
        ):
            raise ValueError("account artifacts must be valid at the NAV event time")
        actual_hash = account_state_hash_from_artifacts(
            nav,
            positions,
            lots,
        )
        if event_transitions[-1]["after_state_hash"] != actual_hash:
            raise ValueError("account transition hash does not match NAV/positions/lots")


def verify_run_manifest(manifest: RunManifest, base_dir: Path) -> dict[str, pa.Table]:
    """Read, hash, count, and validate the exact Parquet objects bound by a manifest."""

    root = base_dir.resolve()
    tables: dict[str, pa.Table] = {}
    for artifact in manifest.artifacts:
        relative = Path(artifact.uri)
        if relative.is_absolute():
            raise ValueError("artifact URI must be relative to the manifest directory")
        path = (root / relative).resolve()
        try:
            path.relative_to(root)
        except ValueError as error:
            raise ValueError("artifact URI escapes the manifest directory") from error
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        if digest != artifact.sha256:
            raise ValueError(f"artifact content hash mismatch: {artifact.artifact_name}")
        table = pq.read_table(path)
        if table.num_rows != artifact.row_count:
            raise ValueError(f"artifact row count mismatch: {artifact.artifact_name}")
        if any(run_id != manifest.run_id for run_id in table["run_id"].to_pylist()):
            raise ValueError(f"artifact run_id mismatch: {artifact.artifact_name}")
        tables[artifact.artifact_name] = table
    validate_run_artifacts(tables)
    if any(
        row["strategy_id"] != manifest.strategy_id
        or row["snapshot_id"] != manifest.snapshot_id
        for row in tables["signals"].to_pylist()
    ):
        raise ValueError("signal strategy/snapshot identity does not match run manifest")
    return tables


def account_state_hash_from_artifacts(
    nav: dict[str, object],
    positions: list[dict[str, object]],
    lots: list[dict[str, object]],
) -> str:
    event_time = cast(datetime, nav["event_time"])
    event_us = _datetime_micros(event_time)
    position_payload = [
        [
            row["instrument_id"],
            int(cast(Decimal, row["quantity"])),
            int(cast(Decimal, row["sellable_quantity"])),
            int(cast(Decimal, row["market_value"]) * 100_000_000),
        ]
        for row in positions
    ]
    lot_payload = [
        [
            row["lot_id"],
            row["instrument_id"],
            int(cast(Decimal, row["quantity"])),
            _datetime_micros(cast(datetime, row["acquired_at"])),
            _datetime_micros(cast(datetime, row["sellable_at"])),
            int(cast(Decimal, row["unit_cost"]) * 100_000_000),
        ]
        for row in lots
    ]
    payload = [
        "account-state/v1",
        event_us,
        int(cast(Decimal, nav["cash"]) * 100_000_000),
        int(cast(Decimal, nav["market_value"]) * 100_000_000),
        int(cast(Decimal, nav["net_asset_value"]) * 100_000_000),
        position_payload,
        lot_payload,
    ]
    return hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode()
    ).hexdigest()


def _datetime_micros(value: datetime) -> int:
    epoch = datetime(1970, 1, 1, tzinfo=value.tzinfo)
    delta = value - epoch
    return (delta.days * 86_400 + delta.seconds) * 1_000_000 + delta.microseconds
