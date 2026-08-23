"""Out-of-core PIT materialization of the managed fundamental factor suite."""

from __future__ import annotations

import hashlib
import json
import shutil
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path, PurePosixPath
from typing import Any, Literal, cast

import duckdb
import pyarrow as pa
import pyarrow.parquet as pq
from pydantic import BaseModel, ConfigDict, Field, model_validator

from trademaster.factors import factor_output_schema
from trademaster.factors.cross_section import CrossSectionCompositeFactor
from trademaster.factors.fundamental import (
    FundamentalMetricFactor,
    fundamental_factor_suite,
)
from trademaster.research.full_a import (
    CompletedAcquisitionOutcome,
    DownloadPlanManifest,
    DownloadTask,
    FullAResearchStore,
)
from trademaster.research.full_a_panel import (
    FactorResearchPanelBuilder,
    FactorResearchPanelManifest,
)

_SHA256 = r"^[0-9a-f]{64}$"
_LEGACY_BUILDER_VERSION = "full-a-fundamental/v1"
_BUILDER_VERSION = "full-a-fundamental/v2"
_REQUIRED_ENDPOINTS = (
    "balancesheet",
    "cashflow",
    "daily_basic",
    "fina_indicator",
    "income",
)
_INDUSTRY_ENDPOINT = "index_member_all"
_ATOMIC_SUITE = fundamental_factor_suite()
_ATOMIC_IDENTITIES = tuple(sorted(item.definition.identity for item in _ATOMIC_SUITE.atomic))
_GLOBAL_IDENTITY = _ATOMIC_SUITE.global_composite.definition.identity
_INDUSTRY_IDENTITY = _ATOMIC_SUITE.industry_composite.definition.identity
_DEFINITION_BINDINGS = tuple(
    sorted(
        (
            item.definition.factor_id,
            item.definition.version,
            item.definition.definition_sha256,
        )
        for item in _ATOMIC_SUITE.registrations
    )
)


def _canonical_json(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode()


def _sha256_json(value: object) -> str:
    return hashlib.sha256(_canonical_json(value)).hexdigest()


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _schema_sha256(path: Path) -> str:
    schema = pq.read_schema(path).remove_metadata()
    return hashlib.sha256(schema.serialize().to_pybytes()).hexdigest()


def _builder_code_sha256() -> str:
    return _file_sha256(Path(__file__))


def _quoted_path(path: Path) -> str:
    return "'" + str(path).replace("'", "''") + "'"


def _parquet_scan(paths: tuple[Path, ...]) -> str:
    if not paths:
        raise RuntimeError("fundamental endpoint has no evidence objects")
    values = ",".join(_quoted_path(path) for path in paths)
    return f"read_parquet([{values}], union_by_name=true)"


def fundamental_input_schema() -> pa.Schema:
    """Auditable monthly values selected before applying atomic transforms."""

    fields: list[Any] = [
        pa.field("instrument_id", pa.string(), nullable=False),
        pa.field("event_time", pa.timestamp("us", tz="UTC"), nullable=False),
        pa.field("pe_ttm", pa.float64(), nullable=True),
        pa.field("pb", pa.float64(), nullable=True),
        pa.field("dv_ttm", pa.float64(), nullable=True),
        pa.field("roe", pa.float64(), nullable=True),
        pa.field("grossprofit_margin", pa.float64(), nullable=True),
        pa.field("ocf_to_or", pa.float64(), nullable=True),
        pa.field("q_sales_yoy", pa.float64(), nullable=True),
        pa.field("q_profit_yoy", pa.float64(), nullable=True),
        pa.field("debt_to_assets", pa.float64(), nullable=True),
        pa.field("financial_ann_date", pa.date32(), nullable=True),
        pa.field("financial_known_at_session", pa.date32(), nullable=True),
        pa.field("financial_end_date", pa.date32(), nullable=True),
        pa.field("financial_update_flag", pa.string(), nullable=True),
    ]
    return pa.schema(fields)


class _LegacyFullAFundamentalConfig(BaseModel):
    """Exact v1 shape retained so immutable v1 manifests remain loadable."""

    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    schema_id: Literal["trademaster.full-a-fundamental-config/v1"] = (
        "trademaster.full-a-fundamental-config/v1"
    )
    observation_frequency: Literal["month_end"] = "month_end"
    known_at_field: Literal["ann_date"] = "ann_date"
    financial_revision_order: tuple[str, ...] = (
        "end_date_desc",
        "ann_date_desc",
        "update_flag_desc",
    )
    revision_tie_break: Literal["metric_lexicographic_desc"] = "metric_lexicographic_desc"
    industry_policy: Literal["complete_interval_coverage_or_block"] = (
        "complete_interval_coverage_or_block"
    )

    @model_validator(mode="after")
    def validate_revision_order(self) -> _LegacyFullAFundamentalConfig:
        if self.financial_revision_order != (
            "end_date_desc",
            "ann_date_desc",
            "update_flag_desc",
        ):
            raise ValueError("fundamental revision order is not canonical")
        return self

    @property
    def config_sha256(self) -> str:
        return _sha256_json(self.model_dump(mode="json"))

    @property
    def announcement_availability_policy(self) -> Literal["legacy_same_session_or_earlier"]:
        return "legacy_same_session_or_earlier"

    @property
    def revision_availability_policy(self) -> Literal["legacy_ann_date_ordered_revisions"]:
        return "legacy_ann_date_ordered_revisions"


class FullAFundamentalConfig(BaseModel):
    """Frozen conservative PIT semantics for date-only financial announcements."""

    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    schema_id: Literal["trademaster.full-a-fundamental-config/v2"] = (
        "trademaster.full-a-fundamental-config/v2"
    )
    observation_frequency: Literal["month_end"] = "month_end"
    known_at_field: Literal["ann_date"] = "ann_date"
    announcement_availability_policy: Literal["date_only_next_eligible_session"] = (
        "date_only_next_eligible_session"
    )
    revision_availability_policy: Literal["original_only_without_revision_known_at"] = (
        "original_only_without_revision_known_at"
    )
    financial_revision_order: tuple[str, ...] = (
        "end_date_desc",
        "ann_date_desc",
    )
    revision_tie_break: Literal["metric_lexicographic_desc"] = "metric_lexicographic_desc"
    industry_policy: Literal["complete_interval_coverage_or_block"] = (
        "complete_interval_coverage_or_block"
    )

    @model_validator(mode="after")
    def validate_revision_order(self) -> FullAFundamentalConfig:
        if self.financial_revision_order != (
            "end_date_desc",
            "ann_date_desc",
        ):
            raise ValueError("fundamental revision order is not canonical")
        return self

    @property
    def config_sha256(self) -> str:
        return _sha256_json(self.model_dump(mode="json"))


class FundamentalArtifactRef(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    name: Literal["factor_values", "fundamental_inputs", "source_objects"]
    uri: str = Field(min_length=1)
    content_sha256: str = Field(pattern=_SHA256)
    schema_sha256: str = Field(pattern=_SHA256)
    row_count: int = Field(ge=0)

    @model_validator(mode="after")
    def validate_uri(self) -> FundamentalArtifactRef:
        path = PurePosixPath(self.uri)
        if path.is_absolute() or ".." in path.parts or path.suffix != ".parquet":
            raise ValueError("fundamental artifact URI must be a relative Parquet path")
        return self


class FundamentalOutputStatus(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    factor_id: str
    factor_version: str
    status: Literal["materialized", "blocked"]
    blocker_code: str | None = None
    blocker_detail: str | None = None

    @model_validator(mode="after")
    def validate_status(self) -> FundamentalOutputStatus:
        blocked = self.status == "blocked"
        if blocked != (self.blocker_code is not None) or blocked != (
            self.blocker_detail is not None
        ):
            raise ValueError("fundamental output blocker fields disagree with status")
        return self

    @property
    def identity(self) -> tuple[str, str]:
        return (self.factor_id, self.factor_version)


class FullAFundamentalCoverage(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    observation_rows: int = Field(gt=0)
    daily_basic_rows: int = Field(ge=0)
    financial_rows: int = Field(ge=0)
    atomic_factor_rows: int = Field(gt=0)
    valid_atomic_rows: int = Field(ge=0)
    global_composite_rows: int = Field(gt=0)
    valid_global_rows: int = Field(ge=0)
    industry_composite_rows: int = Field(ge=0)
    valid_industry_rows: int = Field(ge=0)
    factor_rows: int = Field(gt=0)

    @model_validator(mode="after")
    def validate_counts(self) -> FullAFundamentalCoverage:
        if (
            self.daily_basic_rows > self.observation_rows
            or self.financial_rows > self.observation_rows
        ):
            raise ValueError("fundamental input coverage exceeds observations")
        if self.atomic_factor_rows != self.observation_rows * len(_ATOMIC_IDENTITIES):
            raise ValueError("atomic factor coverage is incomplete")
        if self.global_composite_rows != self.observation_rows:
            raise ValueError("global composite coverage is incomplete")
        if self.industry_composite_rows not in (0, self.observation_rows):
            raise ValueError("industry composite coverage is partial")
        if self.factor_rows != (
            self.atomic_factor_rows + self.global_composite_rows + self.industry_composite_rows
        ):
            raise ValueError("fundamental factor rows do not sum")
        if (
            self.valid_atomic_rows > self.atomic_factor_rows
            or self.valid_global_rows > self.global_composite_rows
            or self.valid_industry_rows > self.industry_composite_rows
        ):
            raise ValueError("valid fundamental rows exceed total rows")
        return self


class FullAFundamentalPanelManifest(BaseModel):
    """Immutable identity of one PIT fundamental panel and its blocked outputs."""

    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    schema_id: Literal[
        "trademaster.full-a-fundamental-manifest/v1",
        "trademaster.full-a-fundamental-manifest/v2",
    ]
    manifest_sha256: str = Field(pattern=_SHA256)
    materialization_sha256: str = Field(pattern=_SHA256)
    builder_version: Literal["full-a-fundamental/v1", "full-a-fundamental/v2"]
    builder_code_sha256: str = Field(pattern=_SHA256)
    plan_sha256: str = Field(pattern=_SHA256)
    panel_manifest_sha256: str = Field(pattern=_SHA256)
    panel_source_snapshot_sha256: str = Field(pattern=_SHA256)
    source_snapshot_sha256: str = Field(pattern=_SHA256)
    source_object_count: int = Field(gt=0)
    config: _LegacyFullAFundamentalConfig | FullAFundamentalConfig
    factor_definition_bindings: tuple[tuple[str, str, str], ...]
    outputs: tuple[FundamentalOutputStatus, ...]
    coverage: FullAFundamentalCoverage
    artifacts: tuple[FundamentalArtifactRef, ...]

    @model_validator(mode="after")
    def validate_identity(self) -> FullAFundamentalPanelManifest:
        legacy = self.builder_version == _LEGACY_BUILDER_VERSION
        expected_schema = (
            "trademaster.full-a-fundamental-manifest/v1"
            if legacy
            else "trademaster.full-a-fundamental-manifest/v2"
        )
        if self.schema_id != expected_schema or legacy != isinstance(
            self.config, _LegacyFullAFundamentalConfig
        ):
            raise ValueError("fundamental manifest schema, builder and config versions disagree")
        if self.factor_definition_bindings != _DEFINITION_BINDINGS:
            raise ValueError("fundamental definition bindings are not canonical")
        expected_identities = tuple(item[:2] for item in _DEFINITION_BINDINGS)
        if tuple(item.identity for item in self.outputs) != expected_identities:
            raise ValueError("fundamental output statuses are not canonical")
        if any(
            item.status != "materialized"
            for item in self.outputs
            if item.identity != _INDUSTRY_IDENTITY
        ):
            raise ValueError("atomic and global fundamental outputs cannot be blocked")
        industry = next(item for item in self.outputs if item.identity == _INDUSTRY_IDENTITY)
        if (industry.status == "materialized") != (self.coverage.industry_composite_rows > 0):
            raise ValueError("industry output status and coverage disagree")
        if self.artifacts != tuple(sorted(self.artifacts, key=lambda item: item.name)) or {
            item.name for item in self.artifacts
        } != {"factor_values", "fundamental_inputs", "source_objects"}:
            raise ValueError("fundamental artifact set is not canonical")
        artifacts = {item.name: item for item in self.artifacts}
        if artifacts["factor_values"].row_count != self.coverage.factor_rows:
            raise ValueError("fundamental factor artifact coverage differs")
        if artifacts["fundamental_inputs"].row_count != self.coverage.observation_rows:
            raise ValueError("fundamental input artifact coverage differs")
        if artifacts["source_objects"].row_count != self.source_object_count:
            raise ValueError("fundamental source artifact coverage differs")
        payload = self.model_dump(mode="json", exclude={"manifest_sha256"})
        if self.manifest_sha256 != _sha256_json(payload):
            raise ValueError("fundamental manifest content hash mismatch")
        return self

    def output(self, factor_id: str, factor_version: str) -> FundamentalOutputStatus:
        try:
            return next(
                item for item in self.outputs if item.identity == (factor_id, factor_version)
            )
        except StopIteration as error:
            raise KeyError(f"unknown fundamental output: {factor_id}@{factor_version}") from error

    @classmethod
    def build(
        cls,
        *,
        materialization_sha256: str,
        plan_sha256: str,
        panel: FactorResearchPanelManifest,
        source_snapshot_sha256: str,
        source_object_count: int,
        config: FullAFundamentalConfig,
        outputs: tuple[FundamentalOutputStatus, ...],
        coverage: FullAFundamentalCoverage,
        artifacts: tuple[FundamentalArtifactRef, ...],
    ) -> FullAFundamentalPanelManifest:
        ordered_outputs = tuple(sorted(outputs, key=lambda item: item.identity))
        ordered_artifacts = tuple(sorted(artifacts, key=lambda item: item.name))
        base: dict[str, object] = {
            "schema_id": "trademaster.full-a-fundamental-manifest/v2",
            "materialization_sha256": materialization_sha256,
            "builder_version": _BUILDER_VERSION,
            "builder_code_sha256": _builder_code_sha256(),
            "plan_sha256": plan_sha256,
            "panel_manifest_sha256": panel.manifest_sha256,
            "panel_source_snapshot_sha256": panel.source_snapshot_sha256,
            "source_snapshot_sha256": source_snapshot_sha256,
            "source_object_count": source_object_count,
            "config": config,
            "factor_definition_bindings": _DEFINITION_BINDINGS,
            "outputs": ordered_outputs,
            "coverage": coverage,
            "artifacts": ordered_artifacts,
        }
        hash_payload = {
            **base,
            "config": config.model_dump(mode="json"),
            "outputs": [item.model_dump(mode="json") for item in ordered_outputs],
            "coverage": coverage.model_dump(mode="json"),
            "artifacts": [item.model_dump(mode="json") for item in ordered_artifacts],
        }
        base["manifest_sha256"] = _sha256_json(hash_payload)
        return cls.model_validate(base)


class _SourceObject(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    task_id: str = Field(pattern=_SHA256)
    task_key: str
    endpoint: str
    request_sha256: str = Field(pattern=_SHA256)
    content_sha256: str = Field(pattern=_SHA256)
    row_count: int = Field(ge=0)
    evidence_uri: str
    acquisition_outcome: CompletedAcquisitionOutcome
    path: Path


class FullAFundamentalPanelBuilder:
    """Build monthly PIT inputs and managed fundamental values with DuckDB."""

    def __init__(
        self,
        *,
        store: FullAResearchStore,
        panel_builder: FactorResearchPanelBuilder,
        output_root: Path,
    ) -> None:
        self.store = store
        self.panel_builder = panel_builder
        self.output_root = output_root.resolve()
        if self.output_root == Path(self.output_root.anchor):
            raise ValueError("fundamental output root cannot be the filesystem root")
        self.runs = self.output_root / "runs"
        self.manifests = self.output_root / "manifests"

    @staticmethod
    @contextmanager
    def query() -> Iterator[duckdb.DuckDBPyConnection]:
        connection = duckdb.connect()
        try:
            # Cross-sectional floating aggregates must have a stable reduction order.
            connection.execute("SET threads = 1")
            yield connection
        finally:
            connection.close()

    @staticmethod
    def _required_tasks(plan: DownloadPlanManifest) -> tuple[DownloadTask, ...]:
        tasks = tuple(item for item in plan.tasks if item.endpoint in _REQUIRED_ENDPOINTS)
        dates = {
            dict(item.params).get("trade_date") for item in tasks if item.endpoint == "daily_basic"
        }
        expected_dates = {item.strftime("%Y%m%d") for item in plan.sessions}
        if dates != expected_dates:
            raise RuntimeError("fundamental daily_basic task coverage is incomplete")
        expected_instruments = set(plan.instruments)
        for endpoint in _REQUIRED_ENDPOINTS:
            if endpoint == "daily_basic":
                continue
            instruments = {
                dict(item.params).get("ts_code") for item in tasks if item.endpoint == endpoint
            }
            if instruments != expected_instruments:
                raise RuntimeError(f"fundamental source task coverage is incomplete for {endpoint}")
        return tuple(sorted(tasks, key=lambda item: item.task_key))

    def _source_objects(
        self,
        plan: DownloadPlanManifest,
    ) -> tuple[tuple[_SourceObject, ...], str, bool]:
        required_tasks = self._required_tasks(plan)
        industry_tasks = tuple(
            sorted(
                (item for item in plan.tasks if item.endpoint == _INDUSTRY_ENDPOINT),
                key=lambda item: item.task_key,
            )
        )
        requested = (*required_tasks, *industry_tasks)
        snapshot = self.store.completed_evidence_snapshot(
            plan,
            endpoints=tuple(sorted((*_REQUIRED_ENDPOINTS, _INDUSTRY_ENDPOINT))),
        )
        by_id = {item.task.task_id: item for item in snapshot.evidence}
        required_ids = {item.task_id for item in required_tasks}
        objects: list[_SourceObject] = []
        industry_complete = bool(industry_tasks)
        for task in requested:
            evidence = by_id.get(task.task_id)
            if evidence is None:
                if task.task_id in required_ids:
                    raise RuntimeError(f"fundamental source task is not completed: {task.task_key}")
                industry_complete = False
                continue
            uri = PurePosixPath(evidence.evidence_uri)
            if uri.is_absolute() or ".." in uri.parts:
                raise RuntimeError("fundamental evidence URI is invalid")
            path = (self.store.root / uri).resolve()
            try:
                path.relative_to(self.store.root)
            except ValueError as error:
                raise RuntimeError("fundamental evidence path escapes its root") from error
            if not path.is_file() or _file_sha256(path) != evidence.content_sha256:
                raise RuntimeError("fundamental source evidence content hash mismatch")
            try:
                metadata = pq.read_metadata(path)
            except Exception as error:
                raise RuntimeError("fundamental source evidence Parquet is unreadable") from error
            if metadata.num_rows != evidence.row_count:
                raise RuntimeError("fundamental source evidence row count mismatch")
            if not set(task.fields) <= set(metadata.schema.to_arrow_schema().names):
                raise RuntimeError("fundamental source evidence schema mismatch")
            objects.append(
                _SourceObject(
                    task_id=task.task_id,
                    task_key=task.task_key,
                    endpoint=task.endpoint,
                    request_sha256=evidence.request_sha256,
                    content_sha256=evidence.content_sha256,
                    row_count=evidence.row_count,
                    evidence_uri=uri.as_posix(),
                    acquisition_outcome=evidence.acquisition_outcome,
                    path=path,
                )
            )
        return tuple(objects), snapshot.snapshot_sha256, industry_complete

    @staticmethod
    def _write_immutable(path: Path, payload: bytes) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists():
            if path.read_bytes() != payload:
                raise RuntimeError("fundamental immutable object identity collision")
            return
        with tempfile.NamedTemporaryFile(
            dir=path.parent,
            prefix="fundamental-manifest-",
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

    def artifact_path(
        self,
        manifest: FullAFundamentalPanelManifest,
        name: str,
    ) -> Path:
        try:
            artifact = next(item for item in manifest.artifacts if item.name == name)
        except StopIteration as error:
            raise KeyError(f"unknown fundamental artifact: {name}") from error
        path = (self.output_root / PurePosixPath(artifact.uri)).resolve()
        try:
            path.relative_to(self.output_root)
        except ValueError as error:
            raise RuntimeError("fundamental artifact path escapes its root") from error
        return path

    def _verify_artifacts(self, manifest: FullAFundamentalPanelManifest) -> None:
        for artifact in manifest.artifacts:
            path = self.artifact_path(manifest, artifact.name)
            if not path.is_file() or _file_sha256(path) != artifact.content_sha256:
                raise RuntimeError("fundamental artifact content hash mismatch")
            try:
                metadata = pq.read_metadata(path)
                schema_sha256 = _schema_sha256(path)
            except Exception as error:
                raise RuntimeError("fundamental artifact Parquet is unreadable") from error
            if metadata.num_rows != artifact.row_count or schema_sha256 != artifact.schema_sha256:
                raise RuntimeError("fundamental artifact metadata mismatch")

    def load_manifest(self, manifest_sha256: str) -> FullAFundamentalPanelManifest:
        if len(manifest_sha256) != 64 or any(
            char not in "0123456789abcdef" for char in manifest_sha256
        ):
            raise ValueError("fundamental manifest SHA-256 is invalid")
        try:
            payload = (self.manifests / f"{manifest_sha256}.json").read_bytes()
        except OSError as error:
            raise RuntimeError("fundamental manifest is missing") from error
        try:
            manifest = FullAFundamentalPanelManifest.model_validate_json(payload, strict=True)
        except ValueError as error:
            raise RuntimeError("fundamental manifest is invalid") from error
        if manifest.manifest_sha256 != manifest_sha256:
            raise RuntimeError("fundamental manifest path and identity differ")
        self._verify_artifacts(manifest)
        return manifest

    def build(
        self,
        plan: DownloadPlanManifest,
        panel_manifest_sha256: str,
        *,
        config: FullAFundamentalConfig | None = None,
    ) -> FullAFundamentalPanelManifest:
        resolved_config = config or FullAFundamentalConfig()
        source_objects, source_snapshot_sha256, industry_complete = self._source_objects(plan)
        panel = self.panel_builder.load_manifest(panel_manifest_sha256)
        if panel.plan_sha256 != plan.plan_sha256:
            raise RuntimeError("fundamental and market panel plans differ")
        if panel.config.observation_frequency != resolved_config.observation_frequency:
            raise RuntimeError("fundamental panel requires a month-end market panel")
        materialization_sha256 = _sha256_json(
            {
                "builder_version": _BUILDER_VERSION,
                "builder_code_sha256": _builder_code_sha256(),
                "plan_sha256": plan.plan_sha256,
                "panel_manifest_sha256": panel.manifest_sha256,
                "panel_source_snapshot_sha256": panel.source_snapshot_sha256,
                "source_snapshot_sha256": source_snapshot_sha256,
                "config": resolved_config.model_dump(mode="json"),
                "factor_definition_bindings": _DEFINITION_BINDINGS,
            }
        )
        run_root = self.runs / materialization_sha256
        existing = run_root / "manifest.json"
        if existing.is_file():
            payload = existing.read_bytes()
            try:
                parsed = FullAFundamentalPanelManifest.model_validate_json(payload, strict=True)
            except ValueError as error:
                raise RuntimeError("fundamental run manifest is invalid") from error
            self._verify_artifacts(parsed)
            self._write_immutable(self.manifests / f"{parsed.manifest_sha256}.json", payload)
            loaded = self.load_manifest(parsed.manifest_sha256)
            if parsed != loaded or parsed.materialization_sha256 != materialization_sha256:
                raise RuntimeError("fundamental materialization identity collision")
            return loaded

        self.runs.mkdir(parents=True, exist_ok=True)
        self.manifests.mkdir(parents=True, exist_ok=True)
        temporary = Path(tempfile.mkdtemp(prefix="fundamental-", dir=self.runs))
        try:
            manifest = self._materialize(
                plan=plan,
                panel=panel,
                config=resolved_config,
                source_objects=source_objects,
                source_snapshot_sha256=source_snapshot_sha256,
                industry_complete=industry_complete,
                materialization_sha256=materialization_sha256,
                temporary=temporary,
            )
            payload = _canonical_json(manifest.model_dump(mode="json"))
            (temporary / "manifest.json").write_bytes(payload)
            try:
                temporary.replace(run_root)
            except FileExistsError:
                shutil.rmtree(temporary)
            self._write_immutable(self.manifests / f"{manifest.manifest_sha256}.json", payload)
            return self.load_manifest(manifest.manifest_sha256)
        finally:
            if temporary.exists():
                shutil.rmtree(temporary)

    def _materialize(
        self,
        *,
        plan: DownloadPlanManifest,
        panel: FactorResearchPanelManifest,
        config: FullAFundamentalConfig,
        source_objects: tuple[_SourceObject, ...],
        source_snapshot_sha256: str,
        industry_complete: bool,
        materialization_sha256: str,
        temporary: Path,
    ) -> FullAFundamentalPanelManifest:
        by_endpoint = {
            endpoint: tuple(item.path for item in source_objects if item.endpoint == endpoint)
            for endpoint in (*_REQUIRED_ENDPOINTS, _INDUSTRY_ENDPOINT)
        }
        source_table = pa.table(
            {
                "task_id": [item.task_id for item in source_objects],
                "task_key": [item.task_key for item in source_objects],
                "endpoint": [item.endpoint for item in source_objects],
                "request_sha256": [item.request_sha256 for item in source_objects],
                "content_sha256": [item.content_sha256 for item in source_objects],
                "row_count": pa.array([item.row_count for item in source_objects], type=pa.int64()),
                "evidence_uri": [item.evidence_uri for item in source_objects],
                "acquisition_outcome": [item.acquisition_outcome for item in source_objects],
            }
        )
        pq.write_table(source_table, temporary / "source_objects.parquet", compression="zstd")

        month_last = {(item.year, item.month): item for item in plan.sessions}
        observation_dates = tuple(sorted(month_last.values()))
        observation_table = pa.table(
            {"session_date": pa.array(observation_dates, type=pa.date32())}
        )
        universe_path = self.panel_builder.artifact_path(panel, "universe")
        market_path = self.panel_builder.artifact_path(panel, "market")
        spill = temporary / ".duckdb-spill"
        spill.mkdir()
        with self.query() as connection:
            connection.execute(f"SET temp_directory = {_quoted_path(spill)}")
            connection.register("observation_dates", observation_table)
            connection.execute(
                "CREATE TEMP VIEW panel_universe AS SELECT * FROM read_parquet("
                f"{_quoted_path(universe_path)})"
            )
            connection.execute(
                "CREATE TEMP VIEW panel_market AS SELECT * FROM read_parquet("
                f"{_quoted_path(market_path)})"
            )
            for endpoint in ("daily_basic", "fina_indicator"):
                connection.execute(
                    f"CREATE TEMP VIEW raw_{endpoint} AS SELECT * FROM "
                    f"{_parquet_scan(by_endpoint[endpoint])}"
                )
            if industry_complete:
                connection.execute(
                    "CREATE TEMP VIEW raw_index_member_all AS SELECT * FROM "
                    f"{_parquet_scan(by_endpoint[_INDUSTRY_ENDPOINT])}"
                )
            self._validate_source_schemas(connection, industry_complete=industry_complete)
            self._create_input_views(connection)
            self._copy_query(
                connection,
                "SELECT * FROM fundamental_inputs ORDER BY event_time, instrument_id",
                temporary / "fundamental_inputs.parquet",
                schema=fundamental_input_schema(),
            )
            connection.execute(
                "CREATE OR REPLACE TEMP VIEW fundamental_inputs AS SELECT * FROM read_parquet("
                f"{_quoted_path(temporary / 'fundamental_inputs.parquet')})"
            )
            self._create_atomic_view(connection)
            self._create_composite_view(
                connection,
                output_view="global_composite",
                factor_id=_GLOBAL_IDENTITY[0],
                group_view=None,
            )
            industry_status = self._create_industry_composite(
                connection,
                industry_complete=industry_complete,
            )
            factor_views = ["atomic_factor_values", "global_composite"]
            if industry_status.status == "materialized":
                factor_views.append("industry_composite")
            factor_query = " UNION ALL ".join(f"SELECT * FROM {item}" for item in factor_views)
            self._copy_query(
                connection,
                f"SELECT * FROM ({factor_query}) ORDER BY event_time, instrument_id, factor_id",
                temporary / "factor_values.parquet",
                schema=factor_output_schema(),
            )
            connection.execute(
                "CREATE TEMP VIEW factor_values AS SELECT * FROM read_parquet("
                f"{_quoted_path(temporary / 'factor_values.parquet')})"
            )
            coverage = self._coverage(connection, industry_status)
        try:
            spill.rmdir()
        except OSError as error:
            raise RuntimeError("DuckDB spill directory was not cleaned") from error

        outputs = tuple(
            sorted(
                (
                    *(
                        FundamentalOutputStatus(
                            factor_id=factor_id,
                            factor_version=version,
                            status="materialized",
                        )
                        for factor_id, version in (*_ATOMIC_IDENTITIES, _GLOBAL_IDENTITY)
                    ),
                    industry_status,
                ),
                key=lambda item: item.identity,
            )
        )
        run_prefix = PurePosixPath("runs") / materialization_sha256
        artifacts = tuple(
            self._artifact_ref(
                name=cast(
                    Literal["factor_values", "fundamental_inputs", "source_objects"],
                    name,
                ),
                path=temporary / filename,
                uri=(run_prefix / filename).as_posix(),
            )
            for name, filename in (
                ("factor_values", "factor_values.parquet"),
                ("fundamental_inputs", "fundamental_inputs.parquet"),
                ("source_objects", "source_objects.parquet"),
            )
        )
        return FullAFundamentalPanelManifest.build(
            materialization_sha256=materialization_sha256,
            plan_sha256=plan.plan_sha256,
            panel=panel,
            source_snapshot_sha256=source_snapshot_sha256,
            source_object_count=len(source_objects),
            config=config,
            outputs=outputs,
            coverage=coverage,
            artifacts=artifacts,
        )

    @staticmethod
    def _validate_source_schemas(
        connection: duckdb.DuckDBPyConnection,
        *,
        industry_complete: bool,
    ) -> None:
        required = {
            "daily_basic": {"ts_code", "trade_date", "pe_ttm", "pb", "dv_ttm"},
            "fina_indicator": {
                "ts_code",
                "ann_date",
                "end_date",
                "update_flag",
                "roe",
                "grossprofit_margin",
                "ocf_to_or",
                "q_sales_yoy",
                "q_profit_yoy",
                "debt_to_assets",
            },
        }
        if industry_complete:
            required["index_member_all"] = {
                "ts_code",
                "l1_code",
                "in_date",
                "out_date",
            }
        for endpoint, fields in required.items():
            actual = {
                str(row[0]) for row in connection.execute(f"DESCRIBE raw_{endpoint}").fetchall()
            }
            if not fields <= actual:
                raise RuntimeError(f"fundamental source schema is incomplete for {endpoint}")
        duplicate = connection.execute(
            """
            SELECT count(*) FROM (
                SELECT ts_code, trade_date, count(*) AS observations
                FROM raw_daily_basic GROUP BY ts_code, trade_date HAVING count(*) > 1
            )
            """
        ).fetchone()
        if duplicate is None or int(duplicate[0]) != 0:
            raise RuntimeError("daily_basic contains duplicate instrument sessions")
        malformed = connection.execute(
            """
            SELECT count(*) FROM raw_fina_indicator
            WHERE ann_date IS NULL OR end_date IS NULL
               OR try_strptime(trim(CAST(ann_date AS VARCHAR)), '%Y%m%d') IS NULL
               OR try_strptime(trim(CAST(end_date AS VARCHAR)), '%Y%m%d') IS NULL
            """
        ).fetchone()
        if malformed is None or int(malformed[0]) != 0:
            raise RuntimeError("fina_indicator contains malformed PIT dates")

    @staticmethod
    def _create_input_views(connection: duckdb.DuckDBPyConnection) -> None:
        connection.execute(
            """
            CREATE TEMP VIEW observations AS
            SELECT CAST(u.instrument_id AS VARCHAR) AS instrument_id, u.event_time
            FROM panel_universe u
            JOIN observation_dates d ON CAST(u.event_time AS DATE) = d.session_date
            JOIN panel_market m
              ON m.instrument_id = u.instrument_id AND m.event_time = u.event_time
            WHERE u.eligible
            """
        )
        duplicate = connection.execute(
            """
            SELECT count(*) - count(DISTINCT (instrument_id, event_time)) FROM observations
            """
        ).fetchone()
        if duplicate is None or int(duplicate[0]) != 0:
            raise RuntimeError("fundamental observations contain duplicate keys")
        connection.execute(
            """
            CREATE TEMP VIEW eligible_sessions AS
            SELECT DISTINCT CAST(event_time AS DATE) AS session_date
            FROM panel_market
            """
        )
        connection.execute(
            """
            CREATE TEMP VIEW typed_fina AS
            SELECT
                CAST(ts_code AS VARCHAR) AS instrument_id,
                try_strptime(trim(CAST(ann_date AS VARCHAR)), '%Y%m%d')::DATE AS ann_date,
                try_strptime(trim(CAST(end_date AS VARCHAR)), '%Y%m%d')::DATE AS end_date,
                CAST(update_flag AS VARCHAR) AS update_flag,
                try_cast(roe AS DOUBLE) AS roe,
                try_cast(grossprofit_margin AS DOUBLE) AS grossprofit_margin,
                try_cast(ocf_to_or AS DOUBLE) AS ocf_to_or,
                try_cast(q_sales_yoy AS DOUBLE) AS q_sales_yoy,
                try_cast(q_profit_yoy AS DOUBLE) AS q_profit_yoy,
                try_cast(debt_to_assets AS DOUBLE) AS debt_to_assets
            FROM raw_fina_indicator
            WHERE trim(CAST(update_flag AS VARCHAR)) = '0'
            """
        )
        connection.execute(
            """
            CREATE TEMP VIEW available_fina AS
            SELECT f.*,
                   (
                       SELECT min(session.session_date)
                       FROM eligible_sessions session
                       WHERE session.session_date > f.ann_date
                   ) AS known_at_session
            FROM typed_fina f
            """
        )
        connection.execute(
            """
            CREATE TEMP VIEW selected_fina AS
            SELECT o.instrument_id, o.event_time,
                   f.ann_date, f.known_at_session, f.end_date, f.update_flag,
                   f.roe, f.grossprofit_margin, f.ocf_to_or,
                   f.q_sales_yoy, f.q_profit_yoy, f.debt_to_assets
            FROM observations o
            LEFT JOIN available_fina f
              ON f.instrument_id = o.instrument_id
             AND f.known_at_session <= CAST(o.event_time AS DATE)
             AND f.end_date <= CAST(o.event_time AS DATE)
            QUALIFY row_number() OVER (
                PARTITION BY o.instrument_id, o.event_time
                ORDER BY f.end_date DESC NULLS LAST,
                         f.ann_date DESC NULLS LAST,
                         f.roe DESC NULLS LAST,
                         f.grossprofit_margin DESC NULLS LAST,
                         f.ocf_to_or DESC NULLS LAST,
                         f.q_sales_yoy DESC NULLS LAST,
                         f.q_profit_yoy DESC NULLS LAST,
                         f.debt_to_assets DESC NULLS LAST
            ) = 1
            """
        )
        connection.execute(
            """
            CREATE TEMP VIEW fundamental_inputs AS
            SELECT
                o.instrument_id,
                o.event_time,
                try_cast(d.pe_ttm AS DOUBLE) AS pe_ttm,
                try_cast(d.pb AS DOUBLE) AS pb,
                try_cast(d.dv_ttm AS DOUBLE) AS dv_ttm,
                f.roe,
                f.grossprofit_margin,
                f.ocf_to_or,
                f.q_sales_yoy,
                f.q_profit_yoy,
                f.debt_to_assets,
                f.ann_date AS financial_ann_date,
                f.known_at_session AS financial_known_at_session,
                f.end_date AS financial_end_date,
                f.update_flag AS financial_update_flag
            FROM observations o
            LEFT JOIN raw_daily_basic d
              ON CAST(d.ts_code AS VARCHAR) = o.instrument_id
             AND try_strptime(CAST(d.trade_date AS VARCHAR), '%Y%m%d')::DATE
                 = CAST(o.event_time AS DATE)
            LEFT JOIN selected_fina f
              ON f.instrument_id = o.instrument_id AND f.event_time = o.event_time
            """
        )

    @staticmethod
    def _create_atomic_view(connection: duckdb.DuckDBPyConnection) -> None:
        expressions: list[str] = []
        for registration in _ATOMIC_SUITE.atomic:
            factor = registration.factor
            if not isinstance(factor, FundamentalMetricFactor):
                raise TypeError("fundamental atomic implementation is unsupported")
            field = factor.source_field
            finite = f"{field} IS NOT NULL AND isfinite({field})"
            if factor.transform == "positive_reciprocal":
                valid = f"{finite} AND {field} > 0"
                value = f"1.0 / {field}"
            elif factor.transform == "negate":
                valid = finite
                value = f"-{field}"
            elif factor.transform == "identity":
                valid = finite
                value = field
            else:
                raise RuntimeError("fundamental atomic transform is unsupported")
            expressions.append(
                f"""
                SELECT instrument_id, event_time,
                       '{factor.spec.factor_id}' AS factor_id,
                       '{factor.spec.version}' AS factor_version,
                       CAST(CASE WHEN {valid} THEN {value} ELSE 0.0 END AS DOUBLE) AS value,
                       CAST(coalesce(({valid}), false) AS BOOLEAN) AS is_valid
                FROM fundamental_inputs
                """
            )
        connection.execute(
            "CREATE TEMP VIEW atomic_factor_values AS " + " UNION ALL ".join(expressions)
        )

    @staticmethod
    def _create_composite_view(
        connection: duckdb.DuckDBPyConnection,
        *,
        output_view: str,
        factor_id: str,
        group_view: str | None,
    ) -> None:
        factor = (
            _ATOMIC_SUITE.global_composite.factor
            if group_view is None
            else _ATOMIC_SUITE.industry_composite.factor
        )
        if not isinstance(factor, CrossSectionCompositeFactor):
            raise TypeError("fundamental composite implementation is unsupported")
        transform = factor.transform
        required = tuple(item for item in factor.components if item.required)
        required_condition = " AND ".join(
            f"count(*) FILTER (WHERE a.factor_id = '{item.factor_id}' AND a.is_valid) = 1"
            for item in required
        )
        if not required_condition:
            required_condition = "true"
        groups = (
            "SELECT instrument_id, event_time, '__all__' AS group_id FROM observations"
            if group_view is None
            else f"SELECT instrument_id, event_time, industry_id AS group_id FROM {group_view}"
        )
        weights = (
            "CASE a.factor_id "
            + " ".join(f"WHEN '{item.factor_id}' THEN {item.weight}" for item in factor.components)
            + " ELSE 0.0 END"
        )
        total_weight = sum(item.weight for item in factor.components)
        connection.execute(
            f"""
            CREATE TEMP VIEW {output_view} AS
            WITH groups AS ({groups}),
            eligible AS (
                SELECT g.instrument_id, g.event_time, g.group_id
                FROM groups g
                JOIN atomic_factor_values a USING (instrument_id, event_time)
                GROUP BY g.instrument_id, g.event_time, g.group_id
                HAVING count(*) FILTER (WHERE a.is_valid)
                           >= {transform.minimum_valid_components}
                   AND {required_condition}
            ),
            component_bounds AS (
                SELECT e.event_time, e.group_id, a.factor_id,
                       quantile_cont(a.value, {transform.winsor_lower}) AS lower_value,
                       quantile_cont(a.value, {transform.winsor_upper}) AS upper_value
                FROM eligible e
                JOIN atomic_factor_values a USING (instrument_id, event_time)
                WHERE a.is_valid
                GROUP BY e.event_time, e.group_id, a.factor_id
            ),
            clipped AS (
                SELECT e.instrument_id, e.event_time, e.group_id,
                       a.factor_id,
                       least(greatest(a.value, b.lower_value), b.upper_value) AS clipped_value
                FROM eligible e
                JOIN atomic_factor_values a USING (instrument_id, event_time)
                JOIN component_bounds b
                  ON b.event_time = e.event_time AND b.group_id = e.group_id
                 AND b.factor_id = a.factor_id
                WHERE a.is_valid
            ),
            standardized AS (
                SELECT *, avg(clipped_value) OVER w AS component_mean,
                       stddev_pop(clipped_value) OVER w AS component_stddev
                FROM clipped
                WINDOW w AS (PARTITION BY event_time, group_id, factor_id)
            ),
            scores AS (
                SELECT e.instrument_id, e.event_time,
                       sum(({weights}) / {total_weight}
                           * CASE WHEN s.component_stddev = 0 THEN 0.0
                                  ELSE (s.clipped_value - s.component_mean)
                                       / s.component_stddev END) AS score
                FROM eligible e
                JOIN standardized s
                  ON s.instrument_id = e.instrument_id AND s.event_time = e.event_time
                 AND s.group_id = e.group_id
                JOIN atomic_factor_values a
                  ON a.instrument_id = s.instrument_id AND a.event_time = s.event_time
                 AND a.factor_id = s.factor_id
                GROUP BY e.instrument_id, e.event_time
            )
            SELECT g.instrument_id, g.event_time,
                   '{factor_id}' AS factor_id,
                   '1' AS factor_version,
                   CAST(CASE WHEN s.instrument_id IS NULL THEN 0.0 ELSE s.score END AS DOUBLE)
                       AS value,
                   CAST(s.instrument_id IS NOT NULL AS BOOLEAN) AS is_valid
            FROM groups g
            LEFT JOIN scores s USING (instrument_id, event_time)
            """
        )

    def _create_industry_composite(
        self,
        connection: duckdb.DuckDBPyConnection,
        *,
        industry_complete: bool,
    ) -> FundamentalOutputStatus:
        if not industry_complete:
            return FundamentalOutputStatus(
                factor_id=_INDUSTRY_IDENTITY[0],
                factor_version=_INDUSTRY_IDENTITY[1],
                status="blocked",
                blocker_code="industry_membership_evidence_incomplete",
                blocker_detail=(
                    "All planned index_member_all evidence must be completed before interval "
                    "coverage can be assessed."
                ),
            )
        connection.execute(
            """
            CREATE TEMP VIEW membership_intervals AS
            SELECT DISTINCT CAST(ts_code AS VARCHAR) AS instrument_id,
                   trim(CAST(l1_code AS VARCHAR)) AS industry_id,
                   try_strptime(trim(CAST(in_date AS VARCHAR)), '%Y%m%d')::DATE AS in_date,
                   CASE WHEN out_date IS NULL OR trim(CAST(out_date AS VARCHAR)) = '' THEN NULL
                        ELSE try_strptime(trim(CAST(out_date AS VARCHAR)), '%Y%m%d')::DATE END
                        AS out_date
            FROM raw_index_member_all
            WHERE ts_code IS NOT NULL AND l1_code IS NOT NULL AND in_date IS NOT NULL
              AND trim(CAST(l1_code AS VARCHAR)) <> ''
              AND try_strptime(trim(CAST(in_date AS VARCHAR)), '%Y%m%d') IS NOT NULL
            """
        )
        connection.execute(
            """
            CREATE TEMP VIEW industry_matches AS
            SELECT o.instrument_id, o.event_time,
                   count(DISTINCT m.industry_id) AS match_count,
                   min(m.industry_id) AS industry_id
            FROM observations o
            LEFT JOIN membership_intervals m
              ON m.instrument_id = o.instrument_id
             AND m.in_date <= CAST(o.event_time AS DATE)
             AND (m.out_date IS NULL OR CAST(o.event_time AS DATE) <= m.out_date)
            GROUP BY o.instrument_id, o.event_time
            """
        )
        counts = connection.execute(
            """
            SELECT count(*) FILTER (WHERE match_count = 0),
                   count(*) FILTER (WHERE match_count > 1)
            FROM industry_matches
            """
        ).fetchone()
        if counts is None:
            raise RuntimeError("industry membership coverage returned no result")
        gaps, ambiguous = int(counts[0]), int(counts[1])
        if gaps or ambiguous:
            code = (
                "historical_industry_membership_interval_ambiguous"
                if ambiguous
                else "historical_industry_membership_interval_gap"
            )
            return FundamentalOutputStatus(
                factor_id=_INDUSTRY_IDENTITY[0],
                factor_version=_INDUSTRY_IDENTITY[1],
                status="blocked",
                blocker_code=code,
                blocker_detail=f"interval gaps={gaps}, ambiguous observations={ambiguous}",
            )
        connection.execute(
            """
            CREATE TEMP VIEW observation_industries AS
            SELECT instrument_id, event_time, industry_id FROM industry_matches
            WHERE match_count = 1
            """
        )
        self._create_composite_view(
            connection,
            output_view="industry_composite",
            factor_id=_INDUSTRY_IDENTITY[0],
            group_view="observation_industries",
        )
        return FundamentalOutputStatus(
            factor_id=_INDUSTRY_IDENTITY[0],
            factor_version=_INDUSTRY_IDENTITY[1],
            status="materialized",
        )

    @staticmethod
    def _copy_query(
        connection: duckdb.DuckDBPyConnection,
        query: str,
        path: Path,
        *,
        schema: pa.Schema,
    ) -> None:
        reader = connection.execute(query).to_arrow_reader(122_880)
        with pq.ParquetWriter(path, schema, compression="zstd") as writer:
            for batch in reader:
                writer.write_table(pa.Table.from_batches([batch]).cast(schema))

    @staticmethod
    def _coverage(
        connection: duckdb.DuckDBPyConnection,
        industry_status: FundamentalOutputStatus,
    ) -> FullAFundamentalCoverage:
        inputs = connection.execute(
            """
            SELECT count(*),
                   count(*) FILTER (WHERE pe_ttm IS NOT NULL OR pb IS NOT NULL
                                           OR dv_ttm IS NOT NULL),
                   count(*) FILTER (WHERE financial_ann_date IS NOT NULL)
            FROM fundamental_inputs
            """
        ).fetchone()
        atomic = connection.execute(
            "SELECT count(*), count(*) FILTER (WHERE is_valid) FROM atomic_factor_values"
        ).fetchone()
        global_rows = connection.execute(
            "SELECT count(*), count(*) FILTER (WHERE is_valid) FROM global_composite"
        ).fetchone()
        factor = connection.execute("SELECT count(*) FROM factor_values").fetchone()
        if inputs is None or atomic is None or global_rows is None or factor is None:
            raise RuntimeError("fundamental coverage query returned no result")
        industry_rows = (0, 0)
        if industry_status.status == "materialized":
            selected = connection.execute(
                "SELECT count(*), count(*) FILTER (WHERE is_valid) FROM industry_composite"
            ).fetchone()
            if selected is None:
                raise RuntimeError("industry composite coverage returned no result")
            industry_rows = (int(selected[0]), int(selected[1]))
        return FullAFundamentalCoverage(
            observation_rows=int(inputs[0]),
            daily_basic_rows=int(inputs[1]),
            financial_rows=int(inputs[2]),
            atomic_factor_rows=int(atomic[0]),
            valid_atomic_rows=int(atomic[1]),
            global_composite_rows=int(global_rows[0]),
            valid_global_rows=int(global_rows[1]),
            industry_composite_rows=industry_rows[0],
            valid_industry_rows=industry_rows[1],
            factor_rows=int(factor[0]),
        )

    @staticmethod
    def _artifact_ref(
        *,
        name: Literal["factor_values", "fundamental_inputs", "source_objects"],
        path: Path,
        uri: str,
    ) -> FundamentalArtifactRef:
        return FundamentalArtifactRef(
            name=name,
            uri=uri,
            content_sha256=_file_sha256(path),
            schema_sha256=_schema_sha256(path),
            row_count=pq.read_metadata(path).num_rows,
        )


__all__ = [
    "FullAFundamentalConfig",
    "FullAFundamentalCoverage",
    "FullAFundamentalPanelBuilder",
    "FullAFundamentalPanelManifest",
    "FundamentalArtifactRef",
    "FundamentalOutputStatus",
    "fundamental_input_schema",
]
