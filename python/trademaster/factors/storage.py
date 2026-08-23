"""Parquet-authoritative factor materialization with a DuckDB discovery catalog."""

from __future__ import annotations

import hashlib
import json
import tempfile
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path, PurePosixPath
from typing import Literal, Self

import duckdb
import pyarrow as pa
import pyarrow.parquet as pq
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from trademaster.contracts import FactorContext, SnapshotManifest, _require_utc

from .management import FactorDefinition, FactorDependency, ManagedFactorRegistry


class FactorIntegrityError(RuntimeError):
    """A persisted factor definition, manifest, or Parquet object is inconsistent."""


def _canonical_json(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode()


def _sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _schema_sha256(table: pa.Table) -> str:
    return _sha256(table.schema.remove_metadata().to_string().encode())


def _table_sha256(table: pa.Table) -> str:
    sink = pa.BufferOutputStream()
    with pa.ipc.new_stream(sink, table.schema) as writer:
        writer.write_table(table.combine_chunks())
    return _sha256(sink.getvalue().to_pybytes())


def _snapshot_request_sha256(snapshot: SnapshotManifest) -> str:
    return _sha256(_canonical_json(snapshot.request.model_dump(mode="json")))


def _snapshot_objects_sha256(snapshot: SnapshotManifest) -> str:
    return _sha256(_canonical_json([item.sha256 for item in snapshot.objects]))


class FactorScope(BaseModel):
    """Exact event and universe scope requested for one factor materialization."""

    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    start: datetime
    end: datetime
    as_of: datetime
    instruments: tuple[str, ...]
    coverage_keys: tuple[str, ...]

    _utc_times = field_validator("start", "end", "as_of")(_require_utc)

    @model_validator(mode="after")
    def validate_scope(self) -> FactorScope:
        if self.end < self.start or self.as_of < self.end:
            raise ValueError("factor scope event/cutoff range is invalid")
        if (
            not self.instruments
            or self.instruments != tuple(sorted(set(self.instruments)))
            or self.coverage_keys != tuple(sorted(set(self.coverage_keys)))
        ):
            raise ValueError("factor scope universe and coverage keys must be canonical")
        return self

    @property
    def instrument_set_sha256(self) -> str:
        return _sha256(_canonical_json(self.instruments))


class ParentMaterializationRef(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    factor_id: str = Field(min_length=1)
    factor_version: str = Field(min_length=1)
    definition_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    materialization_id: str = Field(pattern=r"^[0-9a-f]{64}$")
    output_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @property
    def identity(self) -> tuple[str, str]:
        return (self.factor_id, self.factor_version)


class FactorMaterialization(BaseModel):
    """Self-validating lineage manifest for one immutable factor Parquet object."""

    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    schema_id: Literal["trademaster.factor-materialization/v1"]
    materialization_id: str = Field(pattern=r"^[0-9a-f]{64}$")
    request_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    factor_id: str = Field(min_length=1)
    factor_version: str = Field(min_length=1)
    definition_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    code_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    parameters_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    input_snapshot_id: str = Field(pattern=r"^[0-9a-f]{64}$")
    input_snapshot_request_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    input_snapshot_objects_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    input_table_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    scope: FactorScope
    parents: tuple[ParentMaterializationRef, ...]
    output_uri: str = Field(min_length=1)
    output_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    output_schema_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    output_row_count: int = Field(ge=0)
    output_event_time_start: datetime
    output_event_time_end: datetime
    created_at: datetime

    _utc_times = field_validator("output_event_time_start", "output_event_time_end", "created_at")(
        _require_utc
    )

    @model_validator(mode="after")
    def validate_manifest(self) -> FactorMaterialization:
        parent_identities = tuple(item.identity for item in self.parents)
        if parent_identities != tuple(sorted(set(parent_identities))):
            raise ValueError("factor parent materializations must be unique and sorted")
        uri = PurePosixPath(self.output_uri)
        if uri.is_absolute() or ".." in uri.parts or not uri.parts or uri.parts[0] != "values":
            raise ValueError("factor output URI must be contained under values")
        if self.output_event_time_end < self.output_event_time_start:
            raise ValueError("factor output event range is reversed")
        if self.request_sha256 != _sha256(_canonical_json(self._request_payload())):
            raise ValueError("factor materialization request hash mismatch")
        if self.materialization_id != _sha256(_canonical_json(self._identity_payload())):
            raise ValueError("factor materialization identity mismatch")
        return self

    def _request_payload(self) -> dict[str, object]:
        return {
            "definition_sha256": self.definition_sha256,
            "input_snapshot_id": self.input_snapshot_id,
            "input_snapshot_request_sha256": self.input_snapshot_request_sha256,
            "input_snapshot_objects_sha256": self.input_snapshot_objects_sha256,
            "input_table_sha256": self.input_table_sha256,
            "scope": self.scope.model_dump(mode="json"),
            "parents": [item.model_dump(mode="json") for item in self.parents],
        }

    def _identity_payload(self) -> dict[str, object]:
        return {
            "request_sha256": self.request_sha256,
            "output_sha256": self.output_sha256,
            "output_schema_sha256": self.output_schema_sha256,
            "output_row_count": self.output_row_count,
            "output_event_time_start": self.output_event_time_start.isoformat(),
            "output_event_time_end": self.output_event_time_end.isoformat(),
        }

    @classmethod
    def build(
        cls,
        *,
        factor_id: str,
        factor_version: str,
        definition_sha256: str,
        code_sha256: str,
        parameters_sha256: str,
        snapshot: SnapshotManifest,
        input_table_sha256: str,
        scope: FactorScope,
        parents: tuple[ParentMaterializationRef, ...],
        output_uri: str,
        output_sha256: str,
        output_schema_sha256: str,
        output_row_count: int,
        output_event_time_start: datetime,
        output_event_time_end: datetime,
        created_at: datetime,
    ) -> FactorMaterialization:
        request_payload = {
            "definition_sha256": definition_sha256,
            "input_snapshot_id": snapshot.snapshot_id,
            "input_snapshot_request_sha256": _snapshot_request_sha256(snapshot),
            "input_snapshot_objects_sha256": _snapshot_objects_sha256(snapshot),
            "input_table_sha256": input_table_sha256,
            "scope": scope.model_dump(mode="json"),
            "parents": [item.model_dump(mode="json") for item in parents],
        }
        request_sha256 = _sha256(_canonical_json(request_payload))
        identity_payload = {
            "request_sha256": request_sha256,
            "output_sha256": output_sha256,
            "output_schema_sha256": output_schema_sha256,
            "output_row_count": output_row_count,
            "output_event_time_start": output_event_time_start.isoformat(),
            "output_event_time_end": output_event_time_end.isoformat(),
        }
        return cls(
            schema_id="trademaster.factor-materialization/v1",
            materialization_id=_sha256(_canonical_json(identity_payload)),
            request_sha256=request_sha256,
            factor_id=factor_id,
            factor_version=factor_version,
            definition_sha256=definition_sha256,
            code_sha256=code_sha256,
            parameters_sha256=parameters_sha256,
            input_snapshot_id=snapshot.snapshot_id,
            input_snapshot_request_sha256=_snapshot_request_sha256(snapshot),
            input_snapshot_objects_sha256=_snapshot_objects_sha256(snapshot),
            input_table_sha256=input_table_sha256,
            scope=scope,
            parents=parents,
            output_uri=output_uri,
            output_sha256=output_sha256,
            output_schema_sha256=output_schema_sha256,
            output_row_count=output_row_count,
            output_event_time_start=output_event_time_start,
            output_event_time_end=output_event_time_end,
            created_at=created_at,
        )


@dataclass(frozen=True, slots=True)
class FactorArtifact:
    definition: FactorDefinition
    manifest: FactorMaterialization
    table: pa.Table
    path: Path
    from_cache: bool


class FactorCatalog:
    """DuckDB discovery index; definitions, manifests, and values remain files."""

    def __init__(self, path: Path) -> None:
        self.path = path.resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._connection = duckdb.connect(str(self.path))
        self._connection.execute(
            """
            CREATE TABLE IF NOT EXISTS factor_definitions (
                factor_id VARCHAR NOT NULL,
                factor_version VARCHAR NOT NULL,
                definition_sha256 VARCHAR NOT NULL,
                manifest_uri VARCHAR NOT NULL,
                manifest_sha256 VARCHAR NOT NULL,
                registered_at TIMESTAMPTZ NOT NULL,
                PRIMARY KEY (factor_id, factor_version),
                UNIQUE (definition_sha256)
            )
            """
        )
        self._connection.execute(
            """
            CREATE TABLE IF NOT EXISTS factor_evaluations (
                evaluation_id VARCHAR PRIMARY KEY,
                factor_materialization_id VARCHAR NOT NULL,
                manifest_uri VARCHAR NOT NULL UNIQUE,
                manifest_sha256 VARCHAR NOT NULL,
                created_at TIMESTAMPTZ NOT NULL
            )
            """
        )
        self._connection.execute(
            """
            CREATE TABLE IF NOT EXISTS factor_materializations (
                request_sha256 VARCHAR PRIMARY KEY,
                materialization_id VARCHAR NOT NULL UNIQUE,
                factor_id VARCHAR NOT NULL,
                factor_version VARCHAR NOT NULL,
                definition_sha256 VARCHAR NOT NULL,
                manifest_uri VARCHAR NOT NULL UNIQUE,
                manifest_sha256 VARCHAR NOT NULL,
                output_uri VARCHAR NOT NULL UNIQUE,
                output_sha256 VARCHAR NOT NULL UNIQUE,
                output_row_count BIGINT NOT NULL,
                created_at TIMESTAMPTZ NOT NULL
            )
            """
        )

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def close(self) -> None:
        self._connection.close()

    def definition_count(self) -> int:
        row = self._connection.execute("SELECT count(*) FROM factor_definitions").fetchone()
        if row is None:
            raise FactorIntegrityError("factor definition catalog count is unavailable")
        return int(row[0])

    def materialization_count(self) -> int:
        row = self._connection.execute("SELECT count(*) FROM factor_materializations").fetchone()
        if row is None:
            raise FactorIntegrityError("factor materialization catalog count is unavailable")
        return int(row[0])

    def evaluation_count(self) -> int:
        row = self._connection.execute("SELECT count(*) FROM factor_evaluations").fetchone()
        if row is None:
            raise FactorIntegrityError("factor evaluation catalog count is unavailable")
        return int(row[0])

    def evaluation_by_id(self, evaluation_id: str) -> tuple[str, str] | None:
        row = self._connection.execute(
            """
            SELECT manifest_uri, manifest_sha256
            FROM factor_evaluations WHERE evaluation_id = ?
            """,
            [evaluation_id],
        ).fetchone()
        return None if row is None else (str(row[0]), str(row[1]))

    def register_evaluation(
        self,
        *,
        evaluation_id: str,
        factor_materialization_id: str,
        manifest_uri: str,
        manifest_sha256: str,
        created_at: datetime,
    ) -> None:
        existing = self.evaluation_by_id(evaluation_id)
        expected = (manifest_uri, manifest_sha256)
        if existing is not None:
            if existing != expected:
                raise FactorIntegrityError("factor evaluation identity conflicts")
            return
        self._connection.execute(
            "INSERT INTO factor_evaluations VALUES (?, ?, ?, ?, ?)",
            [
                evaluation_id,
                factor_materialization_id,
                manifest_uri,
                manifest_sha256,
                created_at,
            ],
        )

    def definition_by_identity(self, factor_id: str, version: str) -> tuple[str, str, str] | None:
        row = self._connection.execute(
            """
            SELECT definition_sha256, manifest_uri, manifest_sha256
            FROM factor_definitions WHERE factor_id = ? AND factor_version = ?
            """,
            [factor_id, version],
        ).fetchone()
        return None if row is None else (str(row[0]), str(row[1]), str(row[2]))

    def definitions(self) -> tuple[tuple[str, str, str], ...]:
        rows = self._connection.execute(
            """
            SELECT factor_id, factor_version, definition_sha256
            FROM factor_definitions ORDER BY factor_id, factor_version
            """
        ).fetchall()
        return tuple((str(row[0]), str(row[1]), str(row[2])) for row in rows)

    def materialization_ids(self, factor_id: str, factor_version: str) -> tuple[str, ...]:
        rows = self._connection.execute(
            """
            SELECT materialization_id FROM factor_materializations
            WHERE factor_id = ? AND factor_version = ?
            ORDER BY materialization_id
            """,
            [factor_id, factor_version],
        ).fetchall()
        return tuple(str(row[0]) for row in rows)

    def register_definition(
        self,
        *,
        factor_id: str,
        factor_version: str,
        definition_sha256: str,
        manifest_uri: str,
        manifest_sha256: str,
        registered_at: datetime,
    ) -> None:
        existing = self.definition_by_identity(factor_id, factor_version)
        expected = (definition_sha256, manifest_uri, manifest_sha256)
        if existing is not None:
            if existing != expected:
                raise FactorIntegrityError("factor identity conflicts with persisted definition")
            return
        self._connection.execute(
            """
            INSERT INTO factor_definitions VALUES (?, ?, ?, ?, ?, ?)
            """,
            [
                factor_id,
                factor_version,
                definition_sha256,
                manifest_uri,
                manifest_sha256,
                registered_at,
            ],
        )

    def materialization_by_request(self, request_sha256: str) -> tuple[str, str, str] | None:
        row = self._connection.execute(
            """
            SELECT materialization_id, manifest_uri, manifest_sha256
            FROM factor_materializations WHERE request_sha256 = ?
            """,
            [request_sha256],
        ).fetchone()
        return None if row is None else (str(row[0]), str(row[1]), str(row[2]))

    def materialization_by_id(self, materialization_id: str) -> tuple[str, str, str] | None:
        row = self._connection.execute(
            """
            SELECT request_sha256, manifest_uri, manifest_sha256
            FROM factor_materializations WHERE materialization_id = ?
            """,
            [materialization_id],
        ).fetchone()
        return None if row is None else (str(row[0]), str(row[1]), str(row[2]))

    def register_materialization(
        self,
        manifest: FactorMaterialization,
        *,
        manifest_uri: str,
        manifest_sha256: str,
    ) -> None:
        existing = self.materialization_by_request(manifest.request_sha256)
        expected = (manifest.materialization_id, manifest_uri, manifest_sha256)
        if existing is not None:
            if existing != expected:
                raise FactorIntegrityError(
                    "factor materialization request produced conflicting output"
                )
            return
        self._connection.execute(
            """
            INSERT INTO factor_materializations VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                manifest.request_sha256,
                manifest.materialization_id,
                manifest.factor_id,
                manifest.factor_version,
                manifest.definition_sha256,
                manifest_uri,
                manifest_sha256,
                manifest.output_uri,
                manifest.output_sha256,
                manifest.output_row_count,
                manifest.created_at,
            ],
        )


class FactorManager:
    """Compute, persist, discover, and verify managed factor artifacts."""

    def __init__(
        self,
        *,
        root: Path,
        registry: ManagedFactorRegistry,
        clock: Callable[[], datetime],
    ) -> None:
        self.root = root.resolve()
        if self.root == Path(self.root.anchor):
            raise ValueError("factor root cannot be the filesystem root")
        self.registry = registry
        self.clock = clock
        self.definitions = self.root / "definitions"
        self.values = self.root / "values"
        self.materializations = self.root / "materializations"
        self.temporary = self.root / ".tmp"
        for path in (self.definitions, self.values, self.materializations, self.temporary):
            path.mkdir(parents=True, exist_ok=True)
        self.catalog = FactorCatalog(self.root / "catalog.duckdb")

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def close(self) -> None:
        self.catalog.close()

    def _relative(self, path: Path) -> str:
        resolved = path.resolve()
        try:
            return resolved.relative_to(self.root).as_posix()
        except ValueError as error:
            raise FactorIntegrityError("factor artifact path escapes its root") from error

    def _write_atomic(self, path: Path, payload: bytes) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists():
            if path.read_bytes() != payload:
                raise FactorIntegrityError("factor artifact identity collision")
            return
        with tempfile.NamedTemporaryFile(
            dir=self.temporary,
            prefix="factor-",
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

    def _register_definition(self, factor_id: str, version: str) -> None:
        registration = self.registry.get(factor_id, version)
        definition = registration.definition
        payload = _canonical_json(definition.model_dump(mode="json"))
        path = self.definitions / factor_id / version / f"{definition.definition_sha256}.json"
        self._write_atomic(path, payload)
        self.catalog.register_definition(
            factor_id=factor_id,
            factor_version=version,
            definition_sha256=definition.definition_sha256,
            manifest_uri=self._relative(path),
            manifest_sha256=_sha256(payload),
            registered_at=self.clock(),
        )

    @staticmethod
    def _parents(artifacts: tuple[FactorArtifact, ...]) -> tuple[ParentMaterializationRef, ...]:
        refs = tuple(
            sorted(
                (
                    ParentMaterializationRef(
                        factor_id=item.manifest.factor_id,
                        factor_version=item.manifest.factor_version,
                        definition_sha256=item.manifest.definition_sha256,
                        materialization_id=item.manifest.materialization_id,
                        output_sha256=item.manifest.output_sha256,
                    )
                    for item in artifacts
                ),
                key=lambda item: item.identity,
            )
        )
        if tuple(item.identity for item in refs) != tuple(sorted({item.identity for item in refs})):
            raise ValueError("parent factor artifacts must be unique")
        return refs

    def materialize(
        self,
        factor_id: str,
        version: str,
        *,
        context: FactorContext,
        scope: FactorScope,
        parents: tuple[FactorArtifact, ...] = (),
    ) -> FactorArtifact:
        if context.as_of != scope.as_of or context.snapshot.as_of > scope.as_of:
            raise ValueError("factor context and materialization scope disagree")
        registration = self.registry.get(factor_id, version)
        expected_parent_identities = tuple(
            sorted(
                (item.factor_id, item.factor_version)
                for item in registration.definition.dependencies
                if isinstance(item, FactorDependency)
            )
        )
        refs = self._parents(parents)
        if tuple(item.identity for item in refs) != expected_parent_identities:
            raise ValueError("factor parent artifacts do not match definition dependencies")
        if any(item.manifest.input_snapshot_id != context.snapshot.snapshot_id for item in parents):
            raise ValueError("factor parent artifacts use a different source snapshot")
        if parents:
            from trademaster.factors import factor_output_schema

            factor_fields = factor_output_schema().names
            if not set(factor_fields) <= set(context.inputs.column_names):
                raise ValueError("factor dependency input schema is incomplete")
            expected_parent_values = pa.concat_tables(
                tuple(
                    item.table.select(factor_fields).replace_schema_metadata(None)
                    for item in parents
                )
            )
            actual_parent_values = context.inputs.select(factor_fields).replace_schema_metadata(
                None
            )
            sort_keys: list[tuple[str, Literal["ascending", "descending"]]] = [
                ("event_time", "ascending"),
                ("instrument_id", "ascending"),
                ("factor_id", "ascending"),
                ("factor_version", "ascending"),
            ]
            if expected_parent_values.num_rows:
                expected_parent_values = expected_parent_values.sort_by(sort_keys)
                actual_parent_values = actual_parent_values.sort_by(sort_keys)
            if not expected_parent_values.equals(actual_parent_values, check_metadata=False):
                raise ValueError("factor dependency inputs differ from declared parent artifacts")
        self._register_definition(factor_id, version)
        input_table_sha256 = _table_sha256(context.inputs)
        request_payload = {
            "definition_sha256": registration.definition.definition_sha256,
            "input_snapshot_id": context.snapshot.snapshot_id,
            "input_snapshot_request_sha256": _snapshot_request_sha256(context.snapshot),
            "input_snapshot_objects_sha256": _snapshot_objects_sha256(context.snapshot),
            "input_table_sha256": input_table_sha256,
            "scope": scope.model_dump(mode="json"),
            "parents": [item.model_dump(mode="json") for item in refs],
        }
        request_sha256 = _sha256(_canonical_json(request_payload))
        cached = self.catalog.materialization_by_request(request_sha256)
        if cached is not None:
            return self._load_cached(
                materialization_id=cached[0],
                manifest_uri=cached[1],
                manifest_sha256=cached[2],
                expected_request_sha256=request_sha256,
            )

        from trademaster.factors import FactorExecutor, FactorRegistry

        external = tuple(item.identity for item in refs)
        output = FactorExecutor(
            FactorRegistry(
                (registration.factor,),
                external_factor_identities=external,
                external_dataset_fields=self.registry.external_dataset_fields,
            )
        ).compute(factor_id, version, context)
        rows = output.to_pylist()
        if not rows:
            raise FactorIntegrityError("factor materialization output cannot be empty")
        event_times = tuple(row["event_time"] for row in rows)
        instruments = tuple(sorted({str(row["instrument_id"]) for row in rows}))
        if (
            min(event_times) < scope.start
            or max(event_times) > scope.end
            or instruments != scope.instruments
        ):
            raise FactorIntegrityError("factor output does not exactly match requested scope")
        metadata = dict(output.schema.metadata or {})
        metadata.update(
            {
                b"trademaster.factor.schema_id": b"trademaster.factor-values/v1",
                b"trademaster.factor.definition_sha256": (
                    registration.definition.definition_sha256.encode()
                ),
                b"trademaster.factor.code_sha256": (registration.definition.code_sha256.encode()),
                b"trademaster.factor.parameters_sha256": (
                    registration.definition.parameters_sha256.encode()
                ),
                b"trademaster.factor.request_sha256": request_sha256.encode(),
            }
        )
        output = output.replace_schema_metadata(metadata)
        with tempfile.NamedTemporaryFile(
            dir=self.temporary,
            prefix="factor-values-",
            suffix=".parquet.tmp",
            delete=False,
        ) as handle:
            temporary = Path(handle.name)
        try:
            pq.write_table(output, temporary)
            output_sha256 = _file_sha256(temporary)
            year_partition = (
                f"{scope.start.year}"
                if scope.start.year == scope.end.year
                else f"{scope.start.year}-{scope.end.year}"
            )
            output_path = (
                self.values
                / f"factor_id={factor_id}"
                / f"factor_version={version}"
                / f"event_year={year_partition}"
                / f"{output_sha256}.parquet"
            )
            output_path.parent.mkdir(parents=True, exist_ok=True)
            if output_path.exists():
                if _file_sha256(output_path) != output_sha256:
                    raise FactorIntegrityError("factor output hash collision")
            else:
                temporary.replace(output_path)
        finally:
            if temporary.exists():
                temporary.unlink()
        manifest = FactorMaterialization.build(
            factor_id=factor_id,
            factor_version=version,
            definition_sha256=registration.definition.definition_sha256,
            code_sha256=registration.definition.code_sha256,
            parameters_sha256=registration.definition.parameters_sha256,
            snapshot=context.snapshot,
            input_table_sha256=input_table_sha256,
            scope=scope,
            parents=refs,
            output_uri=self._relative(output_path),
            output_sha256=output_sha256,
            output_schema_sha256=_schema_sha256(output),
            output_row_count=output.num_rows,
            output_event_time_start=min(event_times),
            output_event_time_end=max(event_times),
            created_at=self.clock(),
        )
        manifest_payload = _canonical_json(manifest.model_dump(mode="json"))
        manifest_path = self.materializations / manifest.materialization_id / "manifest.json"
        self._write_atomic(manifest_path, manifest_payload)
        self.catalog.register_materialization(
            manifest,
            manifest_uri=self._relative(manifest_path),
            manifest_sha256=_sha256(manifest_payload),
        )
        return FactorArtifact(
            definition=registration.definition,
            manifest=manifest,
            table=output,
            path=output_path,
            from_cache=False,
        )

    def load(self, materialization_id: str) -> FactorArtifact:
        existing = self.catalog.materialization_by_id(materialization_id)
        if existing is None:
            raise KeyError(f"unknown factor materialization: {materialization_id}")
        return self._load_cached(
            materialization_id=materialization_id,
            expected_request_sha256=existing[0],
            manifest_uri=existing[1],
            manifest_sha256=existing[2],
        )

    def query_values(
        self,
        factor_id: str,
        version: str,
        *,
        start: datetime,
        end: datetime,
        instruments: tuple[str, ...] = (),
    ) -> pa.Table:
        _require_utc(start)
        _require_utc(end)
        if end < start or instruments != tuple(sorted(set(instruments))):
            raise ValueError("factor value query scope is invalid")
        artifacts = tuple(
            self.load(materialization_id)
            for materialization_id in self.catalog.materialization_ids(factor_id, version)
        )
        rows = [
            row
            for artifact in artifacts
            for row in artifact.table.to_pylist()
            if start <= row["event_time"] <= end
            and (not instruments or row["instrument_id"] in instruments)
        ]
        keys = tuple((row["event_time"], row["instrument_id"]) for row in rows)
        if len(keys) != len(set(keys)):
            raise FactorIntegrityError("factor value query found overlapping materializations")
        from trademaster.factors import factor_output_schema

        return pa.Table.from_pylist(
            sorted(rows, key=lambda row: (row["event_time"], row["instrument_id"])),
            schema=factor_output_schema(),
        )

    def _load_cached(
        self,
        *,
        materialization_id: str,
        manifest_uri: str,
        manifest_sha256: str,
        expected_request_sha256: str,
    ) -> FactorArtifact:
        manifest_path = (self.root / manifest_uri).resolve()
        self._relative(manifest_path)
        try:
            payload = manifest_path.read_bytes()
        except OSError as error:
            raise FactorIntegrityError("factor materialization manifest is missing") from error
        if _sha256(payload) != manifest_sha256:
            raise FactorIntegrityError("factor materialization manifest hash mismatch")
        try:
            manifest = FactorMaterialization.model_validate_json(payload, strict=True)
        except ValueError as error:
            raise FactorIntegrityError("factor materialization manifest is invalid") from error
        if (
            manifest.materialization_id != materialization_id
            or manifest.request_sha256 != expected_request_sha256
        ):
            raise FactorIntegrityError("factor materialization catalog identity mismatch")
        output_path = (self.root / manifest.output_uri).resolve()
        self._relative(output_path)
        if not output_path.is_file() or _file_sha256(output_path) != manifest.output_sha256:
            raise FactorIntegrityError("factor materialization Parquet hash mismatch")
        try:
            table = pq.read_table(output_path)
        except Exception as error:
            raise FactorIntegrityError("factor materialization Parquet is unreadable") from error
        if (
            table.num_rows != manifest.output_row_count
            or _schema_sha256(table) != manifest.output_schema_sha256
        ):
            raise FactorIntegrityError("factor materialization Parquet schema or rows mismatch")
        metadata = table.schema.metadata or {}
        expected_metadata = {
            b"trademaster.factor.definition_sha256": manifest.definition_sha256.encode(),
            b"trademaster.factor.code_sha256": manifest.code_sha256.encode(),
            b"trademaster.factor.parameters_sha256": manifest.parameters_sha256.encode(),
            b"trademaster.factor.request_sha256": manifest.request_sha256.encode(),
        }
        if any(metadata.get(key) != value for key, value in expected_metadata.items()):
            raise FactorIntegrityError("factor materialization Parquet metadata mismatch")
        definition = self.registry.get(manifest.factor_id, manifest.factor_version).definition
        if definition.definition_sha256 != manifest.definition_sha256:
            raise FactorIntegrityError(
                "persisted materialization uses a different factor definition"
            )
        return FactorArtifact(
            definition=definition,
            manifest=manifest,
            table=table,
            path=output_path,
            from_cache=True,
        )


__all__ = [
    "FactorArtifact",
    "FactorCatalog",
    "FactorIntegrityError",
    "FactorManager",
    "FactorMaterialization",
    "FactorScope",
    "ParentMaterializationRef",
]
