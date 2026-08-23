"""Out-of-core PIT panel and public-factor materialization for full-A research."""

from __future__ import annotations

import hashlib
import json
import shutil
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, date, datetime
from pathlib import Path, PurePosixPath
from typing import Any, Literal, cast

import duckdb
import pyarrow as pa
import pyarrow.parquet as pq
from pydantic import BaseModel, ConfigDict, Field, model_validator

from trademaster.factors import factor_output_schema, public_executable_factor_suite
from trademaster.research.factor_evaluation import (
    forward_return_v2_schema,
    full_a_market_schema,
    full_a_universe_schema,
)
from trademaster.research.full_a import (
    CompletedAcquisitionOutcome,
    DownloadPlanManifest,
    DownloadTask,
    FullAResearchStore,
)

_SHA256 = r"^[0-9a-f]{64}$"
_BSE_FIRST_SESSION = date(2021, 11, 15)
_LEGACY_BUILDER_VERSION = "full-a-panel/v1"
_BUILDER_VERSION = "full-a-panel/v2"
_REQUIRED_ENDPOINTS = frozenset(
    {
        "stock_basic",
        "daily",
        "adj_factor",
        "daily_basic",
        "stk_limit",
        "suspend_d",
        "stock_st",
    }
)
_SESSION_ENDPOINTS = frozenset(_REQUIRED_ENDPOINTS - {"stock_basic"})
_FACTOR_IDENTITIES = (
    ("gtja191.alpha014", "1"),
    ("gtja191.alpha015", "1"),
    ("gtja191.alpha018", "1"),
    ("gtja191.alpha020", "1"),
    ("gtja191.alpha031", "1"),
    ("gtja191.alpha034", "1"),
    ("gtja191.alpha046", "1"),
    ("gtja191.alpha053", "1"),
    ("gtja191.alpha058", "1"),
    ("gtja191.alpha088", "1"),
    ("huatai53.size.log_total_market_value", "1"),
)


def _factor_definition_bindings() -> tuple[tuple[str, str, str], ...]:
    return tuple(
        sorted(
            (
                registration.definition.factor_id,
                registration.definition.version,
                registration.definition.definition_sha256,
            )
            for registration in public_executable_factor_suite().registrations
        )
    )


def _builder_code_sha256() -> str:
    return hashlib.sha256(Path(__file__).read_bytes()).hexdigest()


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


def _quoted_path(path: Path) -> str:
    return "'" + str(path).replace("'", "''") + "'"


def _parquet_scan(paths: tuple[Path, ...]) -> str:
    if not paths:
        raise RuntimeError("required source endpoint has no evidence objects")
    values = ",".join(_quoted_path(path) for path in paths)
    return f"read_parquet([{values}], union_by_name=true)"


def full_a_dense_panel_schema() -> pa.Schema:
    """Stable schema of the dense active-instrument by session panel."""

    fields: list[Any] = [
        pa.field("instrument_id", pa.string(), nullable=False),
        pa.field("venue", pa.string(), nullable=False),
        pa.field("session_date", pa.date32(), nullable=False),
        pa.field("event_time", pa.timestamp("us", tz="UTC"), nullable=False),
        pa.field("session_index", pa.int32(), nullable=False),
        pa.field("open", pa.float64(), nullable=True),
        pa.field("high", pa.float64(), nullable=True),
        pa.field("low", pa.float64(), nullable=True),
        pa.field("close", pa.float64(), nullable=True),
        pa.field("pre_close", pa.float64(), nullable=True),
        pa.field("volume_lots", pa.float64(), nullable=True),
        pa.field("amount_thousand_cny", pa.float64(), nullable=True),
        pa.field("adj_factor", pa.float64(), nullable=True),
        pa.field("turnover_rate", pa.float64(), nullable=True),
        pa.field("total_market_value_10k_cny", pa.float64(), nullable=True),
        pa.field("circulating_market_value_10k_cny", pa.float64(), nullable=True),
        pa.field("pe_ttm", pa.float64(), nullable=True),
        pa.field("pb", pa.float64(), nullable=True),
        pa.field("ps_ttm", pa.float64(), nullable=True),
        pa.field("dividend_yield_ttm", pa.float64(), nullable=True),
        pa.field("up_limit", pa.float64(), nullable=True),
        pa.field("down_limit", pa.float64(), nullable=True),
        pa.field("explicitly_suspended", pa.bool_(), nullable=False),
        pa.field("inferred_suspended", pa.bool_(), nullable=False),
        pa.field("suspend_type", pa.string(), nullable=True),
        pa.field("suspend_timing", pa.string(), nullable=True),
        pa.field("is_st", pa.bool_(), nullable=False),
        pa.field("st_type", pa.string(), nullable=True),
        pa.field("st_type_name", pa.string(), nullable=True),
        pa.field("bar_available", pa.bool_(), nullable=False),
        pa.field("tradable", pa.bool_(), nullable=False),
        pa.field("is_observation", pa.bool_(), nullable=False),
    ]
    return pa.schema(fields)


class FullAPanelConfig(BaseModel):
    """Economic configuration of a full-A panel materialization."""

    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    schema_id: Literal["trademaster.full-a-panel-config/v1"] = "trademaster.full-a-panel-config/v1"
    observation_frequency: Literal["all_sessions", "month_end"] = "month_end"
    horizons: tuple[int, ...] = (1, 5, 20, 60, 120, 252)
    label_alignment: Literal["next_session_close"] = "next_session_close"
    price_adjustment: Literal["hfq_raw_times_adj_factor"] = "hfq_raw_times_adj_factor"
    require_tradable_entry: bool = True
    bse_first_session: date = _BSE_FIRST_SESSION

    @model_validator(mode="after")
    def validate_config(self) -> FullAPanelConfig:
        if (
            not self.horizons
            or self.horizons != tuple(sorted(set(self.horizons)))
            or any(value < 1 for value in self.horizons)
        ):
            raise ValueError("panel horizons must be positive and canonical")
        if self.bse_first_session < _BSE_FIRST_SESSION:
            raise ValueError("BSE effective session cannot precede 2021-11-15")
        return self

    @property
    def config_sha256(self) -> str:
        return _sha256_json(self.model_dump(mode="json"))


class PanelArtifactRef(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    name: Literal[
        "dense_panel",
        "factor_values",
        "forward_returns",
        "market",
        "source_objects",
        "universe",
    ]
    uri: str = Field(min_length=1)
    content_sha256: str = Field(pattern=_SHA256)
    schema_sha256: str = Field(pattern=_SHA256)
    row_count: int = Field(ge=0)

    @model_validator(mode="after")
    def validate_uri(self) -> PanelArtifactRef:
        path = PurePosixPath(self.uri)
        if path.is_absolute() or ".." in path.parts or path.suffix != ".parquet":
            raise ValueError("panel artifact URI must be a relative Parquet path")
        return self


class FullAPanelCoverage(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    session_count: int = Field(gt=0)
    instrument_count: int = Field(gt=0)
    inferred_lifecycle_instrument_count: int = Field(ge=0)
    active_panel_rows: int = Field(gt=0)
    bar_available_rows: int = Field(ge=0)
    missing_bar_rows: int = Field(ge=0)
    inferred_suspend_rows: int = Field(ge=0)
    unexplained_missing_bar_rows: int = Field(ge=0)
    bar_without_adj_factor_rows: int = Field(ge=0)
    bar_without_daily_basic_rows: int = Field(ge=0)
    adjusted_price_rows: int = Field(ge=0)
    daily_basic_rows: int = Field(ge=0)
    explicit_suspend_rows: int = Field(ge=0)
    observation_rows: int = Field(gt=0)
    factor_rows: int = Field(gt=0)
    valid_factor_rows: int = Field(ge=0)
    label_rows: int = Field(gt=0)
    valid_label_rows: int = Field(ge=0)

    @model_validator(mode="after")
    def validate_counts(self) -> FullAPanelCoverage:
        if self.bar_available_rows + self.missing_bar_rows != self.active_panel_rows:
            raise ValueError("panel bar coverage does not sum to active rows")
        if self.inferred_lifecycle_instrument_count > self.instrument_count:
            raise ValueError("inferred lifecycle count exceeds panel instruments")
        if (
            self.unexplained_missing_bar_rows > self.missing_bar_rows
            or self.inferred_suspend_rows > self.missing_bar_rows
            or self.bar_without_adj_factor_rows > self.bar_available_rows
            or self.bar_without_daily_basic_rows > self.bar_available_rows
        ):
            raise ValueError("panel gap count exceeds its applicable rows")
        if any(
            value > self.active_panel_rows
            for value in (
                self.adjusted_price_rows,
                self.daily_basic_rows,
                self.explicit_suspend_rows,
                self.observation_rows,
            )
        ):
            raise ValueError("panel coverage count exceeds active rows")
        if self.valid_factor_rows > self.factor_rows or self.valid_label_rows > self.label_rows:
            raise ValueError("valid derived rows exceed total rows")
        return self


class FactorResearchPanelManifest(BaseModel):
    """Immutable manifest binding source snapshot, config and derived Parquets."""

    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    schema_id: Literal[
        "trademaster.full-a-panel-manifest/v1",
        "trademaster.full-a-panel-manifest/v2",
    ]
    manifest_sha256: str = Field(pattern=_SHA256)
    materialization_sha256: str = Field(pattern=_SHA256)
    builder_version: Literal["full-a-panel/v1", "full-a-panel/v2"]
    builder_code_sha256: str = Field(pattern=_SHA256)
    plan_sha256: str = Field(pattern=_SHA256)
    source_snapshot_sha256: str = Field(pattern=_SHA256)
    source_object_count: int = Field(gt=0)
    config: FullAPanelConfig
    factor_identities: tuple[tuple[str, str], ...]
    factor_definition_bindings: tuple[tuple[str, str, str], ...]
    coverage: FullAPanelCoverage
    artifacts: tuple[PanelArtifactRef, ...]

    @model_validator(mode="after")
    def validate_identity(self) -> FactorResearchPanelManifest:
        expected_schema = (
            "trademaster.full-a-panel-manifest/v1"
            if self.builder_version == _LEGACY_BUILDER_VERSION
            else "trademaster.full-a-panel-manifest/v2"
        )
        if self.schema_id != expected_schema:
            raise ValueError("panel manifest schema and builder versions disagree")
        if self.factor_identities != _FACTOR_IDENTITIES:
            raise ValueError("panel factor identity set is not canonical")
        if tuple((item[0], item[1]) for item in self.factor_definition_bindings) != (
            self.factor_identities
        ) or any(
            len(item[2]) != 64 or any(ch not in "0123456789abcdef" for ch in item[2])
            for item in self.factor_definition_bindings
        ):
            raise ValueError("panel factor definition bindings are not canonical")
        if self.artifacts != tuple(sorted(self.artifacts, key=lambda item: item.name)):
            raise ValueError("panel artifacts must be canonically ordered")
        if len({item.name for item in self.artifacts}) != 6:
            raise ValueError("panel manifest artifact set is incomplete")
        artifacts = {item.name: item for item in self.artifacts}
        if (
            any(
                artifacts[name].row_count != self.coverage.active_panel_rows
                for name in ("dense_panel", "market")
            )
            or artifacts["universe"].row_count != self.coverage.observation_rows
        ):
            raise ValueError("dense panel artifact coverage differs")
        if artifacts["source_objects"].row_count != self.source_object_count:
            raise ValueError("source object artifact coverage differs")
        if artifacts["factor_values"].row_count != self.coverage.factor_rows or (
            self.coverage.factor_rows
            != self.coverage.observation_rows * len(self.factor_identities)
        ):
            raise ValueError("factor artifact coverage differs")
        if artifacts["forward_returns"].row_count != self.coverage.label_rows or (
            self.coverage.label_rows != self.coverage.observation_rows * len(self.config.horizons)
        ):
            raise ValueError("label artifact coverage differs")
        payload = self.model_dump(mode="json", exclude={"manifest_sha256"})
        if self.manifest_sha256 != _sha256_json(payload):
            raise ValueError("panel manifest content hash mismatch")
        return self

    @classmethod
    def build(
        cls,
        *,
        materialization_sha256: str,
        plan_sha256: str,
        source_snapshot_sha256: str,
        source_object_count: int,
        config: FullAPanelConfig,
        coverage: FullAPanelCoverage,
        artifacts: tuple[PanelArtifactRef, ...],
    ) -> FactorResearchPanelManifest:
        ordered = tuple(sorted(artifacts, key=lambda item: item.name))
        base: dict[str, object] = {
            "schema_id": "trademaster.full-a-panel-manifest/v2",
            "materialization_sha256": materialization_sha256,
            "builder_version": _BUILDER_VERSION,
            "builder_code_sha256": _builder_code_sha256(),
            "plan_sha256": plan_sha256,
            "source_snapshot_sha256": source_snapshot_sha256,
            "source_object_count": source_object_count,
            "config": config,
            "factor_identities": _FACTOR_IDENTITIES,
            "factor_definition_bindings": _factor_definition_bindings(),
            "coverage": coverage,
            "artifacts": ordered,
        }
        hash_payload = {
            **base,
            "config": config.model_dump(mode="json"),
            "coverage": coverage.model_dump(mode="json"),
            "artifacts": [item.model_dump(mode="json") for item in ordered],
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


class FactorResearchPanelBuilder:
    """Build deterministic full-A research artifacts without loading the panel in Python."""

    def __init__(self, *, store: FullAResearchStore, output_root: Path) -> None:
        self.store = store
        self.output_root = output_root.resolve()
        if self.output_root == Path(self.output_root.anchor):
            raise ValueError("panel output root cannot be the filesystem root")
        self.runs = self.output_root / "runs"
        self.manifests = self.output_root / "manifests"

    @staticmethod
    @contextmanager
    def query() -> Iterator[duckdb.DuckDBPyConnection]:
        connection = duckdb.connect()
        try:
            yield connection
        finally:
            connection.close()

    def _required_tasks(self, plan: DownloadPlanManifest) -> tuple[DownloadTask, ...]:
        tasks = tuple(item for item in plan.tasks if item.endpoint in _REQUIRED_ENDPOINTS)
        stock_statuses = {
            dict(item.params).get("list_status") for item in tasks if item.endpoint == "stock_basic"
        }
        if stock_statuses != {"L", "D", "P", "G"}:
            raise RuntimeError("panel requires stock_basic L/D/P/G evidence")
        expected_dates = {item.strftime("%Y%m%d") for item in plan.sessions}
        for endpoint in _SESSION_ENDPOINTS:
            dates = {
                dict(item.params).get("trade_date") for item in tasks if item.endpoint == endpoint
            }
            if dates != expected_dates:
                raise RuntimeError(f"panel source task coverage is incomplete for {endpoint}")
        return tuple(sorted(tasks, key=lambda item: item.task_key))

    def _source_objects(self, plan: DownloadPlanManifest) -> tuple[tuple[_SourceObject, ...], str]:
        tasks = self._required_tasks(plan)
        snapshot = self.store.completed_evidence_snapshot(
            plan,
            endpoints=tuple(sorted(_REQUIRED_ENDPOINTS)),
        )
        by_id = {item.task.task_id: item for item in snapshot.evidence}
        objects: list[_SourceObject] = []
        for task in tasks:
            evidence = by_id.get(task.task_id)
            if evidence is None:
                raise RuntimeError(f"panel source task is not completed: {task.task_key}")
            uri = PurePosixPath(evidence.evidence_uri)
            if uri.is_absolute() or ".." in uri.parts:
                raise RuntimeError("panel source evidence URI is invalid")
            path = (self.store.root / uri).resolve()
            try:
                path.relative_to(self.store.root)
            except ValueError as error:
                raise RuntimeError("panel source evidence path escapes its root") from error
            if not path.is_file():
                raise RuntimeError("panel source evidence Parquet is missing")
            if _file_sha256(path) != evidence.content_sha256:
                raise RuntimeError("panel source evidence content hash mismatch")
            try:
                actual_rows = pq.read_metadata(path).num_rows
            except Exception as error:
                raise RuntimeError("panel source evidence Parquet is unreadable") from error
            if actual_rows != evidence.row_count:
                raise RuntimeError("panel source evidence row count mismatch")
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
        return tuple(objects), snapshot.snapshot_sha256

    def _verify_artifacts(self, manifest: FactorResearchPanelManifest) -> None:
        for artifact in manifest.artifacts:
            path = self.artifact_path(manifest, artifact.name)
            if not path.is_file():
                raise RuntimeError("panel artifact is missing")
            if _file_sha256(path) != artifact.content_sha256:
                raise RuntimeError("panel artifact content hash mismatch")
            try:
                metadata = pq.read_metadata(path)
                schema_hash = _schema_sha256(path)
            except Exception as error:
                raise RuntimeError("panel artifact Parquet is unreadable") from error
            if metadata.num_rows != artifact.row_count or schema_hash != artifact.schema_sha256:
                raise RuntimeError("panel artifact metadata mismatch")

    @staticmethod
    def _write_immutable(path: Path, payload: bytes) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists():
            if path.read_bytes() != payload:
                raise RuntimeError("panel immutable object identity collision")
            return
        with tempfile.NamedTemporaryFile(
            dir=path.parent, prefix="manifest-", suffix=".tmp", delete=False
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
        manifest: FactorResearchPanelManifest,
        name: str,
    ) -> Path:
        try:
            artifact = next(item for item in manifest.artifacts if item.name == name)
        except StopIteration as error:
            raise KeyError(f"unknown panel artifact: {name}") from error
        uri = PurePosixPath(artifact.uri)
        path = (self.output_root / uri).resolve()
        try:
            path.relative_to(self.output_root)
        except ValueError as error:
            raise RuntimeError("panel artifact path escapes its root") from error
        return path

    def load_manifest(self, manifest_sha256: str) -> FactorResearchPanelManifest:
        if len(manifest_sha256) != 64 or any(
            ch not in "0123456789abcdef" for ch in manifest_sha256
        ):
            raise ValueError("panel manifest SHA-256 is invalid")
        path = self.manifests / f"{manifest_sha256}.json"
        try:
            payload = path.read_bytes()
        except OSError as error:
            raise RuntimeError("panel manifest is missing") from error
        try:
            manifest = FactorResearchPanelManifest.model_validate_json(payload, strict=True)
        except ValueError as error:
            raise RuntimeError("panel manifest is invalid") from error
        if manifest.manifest_sha256 != manifest_sha256:
            raise RuntimeError("panel manifest path and identity differ")
        self._verify_artifacts(manifest)
        return manifest

    def build(
        self,
        plan: DownloadPlanManifest,
        *,
        config: FullAPanelConfig | None = None,
    ) -> FactorResearchPanelManifest:
        resolved_config = config or FullAPanelConfig()
        source_objects, source_snapshot_sha256 = self._source_objects(plan)
        materialization_sha256 = _sha256_json(
            {
                "builder_version": _BUILDER_VERSION,
                "builder_code_sha256": _builder_code_sha256(),
                "plan_sha256": plan.plan_sha256,
                "source_snapshot_sha256": source_snapshot_sha256,
                "config": resolved_config.model_dump(mode="json"),
                "factor_identities": _FACTOR_IDENTITIES,
                "factor_definition_bindings": _factor_definition_bindings(),
            }
        )
        run_root = self.runs / materialization_sha256
        existing = run_root / "manifest.json"
        if existing.is_file():
            payload = existing.read_bytes()
            try:
                parsed = FactorResearchPanelManifest.model_validate_json(payload, strict=True)
            except ValueError as error:
                raise RuntimeError("panel run manifest is invalid") from error
            content_manifest = self.manifests / f"{parsed.manifest_sha256}.json"
            self._verify_artifacts(parsed)
            self._write_immutable(content_manifest, payload)
            manifest = self.load_manifest(parsed.manifest_sha256)
            if parsed != manifest:
                raise RuntimeError("panel run and content manifests differ")
            if (
                manifest.materialization_sha256 != materialization_sha256
                or manifest.plan_sha256 != plan.plan_sha256
                or manifest.source_snapshot_sha256 != source_snapshot_sha256
                or manifest.config != resolved_config
            ):
                raise RuntimeError("panel materialization identity collision")
            return manifest

        self.runs.mkdir(parents=True, exist_ok=True)
        self.manifests.mkdir(parents=True, exist_ok=True)
        temporary = Path(tempfile.mkdtemp(prefix="panel-", dir=self.runs))
        try:
            manifest = self._materialize(
                plan=plan,
                config=resolved_config,
                source_objects=source_objects,
                source_snapshot_sha256=source_snapshot_sha256,
                materialization_sha256=materialization_sha256,
                temporary=temporary,
            )
            manifest_payload = _canonical_json(manifest.model_dump(mode="json"))
            (temporary / "manifest.json").write_bytes(manifest_payload)
            try:
                temporary.replace(run_root)
            except FileExistsError:
                shutil.rmtree(temporary)
            manifest_path = self.manifests / f"{manifest.manifest_sha256}.json"
            self._write_immutable(manifest_path, manifest_payload)
            return self.load_manifest(manifest.manifest_sha256)
        finally:
            if temporary.exists():
                shutil.rmtree(temporary)

    def _materialize(
        self,
        *,
        plan: DownloadPlanManifest,
        config: FullAPanelConfig,
        source_objects: tuple[_SourceObject, ...],
        source_snapshot_sha256: str,
        materialization_sha256: str,
        temporary: Path,
    ) -> FactorResearchPanelManifest:
        by_endpoint = {
            endpoint: tuple(item.path for item in source_objects if item.endpoint == endpoint)
            for endpoint in _REQUIRED_ENDPOINTS
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

        session_dates = list(plan.sessions)
        month_last = {(value.year, value.month): value for value in session_dates}
        session_table = pa.table(
            {
                "session_date": pa.array(session_dates, type=pa.date32()),
                "event_time": pa.array(
                    [
                        datetime(value.year, value.month, value.day, 7, tzinfo=UTC)
                        for value in session_dates
                    ],
                    type=pa.timestamp("us", tz="UTC"),
                ),
                "session_index": pa.array(range(len(session_dates)), type=pa.int32()),
                "is_observation": pa.array(
                    [
                        config.observation_frequency == "all_sessions"
                        or month_last[(value.year, value.month)] == value
                        for value in session_dates
                    ],
                    type=pa.bool_(),
                ),
            }
        )
        instrument_table = pa.table({"instrument_id": list(plan.instruments)})
        horizon_table = pa.table({"horizon_sessions": pa.array(config.horizons, type=pa.int32())})

        spill_directory = temporary / ".duckdb-spill"
        spill_directory.mkdir()
        with self.query() as connection:
            connection.execute(f"SET temp_directory = {_quoted_path(spill_directory)}")
            connection.register("plan_sessions", session_table)
            connection.register("plan_instruments", instrument_table)
            connection.register("label_horizons", horizon_table)
            for endpoint, paths in by_endpoint.items():
                connection.execute(
                    f"CREATE TEMP VIEW raw_{endpoint} AS SELECT * FROM {_parquet_scan(paths)}"
                )
            self._validate_source_keys(connection, len(plan.instruments))
            self._create_panel_views(connection, config)
            self._copy_panel_artifacts(connection, temporary)
            self._create_factor_view(connection)
            self._copy_query(
                connection,
                "SELECT * FROM factor_values ORDER BY event_time, instrument_id, factor_id",
                temporary / "factor_values.parquet",
                schema=factor_output_schema(),
            )
            connection.execute(
                "CREATE OR REPLACE TEMP VIEW factor_values AS SELECT * FROM read_parquet("
                f"{_quoted_path(temporary / 'factor_values.parquet')})"
            )
            self._create_label_view(connection, config)
            self._copy_query(
                connection,
                """
                SELECT * FROM forward_returns
                ORDER BY event_time, instrument_id, horizon_sessions
                """,
                temporary / "forward_returns.parquet",
                schema=forward_return_v2_schema(),
            )
            connection.execute(
                "CREATE OR REPLACE TEMP VIEW forward_returns AS SELECT * FROM read_parquet("
                f"{_quoted_path(temporary / 'forward_returns.parquet')})"
            )
            coverage = self._coverage(connection, plan)
        try:
            spill_directory.rmdir()
        except OSError as error:
            raise RuntimeError(
                "DuckDB spill directory was not cleaned after panel build"
            ) from error

        run_prefix = PurePosixPath("runs") / materialization_sha256
        artifacts = tuple(
            self._artifact_ref(
                name=cast(
                    Literal[
                        "dense_panel",
                        "factor_values",
                        "forward_returns",
                        "market",
                        "source_objects",
                        "universe",
                    ],
                    name,
                ),
                path=temporary / filename,
                uri=(run_prefix / filename).as_posix(),
            )
            for name, filename in (
                ("dense_panel", "dense_panel.parquet"),
                ("factor_values", "factor_values.parquet"),
                ("forward_returns", "forward_returns.parquet"),
                ("market", "market.parquet"),
                ("source_objects", "source_objects.parquet"),
                ("universe", "universe.parquet"),
            )
        )
        return FactorResearchPanelManifest.build(
            materialization_sha256=materialization_sha256,
            plan_sha256=plan.plan_sha256,
            source_snapshot_sha256=source_snapshot_sha256,
            source_object_count=len(source_objects),
            config=config,
            coverage=coverage,
            artifacts=artifacts,
        )

    @staticmethod
    def _validate_source_keys(
        connection: duckdb.DuckDBPyConnection,
        expected_instruments: int,
    ) -> None:
        duplicates = connection.execute(
            """
            SELECT count(*) - count(DISTINCT ts_code) FROM raw_stock_basic
            WHERE ts_code IN (SELECT instrument_id FROM plan_instruments)
            """
        ).fetchone()
        if duplicates is None or int(duplicates[0]) != 0:
            raise RuntimeError("stock_basic contains duplicate planned instruments")
        covered_count = connection.execute(
            """
            SELECT count(DISTINCT instrument_id) FROM (
                SELECT CAST(ts_code AS VARCHAR) AS instrument_id
                FROM raw_stock_basic
                WHERE ts_code IN (SELECT instrument_id FROM plan_instruments)
                UNION ALL
                SELECT CAST(ts_code AS VARCHAR) AS instrument_id
                FROM raw_daily
                WHERE ts_code IN (SELECT instrument_id FROM plan_instruments)
            )
            """
        ).fetchone()
        if covered_count is None or int(covered_count[0]) != expected_instruments:
            raise RuntimeError(
                "stock_basic or market evidence does not cover every planned instrument"
            )
        for endpoint in ("daily", "adj_factor", "daily_basic", "stk_limit"):
            duplicate = connection.execute(
                f"""
                SELECT count(*) FROM (
                    SELECT ts_code, trade_date, count(*) AS observations
                    FROM raw_{endpoint}
                    GROUP BY ts_code, trade_date HAVING count(*) > 1
                )
                """
            ).fetchone()
            if duplicate is None or int(duplicate[0]) != 0:
                raise RuntimeError(f"{endpoint} contains duplicate instrument sessions")

    @staticmethod
    def _create_panel_views(
        connection: duckdb.DuckDBPyConnection,
        config: FullAPanelConfig,
    ) -> None:
        bse_date = config.bse_first_session.isoformat()
        connection.execute(
            f"""
            CREATE TEMP VIEW master_active_instruments AS
            SELECT
                CAST(s.ts_code AS VARCHAR) AS instrument_id,
                CAST(s.exchange AS VARCHAR) AS venue,
                CASE
                    WHEN CAST(s.exchange AS VARCHAR) = 'BSE'
                    THEN greatest(strptime(CAST(s.list_date AS VARCHAR), '%Y%m%d')::DATE,
                                  DATE '{bse_date}')
                    ELSE strptime(CAST(s.list_date AS VARCHAR), '%Y%m%d')::DATE
                END AS effective_list_date,
                CASE
                    WHEN s.delist_date IS NULL OR trim(CAST(s.delist_date AS VARCHAR)) = ''
                    THEN NULL
                    ELSE strptime(CAST(s.delist_date AS VARCHAR), '%Y%m%d')::DATE
                END AS delist_date
            FROM raw_stock_basic s
            JOIN plan_instruments p ON p.instrument_id = CAST(s.ts_code AS VARCHAR)
            """
        )
        connection.execute(
            f"""
            CREATE TEMP VIEW inferred_market_instruments AS
            SELECT
                CAST(d.ts_code AS VARCHAR) AS instrument_id,
                CASE
                    WHEN ends_with(CAST(d.ts_code AS VARCHAR), '.SH') THEN 'SSE'
                    WHEN ends_with(CAST(d.ts_code AS VARCHAR), '.SZ') THEN 'SZSE'
                    WHEN ends_with(CAST(d.ts_code AS VARCHAR), '.BJ') THEN 'BSE'
                    ELSE 'UNKNOWN'
                END AS venue,
                CASE
                    WHEN ends_with(CAST(d.ts_code AS VARCHAR), '.BJ')
                    THEN greatest(
                        min(strptime(CAST(d.trade_date AS VARCHAR), '%Y%m%d')::DATE),
                        DATE '{bse_date}'
                    )
                    ELSE min(strptime(CAST(d.trade_date AS VARCHAR), '%Y%m%d')::DATE)
                END AS effective_list_date,
                max(strptime(CAST(d.trade_date AS VARCHAR), '%Y%m%d')::DATE) AS delist_date
            FROM raw_daily d
            JOIN plan_instruments p
              ON p.instrument_id = CAST(d.ts_code AS VARCHAR)
            LEFT JOIN raw_stock_basic s
              ON CAST(s.ts_code AS VARCHAR) = CAST(d.ts_code AS VARCHAR)
            WHERE s.ts_code IS NULL
            GROUP BY CAST(d.ts_code AS VARCHAR)
            """
        )
        connection.execute(
            """
            CREATE TEMP VIEW active_instruments AS
            SELECT * FROM master_active_instruments
            UNION ALL
            SELECT * FROM inferred_market_instruments
            """
        )
        connection.execute(
            """
            CREATE TEMP VIEW active_grid AS
            SELECT i.instrument_id, i.venue, s.session_date, s.event_time,
                   s.session_index, s.is_observation
            FROM active_instruments i
            CROSS JOIN plan_sessions s
            WHERE s.session_date >= i.effective_list_date
              AND (i.delist_date IS NULL OR s.session_date <= i.delist_date)
            """
        )
        connection.execute(
            """
            CREATE TEMP VIEW suspension_status AS
            SELECT CAST(ts_code AS VARCHAR) AS instrument_id,
                   strptime(CAST(trade_date AS VARCHAR), '%Y%m%d')::DATE AS session_date,
                   true AS explicitly_suspended,
                   max(CAST(suspend_type AS VARCHAR)) AS suspend_type,
                   max(CAST(suspend_timing AS VARCHAR)) AS suspend_timing
            FROM raw_suspend_d
            GROUP BY instrument_id, session_date
            """
        )
        connection.execute(
            """
            CREATE TEMP VIEW st_status AS
            SELECT CAST(ts_code AS VARCHAR) AS instrument_id,
                   strptime(CAST(trade_date AS VARCHAR), '%Y%m%d')::DATE AS session_date,
                   true AS is_st,
                   max(CAST(type AS VARCHAR)) AS st_type,
                   max(CAST(type_name AS VARCHAR)) AS st_type_name
            FROM raw_stock_st
            GROUP BY instrument_id, session_date
            """
        )
        connection.execute(
            """
            CREATE TEMP VIEW dense_panel AS
            SELECT
                g.instrument_id,
                g.venue,
                g.session_date,
                g.event_time,
                g.session_index,
                CASE WHEN isfinite(try_cast(d.open AS DOUBLE))
                     THEN try_cast(d.open AS DOUBLE) ELSE NULL END AS open,
                CASE WHEN isfinite(try_cast(d.high AS DOUBLE))
                     THEN try_cast(d.high AS DOUBLE) ELSE NULL END AS high,
                CASE WHEN isfinite(try_cast(d.low AS DOUBLE))
                     THEN try_cast(d.low AS DOUBLE) ELSE NULL END AS low,
                CASE WHEN isfinite(try_cast(d.close AS DOUBLE))
                     THEN try_cast(d.close AS DOUBLE) ELSE NULL END AS close,
                CASE WHEN isfinite(try_cast(d.pre_close AS DOUBLE))
                     THEN try_cast(d.pre_close AS DOUBLE) ELSE NULL END AS pre_close,
                CASE WHEN isfinite(try_cast(d.vol AS DOUBLE))
                     THEN try_cast(d.vol AS DOUBLE) ELSE NULL END AS volume_lots,
                CASE WHEN isfinite(try_cast(d.amount AS DOUBLE))
                     THEN try_cast(d.amount AS DOUBLE) ELSE NULL END AS amount_thousand_cny,
                CASE WHEN isfinite(try_cast(a.adj_factor AS DOUBLE))
                     THEN try_cast(a.adj_factor AS DOUBLE) ELSE NULL END AS adj_factor,
                try_cast(b.turnover_rate AS DOUBLE) AS turnover_rate,
                try_cast(b.total_mv AS DOUBLE) AS total_market_value_10k_cny,
                try_cast(b.circ_mv AS DOUBLE) AS circulating_market_value_10k_cny,
                try_cast(b.pe_ttm AS DOUBLE) AS pe_ttm,
                try_cast(b.pb AS DOUBLE) AS pb,
                try_cast(b.ps_ttm AS DOUBLE) AS ps_ttm,
                try_cast(b.dv_ttm AS DOUBLE) AS dividend_yield_ttm,
                try_cast(l.up_limit AS DOUBLE) AS up_limit,
                try_cast(l.down_limit AS DOUBLE) AS down_limit,
                coalesce(s.explicitly_suspended, false) AS explicitly_suspended,
                coalesce(
                        d.ts_code IS NULL
                        AND NOT coalesce(s.explicitly_suspended, false)
                        AND isfinite(try_cast(a.adj_factor AS DOUBLE))
                        AND try_cast(a.adj_factor AS DOUBLE) > 0,
                    false
                ) AS inferred_suspended,
                s.suspend_type,
                s.suspend_timing,
                coalesce(st.is_st, false) AS is_st,
                st.st_type,
                st.st_type_name,
                d.ts_code IS NOT NULL
                    AND isfinite(try_cast(d.close AS DOUBLE))
                    AND try_cast(d.close AS DOUBLE) > 0 AS bar_available,
                d.ts_code IS NOT NULL
                    AND isfinite(try_cast(d.close AS DOUBLE))
                    AND try_cast(d.close AS DOUBLE) > 0
                    AND NOT coalesce(s.explicitly_suspended, false) AS tradable,
                g.is_observation
            FROM active_grid g
            LEFT JOIN raw_daily d
              ON CAST(d.ts_code AS VARCHAR) = g.instrument_id
             AND strptime(CAST(d.trade_date AS VARCHAR), '%Y%m%d')::DATE = g.session_date
            LEFT JOIN raw_adj_factor a
              ON CAST(a.ts_code AS VARCHAR) = g.instrument_id
             AND strptime(CAST(a.trade_date AS VARCHAR), '%Y%m%d')::DATE = g.session_date
            LEFT JOIN raw_daily_basic b
              ON CAST(b.ts_code AS VARCHAR) = g.instrument_id
             AND strptime(CAST(b.trade_date AS VARCHAR), '%Y%m%d')::DATE = g.session_date
            LEFT JOIN raw_stk_limit l
              ON CAST(l.ts_code AS VARCHAR) = g.instrument_id
             AND strptime(CAST(l.trade_date AS VARCHAR), '%Y%m%d')::DATE = g.session_date
            LEFT JOIN suspension_status s
              ON s.instrument_id = g.instrument_id AND s.session_date = g.session_date
            LEFT JOIN st_status st
              ON st.instrument_id = g.instrument_id AND st.session_date = g.session_date
            """
        )

    @staticmethod
    def _copy_query(
        connection: duckdb.DuckDBPyConnection,
        query: str,
        path: Path,
        *,
        schema: pa.Schema | None = None,
    ) -> None:
        if schema is not None:
            reader = connection.execute(query).to_arrow_reader(122_880)
            with pq.ParquetWriter(path, schema, compression="zstd") as writer:
                for batch in reader:
                    table = pa.Table.from_batches([batch]).cast(schema)
                    writer.write_table(table)
            return
        connection.execute(
            f"COPY ({query}) TO {_quoted_path(path)} "
            "(FORMAT PARQUET, COMPRESSION ZSTD, ROW_GROUP_SIZE 122880)"
        )

    def _copy_panel_artifacts(
        self,
        connection: duckdb.DuckDBPyConnection,
        temporary: Path,
    ) -> None:
        self._copy_query(
            connection,
            "SELECT * FROM dense_panel ORDER BY event_time, instrument_id",
            temporary / "dense_panel.parquet",
            schema=full_a_dense_panel_schema(),
        )
        connection.execute(
            "CREATE OR REPLACE TEMP VIEW dense_panel AS SELECT * FROM read_parquet("
            f"{_quoted_path(temporary / 'dense_panel.parquet')})"
        )
        self._copy_query(
            connection,
            """
            SELECT instrument_id, event_time, close, adj_factor,
                   amount_thousand_cny * 1000.0 AS amount, bar_available, tradable
            FROM dense_panel ORDER BY event_time, instrument_id
            """,
            temporary / "market.parquet",
            schema=full_a_market_schema(),
        )
        self._copy_query(
            connection,
            """
            SELECT instrument_id, event_time, true AS eligible,
                   CAST(NULL AS VARCHAR) AS exclusion_reason,
                   CAST(NULL AS VARCHAR) AS industry_id,
                   total_market_value_10k_cny AS total_market_value
            FROM dense_panel
            WHERE is_observation
            ORDER BY event_time, instrument_id
            """,
            temporary / "universe.parquet",
            schema=full_a_universe_schema(),
        )

    @staticmethod
    def _create_factor_view(connection: duckdb.DuckDBPyConnection) -> None:
        connection.execute(
            """
            CREATE TEMP VIEW adjusted_prices AS
            SELECT *,
                CASE WHEN close > 0 AND adj_factor > 0
                               AND isfinite(close) AND isfinite(adj_factor)
                               AND isfinite(close * adj_factor)
                     THEN close * adj_factor ELSE NULL END AS adjusted_close,
                CASE WHEN open > 0 AND adj_factor > 0
                               AND isfinite(open) AND isfinite(adj_factor)
                               AND isfinite(open * adj_factor)
                     THEN open * adj_factor ELSE NULL END AS adjusted_open
            FROM dense_panel
            """
        )
        connection.execute(
            """
            CREATE TEMP VIEW price_windows AS
            SELECT *,
                lag(adjusted_close, 1) OVER w AS lag_1,
                lag(adjusted_close, 5) OVER w AS lag_5,
                lag(adjusted_close, 6) OVER w AS lag_6,
                lag(adjusted_close, 20) OVER w AS lag_20,
                avg(adjusted_close) OVER w3 AS mean_3,
                count(adjusted_close) OVER w3 AS count_3,
                avg(adjusted_close) OVER w6 AS mean_6,
                count(adjusted_close) OVER w6 AS count_6,
                avg(adjusted_close) OVER w12 AS mean_12,
                count(adjusted_close) OVER w12 AS count_12,
                avg(adjusted_close) OVER w24 AS mean_24,
                count(adjusted_close) OVER w24 AS count_24
            FROM adjusted_prices
            WINDOW
                w AS (PARTITION BY instrument_id ORDER BY session_index),
                w3 AS (PARTITION BY instrument_id ORDER BY session_index
                        ROWS BETWEEN 2 PRECEDING AND CURRENT ROW),
                w6 AS (PARTITION BY instrument_id ORDER BY session_index
                        ROWS BETWEEN 5 PRECEDING AND CURRENT ROW),
                w12 AS (PARTITION BY instrument_id ORDER BY session_index
                         ROWS BETWEEN 11 PRECEDING AND CURRENT ROW),
                w24 AS (PARTITION BY instrument_id ORDER BY session_index
                         ROWS BETWEEN 23 PRECEDING AND CURRENT ROW)
            """
        )
        connection.execute(
            """
            CREATE TEMP VIEW up_indicators AS
            SELECT *,
                CASE WHEN adjusted_close > 0 AND lag_1 > 0
                     THEN CAST(adjusted_close > lag_1 AS INTEGER) ELSE NULL END AS is_up
            FROM price_windows
            """
        )
        connection.execute(
            """
            CREATE TEMP VIEW reviewed_inputs AS
            SELECT *,
                sum(is_up) OVER w12 AS up_12,
                count(is_up) OVER w12 AS up_count_12,
                sum(is_up) OVER w20 AS up_20,
                count(is_up) OVER w20 AS up_count_20
            FROM up_indicators
            WINDOW
                w12 AS (PARTITION BY instrument_id ORDER BY session_index
                         ROWS BETWEEN 11 PRECEDING AND CURRENT ROW),
                w20 AS (PARTITION BY instrument_id ORDER BY session_index
                         ROWS BETWEEN 19 PRECEDING AND CURRENT ROW)
            """
        )

        expressions = (
            (
                "gtja191.alpha014",
                "lag_5 > 0 AND adjusted_close > 0",
                "adjusted_close - lag_5",
            ),
            (
                "gtja191.alpha015",
                "lag_1 > 0 AND adjusted_open > 0",
                "adjusted_open / lag_1 - 1.0",
            ),
            (
                "gtja191.alpha018",
                "lag_5 > 0 AND adjusted_close > 0",
                "adjusted_close / lag_5",
            ),
            (
                "gtja191.alpha020",
                "lag_6 > 0 AND adjusted_close > 0",
                "100.0 * (adjusted_close - lag_6) / lag_6",
            ),
            (
                "gtja191.alpha031",
                "count_12 = 12 AND mean_12 > 0 AND adjusted_close > 0",
                "100.0 * (adjusted_close - mean_12) / mean_12",
            ),
            (
                "gtja191.alpha034",
                "count_12 = 12 AND mean_12 > 0 AND adjusted_close > 0",
                "mean_12 / adjusted_close",
            ),
            (
                "gtja191.alpha046",
                (
                    "count_3 = 3 AND count_6 = 6 AND count_12 = 12 "
                    "AND count_24 = 24 AND adjusted_close > 0"
                ),
                "(mean_3 + mean_6 + mean_12 + mean_24) / (4.0 * adjusted_close)",
            ),
            (
                "gtja191.alpha053",
                "up_count_12 = 12",
                "100.0 * up_12 / 12.0",
            ),
            (
                "gtja191.alpha058",
                "up_count_20 = 20",
                "100.0 * up_20 / 20.0",
            ),
            (
                "gtja191.alpha088",
                "lag_20 > 0 AND adjusted_close > 0",
                "100.0 * (adjusted_close - lag_20) / lag_20",
            ),
            (
                "huatai53.size.log_total_market_value",
                ("total_market_value_10k_cny > 0 AND isfinite(total_market_value_10k_cny)"),
                "ln(total_market_value_10k_cny * 10000.0)",
            ),
        )
        queries = []
        for factor_id, valid, value in expressions:
            queries.append(
                f"""
                SELECT instrument_id, event_time,
                       '{factor_id}' AS factor_id,
                       '1' AS factor_version,
                       CAST(
                           CASE WHEN candidate_valid AND isfinite(candidate_value)
                                THEN candidate_value ELSE 0.0 END AS DOUBLE
                       ) AS value,
                       CAST(
                           coalesce(candidate_valid AND isfinite(candidate_value), false)
                           AS BOOLEAN
                       ) AS is_valid
                FROM (
                    SELECT instrument_id, event_time,
                           CAST(coalesce(({valid}), false) AS BOOLEAN) AS candidate_valid,
                           CAST(
                               CASE WHEN {valid} THEN {value} ELSE NULL END AS DOUBLE
                           ) AS candidate_value
                    FROM reviewed_inputs WHERE is_observation
                )
                """
            )
        connection.execute("CREATE TEMP VIEW factor_values AS " + " UNION ALL ".join(queries))

    @staticmethod
    def _create_label_view(
        connection: duckdb.DuckDBPyConnection,
        config: FullAPanelConfig,
    ) -> None:
        require_tradable = "true" if config.require_tradable_entry else "false"
        connection.execute(
            """
            CREATE TEMP VIEW label_inputs AS
            SELECT current.instrument_id, current.event_time, h.horizon_sessions,
                   entry_session.event_time AS entry_time,
                   exit_session.event_time AS exit_time,
                   CASE WHEN isfinite(entry.amount_thousand_cny * 1000.0)
                        THEN entry.amount_thousand_cny * 1000.0 ELSE NULL END
                        AS entry_amount,
                   coalesce(entry.tradable, false) AS entry_tradable,
                   CASE WHEN entry.close > 0 AND entry.adj_factor > 0
                                  AND isfinite(entry.close)
                                  AND isfinite(entry.adj_factor)
                                  AND isfinite(entry.close * entry.adj_factor)
                        THEN entry.close * entry.adj_factor ELSE NULL END AS entry_price,
                   CASE WHEN exit_row.close > 0 AND exit_row.adj_factor > 0
                                  AND isfinite(exit_row.close)
                                  AND isfinite(exit_row.adj_factor)
                                  AND isfinite(exit_row.close * exit_row.adj_factor)
                        THEN exit_row.close * exit_row.adj_factor ELSE NULL END AS exit_price,
                   entry.instrument_id IS NOT NULL AS entry_active,
                   exit_row.instrument_id IS NOT NULL AS exit_active
            FROM dense_panel current
            CROSS JOIN label_horizons h
            LEFT JOIN plan_sessions entry_session
              ON entry_session.session_index = current.session_index + 1
            LEFT JOIN plan_sessions exit_session
              ON exit_session.session_index = current.session_index + 1 + h.horizon_sessions
            LEFT JOIN dense_panel entry
              ON entry.instrument_id = current.instrument_id
             AND entry.session_index = current.session_index + 1
            LEFT JOIN dense_panel exit_row
              ON exit_row.instrument_id = current.instrument_id
             AND exit_row.session_index = current.session_index + 1 + h.horizon_sessions
            WHERE current.is_observation
            """
        )
        connection.execute(
            """
            CREATE TEMP VIEW label_returns AS
            SELECT *,
                   CASE WHEN entry_price IS NOT NULL AND exit_price IS NOT NULL
                        THEN exit_price / entry_price - 1.0 ELSE NULL END
                        AS candidate_return
            FROM label_inputs
            """
        )
        connection.execute(
            f"""
            CREATE TEMP VIEW forward_returns AS
            SELECT instrument_id, event_time, entry_time, exit_time, horizon_sessions,
                   CAST(CASE
                       WHEN exit_time IS NULL OR NOT entry_active OR NOT exit_active
                            OR ({require_tradable} AND NOT entry_tradable)
                            OR entry_price IS NULL OR exit_price IS NULL
                            OR candidate_return IS NULL OR NOT isfinite(candidate_return)
                       THEN 0.0
                       ELSE candidate_return
                   END AS DOUBLE) AS forward_return,
                   CAST(NOT (
                       exit_time IS NULL OR NOT entry_active OR NOT exit_active
                       OR ({require_tradable} AND NOT entry_tradable)
                       OR entry_price IS NULL OR exit_price IS NULL
                       OR candidate_return IS NULL OR NOT isfinite(candidate_return)
                   ) AS BOOLEAN) AS is_valid,
                   CASE
                       WHEN exit_time IS NULL THEN 'insufficient_future_sessions'
                       WHEN NOT entry_active THEN 'entry_not_active'
                       WHEN NOT exit_active THEN 'exit_not_active'
                       WHEN {require_tradable} AND NOT entry_tradable THEN 'entry_not_tradable'
                       WHEN entry_price IS NULL THEN 'entry_price_unavailable'
                       WHEN exit_price IS NULL THEN 'exit_price_unavailable'
                       WHEN candidate_return IS NULL OR NOT isfinite(candidate_return)
                            THEN 'forward_return_non_finite'
                       ELSE NULL
                   END AS invalid_reason,
                   entry_amount,
                   entry_tradable
            FROM label_returns
            """
        )

    @staticmethod
    def _coverage(
        connection: duckdb.DuckDBPyConnection,
        plan: DownloadPlanManifest,
    ) -> FullAPanelCoverage:
        row = connection.execute(
            """
            SELECT
                count(*) AS active_panel_rows,
                count(DISTINCT instrument_id) AS instrument_count,
                count(*) FILTER (WHERE bar_available) AS bar_available_rows,
                count(*) FILTER (WHERE NOT bar_available) AS missing_bar_rows,
                count(*) FILTER (
                    WHERE NOT bar_available AND NOT explicitly_suspended
                      AND NOT inferred_suspended
                ) AS unexplained_missing_bar_rows,
                count(*) FILTER (WHERE inferred_suspended) AS inferred_suspend_rows,
                count(*) FILTER (
                    WHERE bar_available
                      AND (adj_factor IS NULL OR NOT isfinite(adj_factor) OR adj_factor <= 0)
                ) AS bar_without_adj_factor_rows,
                count(*) FILTER (
                    WHERE bar_available AND total_market_value_10k_cny IS NULL
                ) AS bar_without_daily_basic_rows,
                count(*) FILTER (
                    WHERE close > 0 AND adj_factor > 0
                      AND isfinite(close) AND isfinite(adj_factor)
                      AND isfinite(close * adj_factor)
                ) AS adjusted_price_rows,
                count(*) FILTER (WHERE total_market_value_10k_cny IS NOT NULL)
                    AS daily_basic_rows,
                count(*) FILTER (WHERE explicitly_suspended) AS explicit_suspend_rows,
                count(*) FILTER (WHERE is_observation) AS observation_rows,
                (SELECT count(*) FROM inferred_market_instruments)
                    AS inferred_lifecycle_instrument_count
            FROM dense_panel
            """
        ).fetchone()
        factor = connection.execute(
            "SELECT count(*), count(*) FILTER (WHERE is_valid) FROM factor_values"
        ).fetchone()
        label = connection.execute(
            "SELECT count(*), count(*) FILTER (WHERE is_valid) FROM forward_returns"
        ).fetchone()
        if row is None or factor is None or label is None:
            raise RuntimeError("panel coverage queries returned no result")
        return FullAPanelCoverage(
            session_count=len(plan.sessions),
            instrument_count=int(row[1]),
            inferred_lifecycle_instrument_count=int(row[12]),
            active_panel_rows=int(row[0]),
            bar_available_rows=int(row[2]),
            missing_bar_rows=int(row[3]),
            unexplained_missing_bar_rows=int(row[4]),
            inferred_suspend_rows=int(row[5]),
            bar_without_adj_factor_rows=int(row[6]),
            bar_without_daily_basic_rows=int(row[7]),
            adjusted_price_rows=int(row[8]),
            daily_basic_rows=int(row[9]),
            explicit_suspend_rows=int(row[10]),
            observation_rows=int(row[11]),
            factor_rows=int(factor[0]),
            valid_factor_rows=int(factor[1]),
            label_rows=int(label[0]),
            valid_label_rows=int(label[1]),
        )

    @staticmethod
    def _artifact_ref(
        *,
        name: Literal[
            "dense_panel",
            "factor_values",
            "forward_returns",
            "market",
            "source_objects",
            "universe",
        ],
        path: Path,
        uri: str,
    ) -> PanelArtifactRef:
        return PanelArtifactRef(
            name=name,
            uri=uri,
            content_sha256=_file_sha256(path),
            schema_sha256=_schema_sha256(path),
            row_count=pq.read_metadata(path).num_rows,
        )


__all__ = [
    "FactorResearchPanelBuilder",
    "FactorResearchPanelManifest",
    "FullAPanelConfig",
    "FullAPanelCoverage",
    "PanelArtifactRef",
    "full_a_dense_panel_schema",
]
