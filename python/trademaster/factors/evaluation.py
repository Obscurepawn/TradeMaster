"""Point-in-time-safe factor quality evaluation against explicit forward returns."""

from __future__ import annotations

import hashlib
import json
import math
import statistics
import tempfile
from bisect import bisect_right
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path, PurePosixPath
from typing import Any, Literal, cast

import pyarrow as pa
import pyarrow.parquet as pq
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from trademaster.contracts import FactorContext, SnapshotManifest, _require_utc

from . import factor_output_schema
from .storage import FactorArtifact, FactorCatalog, FactorIntegrityError


def forward_return_schema() -> pa.Schema:
    fields: list[Any] = [
        pa.field("instrument_id", pa.string(), nullable=False),
        pa.field("event_time", pa.timestamp("us", tz="UTC"), nullable=False),
        pa.field("entry_time", pa.timestamp("us", tz="UTC"), nullable=False),
        pa.field("exit_time", pa.timestamp("us", tz="UTC"), nullable=False),
        pa.field("horizon_sessions", pa.int32(), nullable=False),
        pa.field("return_alignment", pa.string(), nullable=False),
        pa.field("label_snapshot_id", pa.string(), nullable=False),
        pa.field("forward_return", pa.float64(), nullable=False),
        pa.field("is_valid", pa.bool_(), nullable=False),
    ]
    return pa.schema(fields)


def factor_ic_schema() -> pa.Schema:
    fields: list[Any] = [
        pa.field("event_time", pa.timestamp("us", tz="UTC"), nullable=False),
        pa.field("horizon_sessions", pa.int32(), nullable=False),
        pa.field("pearson_ic", pa.float64(), nullable=True),
        pa.field("rank_ic", pa.float64(), nullable=True),
        pa.field("observation_count", pa.int32(), nullable=False),
    ]
    return pa.schema(fields)


def factor_quantile_schema() -> pa.Schema:
    fields: list[Any] = [
        pa.field("event_time", pa.timestamp("us", tz="UTC"), nullable=False),
        pa.field("horizon_sessions", pa.int32(), nullable=False),
        pa.field("quantile", pa.int16(), nullable=False),
        pa.field("mean_forward_return", pa.float64(), nullable=False),
        pa.field("observation_count", pa.int32(), nullable=False),
        pa.field("turnover", pa.float64(), nullable=False),
    ]
    return pa.schema(fields)


class FactorEvaluationConfig(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    horizons: tuple[int, ...]
    quantiles: int = Field(ge=2, le=20)
    minimum_observations: int = Field(ge=2)
    return_alignment: Literal[
        "next_eligible_open",
        "next_eligible_close",
    ]

    @model_validator(mode="after")
    def validate_config(self) -> FactorEvaluationConfig:
        if (
            not self.horizons
            or self.horizons != tuple(sorted(set(self.horizons)))
            or any(value < 1 for value in self.horizons)
        ):
            raise ValueError("evaluation horizons must be positive, unique and sorted")
        return self


class EvaluationLabelContext(BaseModel):
    model_config = ConfigDict(
        frozen=True,
        extra="forbid",
        strict=True,
        arbitrary_types_allowed=True,
    )

    label_snapshot: SnapshotManifest
    eligible_entry_times: tuple[datetime, ...]

    @model_validator(mode="after")
    def validate_context(self) -> EvaluationLabelContext:
        for value in self.eligible_entry_times:
            _require_utc(value)
        if not self.eligible_entry_times or self.eligible_entry_times != tuple(
            sorted(set(self.eligible_entry_times))
        ):
            raise ValueError("eligible label entry times must be nonempty and ordered")
        return self

    @property
    def eligible_entry_times_sha256(self) -> str:
        return hashlib.sha256(
            _canonical_json([value.isoformat() for value in self.eligible_entry_times])
        ).hexdigest()


class HorizonEvaluation(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    horizon_sessions: int = Field(gt=0)
    event_count: int = Field(ge=0)
    mean_ic: float | None
    ic_standard_deviation: float | None
    icir: float | None
    mean_rank_ic: float | None
    rank_ic_standard_deviation: float | None
    rank_icir: float | None
    positive_ic_ratio: float | None
    mean_long_short_spread: float | None
    mean_top_quantile_turnover: float | None


class FactorEvaluationSummary(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    schema_id: Literal["trademaster.factor-evaluation-summary/v1"]
    evaluation_id: str = Field(pattern=r"^[0-9a-f]{64}$")
    factor_id: str = Field(min_length=1)
    factor_version: str = Field(min_length=1)
    factor_definition_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    factor_materialization_id: str = Field(pattern=r"^[0-9a-f]{64}$")
    forward_returns_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    label_snapshot_id: str = Field(pattern=r"^[0-9a-f]{64}$")
    eligible_entry_times_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    coverage: float = Field(ge=0, le=1)
    valid_observation_count: int = Field(ge=0)
    total_observation_count: int = Field(ge=0)
    config: FactorEvaluationConfig
    horizons: tuple[HorizonEvaluation, ...]


@dataclass(frozen=True, slots=True)
class FactorEvaluationResult:
    summary: FactorEvaluationSummary
    forward_returns: pa.Table
    ic_table: pa.Table
    quantile_table: pa.Table


class EvaluationObject(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    name: Literal["forward_returns", "ic", "quantiles", "summary"]
    uri: str = Field(min_length=1)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    row_count: int | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def validate_uri(self) -> EvaluationObject:
        uri = PurePosixPath(self.uri)
        if uri.is_absolute() or ".." in uri.parts or not uri.parts:
            raise ValueError("evaluation object URI must be contained")
        return self


class FactorEvaluationManifest(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    schema_id: Literal["trademaster.factor-evaluation/v1"]
    evaluation_id: str = Field(pattern=r"^[0-9a-f]{64}$")
    factor_materialization_id: str = Field(pattern=r"^[0-9a-f]{64}$")
    factor_definition_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    forward_returns_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    label_snapshot_id: str = Field(pattern=r"^[0-9a-f]{64}$")
    eligible_entry_times_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    config_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    objects: tuple[EvaluationObject, ...]
    created_at: datetime

    _utc_created_at = field_validator("created_at")(_require_utc)

    @model_validator(mode="after")
    def validate_manifest(self) -> FactorEvaluationManifest:
        names = tuple(item.name for item in self.objects)
        if names != tuple(sorted(set(names))) or set(names) != {
            "forward_returns",
            "ic",
            "quantiles",
            "summary",
        }:
            raise ValueError("evaluation objects must be complete, unique and sorted")
        return self


def _canonical_json(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode()


def _table_sha256(table: pa.Table) -> str:
    sink = pa.BufferOutputStream()
    with pa.ipc.new_stream(sink, table.schema) as writer:
        writer.write_table(table.combine_chunks())
    return hashlib.sha256(sink.getvalue().to_pybytes()).hexdigest()


def _pearson(left: list[float], right: list[float]) -> float | None:
    if len(left) != len(right) or len(left) < 2:
        return None
    left_mean = statistics.fmean(left)
    right_mean = statistics.fmean(right)
    numerator = sum(
        (left_value - left_mean) * (right_value - right_mean)
        for left_value, right_value in zip(left, right, strict=True)
    )
    left_scale = math.sqrt(sum((value - left_mean) ** 2 for value in left))
    right_scale = math.sqrt(sum((value - right_mean) ** 2 for value in right))
    if left_scale == 0 or right_scale == 0:
        return None
    return numerator / (left_scale * right_scale)


def _ranks(values: list[float]) -> list[float]:
    ordered = sorted(enumerate(values), key=lambda item: (item[1], item[0]))
    result = [0.0] * len(values)
    start = 0
    while start < len(ordered):
        end = start + 1
        while end < len(ordered) and ordered[end][1] == ordered[start][1]:
            end += 1
        average_rank = (start + 1 + end) / 2
        for index in range(start, end):
            result[ordered[index][0]] = average_rank
        start = end
    return result


def _mean_stdev_ratio(values: list[float]) -> tuple[float | None, float | None, float | None]:
    if not values:
        return (None, None, None)
    mean = statistics.fmean(values)
    deviation = statistics.stdev(values) if len(values) > 1 else None
    ratio = mean / deviation if deviation is not None and deviation > 0 else None
    return (mean, deviation, ratio)


class FactorEvaluator:
    """Compute transparent cross-sectional factor diagnostics by event and horizon."""

    def evaluate(
        self,
        artifact: FactorArtifact,
        forward_returns: pa.Table,
        *,
        config: FactorEvaluationConfig,
        label_context: EvaluationLabelContext,
    ) -> FactorEvaluationResult:
        if artifact.table.schema.remove_metadata() != factor_output_schema():
            raise ValueError("factor evaluation input schema drift")
        if forward_returns.schema.remove_metadata() != forward_return_schema():
            raise ValueError("forward return schema drift")
        FactorContext(
            as_of=label_context.label_snapshot.as_of,
            snapshot=label_context.label_snapshot,
            inputs=forward_returns,
        )
        factor_rows = artifact.table.to_pylist()
        factor_values: dict[tuple[datetime, str], tuple[float, bool]] = {}
        for row in factor_rows:
            factor_key = (
                cast(datetime, row["event_time"]),
                str(row["instrument_id"]),
            )
            if factor_key in factor_values:
                raise ValueError("factor evaluation input contains duplicate observations")
            factor_values[factor_key] = (
                float(row["value"]),
                bool(row["is_valid"]),
            )
        return_values: dict[tuple[datetime, str, int], tuple[float, bool]] = {}
        for row in forward_returns.to_pylist():
            event_time = cast(datetime, row["event_time"])
            entry_time = cast(datetime, row["entry_time"])
            exit_time = cast(datetime, row["exit_time"])
            insertion = bisect_right(label_context.eligible_entry_times, event_time)
            if (
                insertion >= len(label_context.eligible_entry_times)
                or entry_time != label_context.eligible_entry_times[insertion]
                or exit_time <= entry_time
                or exit_time > label_context.label_snapshot.as_of
                or row["return_alignment"] != config.return_alignment
                or row["label_snapshot_id"] != label_context.label_snapshot.snapshot_id
            ):
                raise ValueError("forward return timing, alignment, or label snapshot is invalid")
            return_key = (
                event_time,
                str(row["instrument_id"]),
                int(row["horizon_sessions"]),
            )
            if return_key in return_values:
                raise ValueError("forward returns contain duplicate observations")
            value = float(row["forward_return"])
            valid = bool(row["is_valid"])
            if valid and not math.isfinite(value):
                raise ValueError("valid forward return must be finite")
            return_values[return_key] = (value, valid)

        ic_rows: list[dict[str, object]] = []
        quantile_rows: list[dict[str, object]] = []
        horizon_summaries: list[HorizonEvaluation] = []
        factor_event_times = sorted({key[0] for key in factor_values})
        for horizon in config.horizons:
            ic_values: list[float] = []
            rank_ic_values: list[float] = []
            spreads: list[float] = []
            previous_members: dict[int, set[str]] = {}
            top_turnovers: list[float] = []
            evaluated_events = 0
            for event_time in factor_event_times:
                joined = [
                    (instrument, factor_value, return_values[(event_time, instrument, horizon)][0])
                    for (candidate_time, instrument), (
                        factor_value,
                        factor_valid,
                    ) in factor_values.items()
                    if candidate_time == event_time
                    and factor_valid
                    and math.isfinite(factor_value)
                    and (event_time, instrument, horizon) in return_values
                    and return_values[(event_time, instrument, horizon)][1]
                ]
                if len(joined) < config.minimum_observations:
                    continue
                evaluated_events += 1
                joined.sort(key=lambda item: item[0])
                factor_sample = [item[1] for item in joined]
                return_sample = [item[2] for item in joined]
                pearson = _pearson(factor_sample, return_sample)
                rank_ic = _pearson(_ranks(factor_sample), _ranks(return_sample))
                if pearson is not None:
                    ic_values.append(pearson)
                if rank_ic is not None:
                    rank_ic_values.append(rank_ic)
                ic_rows.append(
                    {
                        "event_time": event_time,
                        "horizon_sessions": horizon,
                        "pearson_ic": pearson,
                        "rank_ic": rank_ic,
                        "observation_count": len(joined),
                    }
                )

                ranked = sorted(joined, key=lambda item: (item[1], item[0]))
                by_quantile: dict[int, list[tuple[str, float]]] = {
                    value: [] for value in range(1, config.quantiles + 1)
                }
                for index, (instrument, _, forward_return) in enumerate(ranked):
                    quantile = min(
                        config.quantiles,
                        index * config.quantiles // len(ranked) + 1,
                    )
                    by_quantile[quantile].append((instrument, forward_return))
                means: dict[int, float] = {}
                for quantile in range(1, config.quantiles + 1):
                    members = {item[0] for item in by_quantile[quantile]}
                    if not members:
                        continue
                    mean_return = statistics.fmean(item[1] for item in by_quantile[quantile])
                    means[quantile] = mean_return
                    previous = previous_members.get(quantile)
                    turnover = (
                        0.0
                        if previous is None
                        else 1.0 - len(previous & members) / max(len(previous), len(members))
                    )
                    previous_members[quantile] = members
                    if quantile == config.quantiles and previous is not None:
                        top_turnovers.append(turnover)
                    quantile_rows.append(
                        {
                            "event_time": event_time,
                            "horizon_sessions": horizon,
                            "quantile": quantile,
                            "mean_forward_return": mean_return,
                            "observation_count": len(members),
                            "turnover": turnover,
                        }
                    )
                if 1 in means and config.quantiles in means:
                    spreads.append(means[config.quantiles] - means[1])

            mean_ic, ic_stdev, icir = _mean_stdev_ratio(ic_values)
            mean_rank_ic, rank_stdev, rank_icir = _mean_stdev_ratio(rank_ic_values)
            horizon_summaries.append(
                HorizonEvaluation(
                    horizon_sessions=horizon,
                    event_count=evaluated_events,
                    mean_ic=mean_ic,
                    ic_standard_deviation=ic_stdev,
                    icir=icir,
                    mean_rank_ic=mean_rank_ic,
                    rank_ic_standard_deviation=rank_stdev,
                    rank_icir=rank_icir,
                    positive_ic_ratio=(
                        sum(value > 0 for value in ic_values) / len(ic_values)
                        if ic_values
                        else None
                    ),
                    mean_long_short_spread=(statistics.fmean(spreads) if spreads else None),
                    mean_top_quantile_turnover=(
                        statistics.fmean(top_turnovers) if top_turnovers else None
                    ),
                )
            )

        forward_returns_sha256 = _table_sha256(forward_returns)
        evaluation_identity = {
            "factor_materialization_id": artifact.manifest.materialization_id,
            "forward_returns_sha256": forward_returns_sha256,
            "config": config.model_dump(mode="json"),
            "label_snapshot_id": label_context.label_snapshot.snapshot_id,
            "eligible_entry_times_sha256": (label_context.eligible_entry_times_sha256),
        }
        valid_count = sum(valid and math.isfinite(value) for value, valid in factor_values.values())
        summary = FactorEvaluationSummary(
            schema_id="trademaster.factor-evaluation-summary/v1",
            evaluation_id=hashlib.sha256(_canonical_json(evaluation_identity)).hexdigest(),
            factor_id=artifact.manifest.factor_id,
            factor_version=artifact.manifest.factor_version,
            factor_definition_sha256=artifact.manifest.definition_sha256,
            factor_materialization_id=artifact.manifest.materialization_id,
            forward_returns_sha256=forward_returns_sha256,
            label_snapshot_id=label_context.label_snapshot.snapshot_id,
            eligible_entry_times_sha256=(label_context.eligible_entry_times_sha256),
            coverage=valid_count / len(factor_values) if factor_values else 0.0,
            valid_observation_count=valid_count,
            total_observation_count=len(factor_values),
            config=config,
            horizons=tuple(horizon_summaries),
        )
        return FactorEvaluationResult(
            summary=summary,
            forward_returns=forward_returns,
            ic_table=pa.Table.from_pylist(ic_rows, schema=factor_ic_schema()),
            quantile_table=pa.Table.from_pylist(quantile_rows, schema=factor_quantile_schema()),
        )


class FactorEvaluationStore:
    """Persist evaluation evidence and index it in the managed factor catalog."""

    def __init__(
        self,
        *,
        root: Path,
        catalog: FactorCatalog,
        clock: Callable[[], datetime],
    ) -> None:
        self.root = root.resolve()
        self.catalog = catalog
        self.clock = clock
        self.evaluations = self.root / "evaluations"
        self.temporary = self.root / ".tmp"
        self.evaluations.mkdir(parents=True, exist_ok=True)
        self.temporary.mkdir(parents=True, exist_ok=True)

    def _relative(self, path: Path) -> str:
        resolved = path.resolve()
        try:
            return resolved.relative_to(self.root).as_posix()
        except ValueError as error:
            raise FactorIntegrityError("factor evaluation path escapes root") from error

    @staticmethod
    def _file_sha256(path: Path) -> str:
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()

    def _write_bytes(self, path: Path, payload: bytes) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists():
            if path.read_bytes() != payload:
                raise FactorIntegrityError("factor evaluation object conflicts")
            return
        with tempfile.NamedTemporaryFile(
            dir=self.temporary,
            prefix="evaluation-",
            suffix=".tmp",
            delete=False,
        ) as handle:
            temporary = Path(handle.name)
            handle.write(payload)
        try:
            temporary.replace(path)
        finally:
            if temporary.exists():
                temporary.unlink()

    def _write_parquet(self, path: Path, table: pa.Table) -> str:
        with tempfile.NamedTemporaryFile(
            dir=self.temporary,
            prefix="evaluation-",
            suffix=".parquet.tmp",
            delete=False,
        ) as handle:
            temporary = Path(handle.name)
        try:
            pq.write_table(table, temporary)
            payload = temporary.read_bytes()
            self._write_bytes(path, payload)
            return hashlib.sha256(payload).hexdigest()
        finally:
            if temporary.exists():
                temporary.unlink()

    def persist(self, result: FactorEvaluationResult) -> FactorEvaluationManifest:
        existing = self.catalog.evaluation_by_id(result.summary.evaluation_id)
        if existing is not None:
            return self._load(
                result.summary.evaluation_id,
                manifest_uri=existing[0],
                manifest_sha256=existing[1],
            )
        directory = self.evaluations / result.summary.evaluation_id
        directory.mkdir(parents=True, exist_ok=True)
        tables: dict[str, pa.Table] = {
            "forward_returns": result.forward_returns,
            "ic": result.ic_table,
            "quantiles": result.quantile_table,
        }
        objects: list[EvaluationObject] = []
        evaluation_names: tuple[Literal["forward_returns", "ic", "quantiles"], ...] = (
            "forward_returns",
            "ic",
            "quantiles",
        )
        for name in evaluation_names:
            table = tables[name]
            path = directory / f"{name}.parquet"
            objects.append(
                EvaluationObject(
                    name=name,
                    uri=self._relative(path),
                    sha256=self._write_parquet(path, table),
                    row_count=table.num_rows,
                )
            )
        summary_path = directory / "summary.json"
        summary_payload = _canonical_json(result.summary.model_dump(mode="json"))
        self._write_bytes(summary_path, summary_payload)
        objects.append(
            EvaluationObject(
                name="summary",
                uri=self._relative(summary_path),
                sha256=hashlib.sha256(summary_payload).hexdigest(),
                row_count=None,
            )
        )
        manifest = FactorEvaluationManifest(
            schema_id="trademaster.factor-evaluation/v1",
            evaluation_id=result.summary.evaluation_id,
            factor_materialization_id=result.summary.factor_materialization_id,
            factor_definition_sha256=result.summary.factor_definition_sha256,
            forward_returns_sha256=result.summary.forward_returns_sha256,
            label_snapshot_id=result.summary.label_snapshot_id,
            eligible_entry_times_sha256=(result.summary.eligible_entry_times_sha256),
            config_sha256=hashlib.sha256(
                _canonical_json(result.summary.config.model_dump(mode="json"))
            ).hexdigest(),
            objects=tuple(sorted(objects, key=lambda item: item.name)),
            created_at=self.clock(),
        )
        manifest_path = directory / "manifest.json"
        manifest_payload = _canonical_json(manifest.model_dump(mode="json"))
        self._write_bytes(manifest_path, manifest_payload)
        self.catalog.register_evaluation(
            evaluation_id=manifest.evaluation_id,
            factor_materialization_id=manifest.factor_materialization_id,
            manifest_uri=self._relative(manifest_path),
            manifest_sha256=hashlib.sha256(manifest_payload).hexdigest(),
            created_at=manifest.created_at,
        )
        return manifest

    def _load(
        self,
        evaluation_id: str,
        *,
        manifest_uri: str,
        manifest_sha256: str,
    ) -> FactorEvaluationManifest:
        path = (self.root / manifest_uri).resolve()
        self._relative(path)
        payload = path.read_bytes()
        if hashlib.sha256(payload).hexdigest() != manifest_sha256:
            raise FactorIntegrityError("factor evaluation manifest hash mismatch")
        try:
            manifest = FactorEvaluationManifest.model_validate_json(payload, strict=True)
        except ValueError as error:
            raise FactorIntegrityError("factor evaluation manifest is invalid") from error
        if manifest.evaluation_id != evaluation_id:
            raise FactorIntegrityError("factor evaluation catalog identity mismatch")
        for item in manifest.objects:
            object_path = (self.root / item.uri).resolve()
            self._relative(object_path)
            if not object_path.is_file() or self._file_sha256(object_path) != item.sha256:
                raise FactorIntegrityError("factor evaluation object hash mismatch")
            if item.row_count is not None and pq.read_table(object_path).num_rows != item.row_count:
                raise FactorIntegrityError("factor evaluation object row count mismatch")
        return manifest


__all__ = [
    "EvaluationLabelContext",
    "FactorEvaluationConfig",
    "FactorEvaluationManifest",
    "FactorEvaluationResult",
    "FactorEvaluationStore",
    "FactorEvaluationSummary",
    "FactorEvaluator",
    "HorizonEvaluation",
    "factor_ic_schema",
    "factor_quantile_schema",
    "forward_return_schema",
]
