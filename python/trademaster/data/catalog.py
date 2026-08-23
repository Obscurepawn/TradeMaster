"""Versioned DuckDB catalog for immutable Parquet objects and coverage proofs."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path, PurePosixPath
from typing import Any, Self

import duckdb
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from trademaster.contracts import DatasetRequest, _require_utc

CATALOG_SCHEMA_VERSION = 1
_OBJECT_COLUMNS = (
    "dataset",
    "partition_key",
    "uri",
    "sha256",
    "schema_sha256",
    "schema_version",
    "row_count",
    "event_time_start",
    "event_time_end",
    "known_at_max",
    "fields_json",
    "coverage_keys_json",
    "coverage_keys_sha256",
    "instrument_set_sha256",
    "instrument_count",
    "created_at",
)
_COVERAGE_COLUMNS = (
    "dataset",
    "request_sha256",
    "covered_start",
    "covered_end",
    "fields_json",
    "coverage_keys_json",
    "coverage_keys_sha256",
    "instrument_set_sha256",
    "instrument_count",
    "object_sha256",
    "verified_at",
)
_OBJECT_TYPES = (
    "VARCHAR",
    "VARCHAR",
    "VARCHAR",
    "VARCHAR",
    "VARCHAR",
    "VARCHAR",
    "UBIGINT",
    "BIGINT",
    "BIGINT",
    "BIGINT",
    "VARCHAR",
    "VARCHAR",
    "VARCHAR",
    "VARCHAR",
    "UBIGINT",
    "BIGINT",
)
_COVERAGE_TYPES = (
    "VARCHAR",
    "VARCHAR",
    "BIGINT",
    "BIGINT",
    "VARCHAR",
    "VARCHAR",
    "VARCHAR",
    "VARCHAR",
    "UBIGINT",
    "VARCHAR",
    "BIGINT",
)


class CatalogVersionError(RuntimeError):
    pass


class CatalogConflictError(RuntimeError):
    pass


def _canonical_json(values: tuple[str, ...]) -> str:
    return json.dumps(values, ensure_ascii=False, separators=(",", ":"))


def _tuple_sha256(values: tuple[str, ...]) -> str:
    return hashlib.sha256(_canonical_json(values).encode()).hexdigest()


def _timestamp_us(value: datetime) -> int:
    return int((value - datetime(1970, 1, 1, tzinfo=UTC)) / timedelta(microseconds=1))


def _request_sha256(request: DatasetRequest) -> str:
    payload = request.model_dump(mode="json")
    return hashlib.sha256(
        json.dumps(
            payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode()
    ).hexdigest()


def _datetime_from_us(value: int) -> datetime:
    return datetime(1970, 1, 1, tzinfo=UTC) + timedelta(microseconds=value)


class CatalogObject(BaseModel):
    """Catalog metadata for one already verified immutable Parquet object."""

    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    dataset: str = Field(min_length=1)
    partition_key: str = Field(min_length=1)
    uri: str = Field(min_length=1)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    schema_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    schema_version: str = Field(min_length=1)
    row_count: int = Field(ge=0, le=2**64 - 1)
    event_time_start: datetime
    event_time_end: datetime
    known_at_max: datetime
    fields: tuple[str, ...]
    coverage_keys: tuple[str, ...]
    instrument_set_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    instrument_count: int = Field(ge=0, le=2**64 - 1)
    created_at: datetime

    _utc_times = field_validator(
        "event_time_start", "event_time_end", "known_at_max", "created_at"
    )(_require_utc)

    @model_validator(mode="after")
    def validate_object(self) -> CatalogObject:
        if self.event_time_end < self.event_time_start:
            raise ValueError("object event-time range is reversed")
        if self.fields != tuple(sorted(set(self.fields))):
            raise ValueError("object fields must be unique and sorted")
        if self.coverage_keys != tuple(sorted(set(self.coverage_keys))):
            raise ValueError("object coverage keys must be unique and sorted")
        uri = PurePosixPath(self.uri)
        if uri.is_absolute() or ".." in uri.parts or not uri.parts or uri.parts[0] != "canonical":
            raise ValueError("object URI must be a contained canonical relative path")
        return self

    @property
    def coverage_keys_sha256(self) -> str:
        return _tuple_sha256(self.coverage_keys)

class CatalogCoverage(BaseModel):
    """One object's verified contribution to a normalized dataset request."""

    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    dataset: str = Field(min_length=1)
    request_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    covered_start: datetime
    covered_end: datetime
    fields: tuple[str, ...]
    coverage_keys: tuple[str, ...]
    instrument_set_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    instrument_count: int = Field(ge=0, le=2**64 - 1)
    object_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    verified_at: datetime

    _utc_times = field_validator("covered_start", "covered_end", "verified_at")(
        _require_utc
    )

    @model_validator(mode="after")
    def validate_coverage(self) -> CatalogCoverage:
        if self.covered_end < self.covered_start:
            raise ValueError("coverage range is reversed")
        if self.fields != tuple(sorted(set(self.fields))):
            raise ValueError("coverage fields must be unique and sorted")
        if self.coverage_keys != tuple(sorted(set(self.coverage_keys))):
            raise ValueError("coverage keys must be unique and sorted")
        return self

    @property
    def coverage_keys_sha256(self) -> str:
        return _tuple_sha256(self.coverage_keys)

    @staticmethod
    def request_sha256_for(request: DatasetRequest) -> str:
        return _request_sha256(request)

    @classmethod
    def from_request_object(
        cls,
        request: DatasetRequest,
        obj: CatalogObject,
        *,
        verified_at: datetime,
    ) -> Self:
        if request.dataset != obj.dataset:
            raise ValueError("request and object datasets differ")
        if request.coverage_keys != obj.coverage_keys:
            raise ValueError("object does not exactly cover request keys")
        if not set(request.fields) <= set(obj.fields):
            raise ValueError("object does not provide requested fields")
        expected_instruments = _tuple_sha256(request.instruments)
        if request.instruments and (
            obj.instrument_set_sha256 != expected_instruments
            or obj.instrument_count != len(request.instruments)
        ):
            raise ValueError("object does not provide requested instrument universe")
        return cls(
            dataset=request.dataset,
            request_sha256=_request_sha256(request),
            covered_start=request.start,
            covered_end=request.end,
            fields=obj.fields,
            coverage_keys=request.coverage_keys,
            instrument_set_sha256=obj.instrument_set_sha256,
            instrument_count=obj.instrument_count,
            object_sha256=obj.sha256,
            verified_at=verified_at,
        )


class DuckDbCatalog:
    """Small transactional catalog; canonical facts remain in Parquet."""

    def __init__(self, path: Path) -> None:
        self.path = path.expanduser().resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._connection = duckdb.connect(str(self.path))
        try:
            self._migrate_or_validate()
        except Exception:
            self._connection.close()
            raise

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def close(self) -> None:
        self._connection.close()

    @property
    def schema_version(self) -> int:
        rows = self._connection.execute(
            "SELECT schema_version FROM catalog_meta"
        ).fetchall()
        if len(rows) != 1:
            raise CatalogVersionError("catalog metadata must contain exactly one row")
        return int(rows[0][0])

    def _table_exists(self, name: str) -> bool:
        row = self._connection.execute(
            "SELECT count(*) FROM information_schema.tables WHERE table_name = ?", [name]
        ).fetchone()
        return row is not None and int(row[0]) == 1

    def _migrate_or_validate(self) -> None:
        if not self._table_exists("catalog_meta"):
            self._connection.execute("BEGIN TRANSACTION")
            try:
                self._connection.execute(
                    "CREATE TABLE catalog_meta (schema_version INTEGER NOT NULL)"
                )
                self._connection.execute(
                    "INSERT INTO catalog_meta VALUES (?)", [CATALOG_SCHEMA_VERSION]
                )
                self._connection.execute(
                    """
                    CREATE TABLE objects (
                        dataset VARCHAR NOT NULL,
                        partition_key VARCHAR NOT NULL,
                        uri VARCHAR NOT NULL UNIQUE,
                        sha256 VARCHAR NOT NULL,
                        schema_sha256 VARCHAR NOT NULL,
                        schema_version VARCHAR NOT NULL,
                        row_count UBIGINT NOT NULL,
                        event_time_start BIGINT NOT NULL,
                        event_time_end BIGINT NOT NULL,
                        known_at_max BIGINT NOT NULL,
                        fields_json VARCHAR NOT NULL,
                        coverage_keys_json VARCHAR NOT NULL,
                        coverage_keys_sha256 VARCHAR NOT NULL,
                        instrument_set_sha256 VARCHAR NOT NULL,
                        instrument_count UBIGINT NOT NULL,
                        created_at BIGINT NOT NULL,
                        PRIMARY KEY (dataset, partition_key, sha256),
                        UNIQUE (dataset, sha256)
                    )
                    """
                )
                self._connection.execute(
                    """
                    CREATE TABLE coverage (
                        dataset VARCHAR NOT NULL,
                        request_sha256 VARCHAR NOT NULL,
                        covered_start BIGINT NOT NULL,
                        covered_end BIGINT NOT NULL,
                        fields_json VARCHAR NOT NULL,
                        coverage_keys_json VARCHAR NOT NULL,
                        coverage_keys_sha256 VARCHAR NOT NULL,
                        instrument_set_sha256 VARCHAR NOT NULL,
                        instrument_count UBIGINT NOT NULL,
                        object_sha256 VARCHAR NOT NULL,
                        verified_at BIGINT NOT NULL,
                        PRIMARY KEY (dataset, request_sha256, object_sha256)
                    )
                    """
                )
                self._connection.execute("COMMIT")
            except Exception:
                self._connection.execute("ROLLBACK")
                raise
        version = self.schema_version
        if version > CATALOG_SCHEMA_VERSION:
            raise CatalogVersionError(
                f"newer catalog schema {version}; supported {CATALOG_SCHEMA_VERSION}"
            )
        if version < CATALOG_SCHEMA_VERSION:
            raise CatalogVersionError(
                f"older catalog schema {version} has no registered migration"
            )
        self._validate_columns("catalog_meta", ("schema_version",), ("INTEGER",))
        self._validate_columns("objects", _OBJECT_COLUMNS, _OBJECT_TYPES)
        self._validate_columns("coverage", _COVERAGE_COLUMNS, _COVERAGE_TYPES)
        self._validate_identity_constraints()

    def _validate_columns(
        self, table: str, expected: tuple[str, ...], expected_types: tuple[str, ...]
    ) -> None:
        if not self._table_exists(table):
            raise CatalogVersionError(f"catalog table is missing: {table}")
        description = self._connection.execute(f"DESCRIBE {table}").fetchall()
        actual = tuple(str(row[0]) for row in description)
        actual_types = tuple(str(row[1]) for row in description)
        nullable = tuple(str(row[2]) for row in description)
        if actual != expected or actual_types != expected_types or any(
            value != "NO" for value in nullable
        ):
            raise CatalogVersionError(f"catalog table has unexpected schema: {table}")

    def _validate_identity_constraints(self) -> None:
        rows = self._connection.execute(
            """
            SELECT table_name, constraint_type, constraint_column_names
            FROM duckdb_constraints()
            WHERE table_name IN ('objects', 'coverage')
              AND constraint_type IN ('PRIMARY KEY', 'UNIQUE')
            """
        ).fetchall()
        actual = {
            (str(table), str(kind), tuple(str(column) for column in columns))
            for table, kind, columns in rows
        }
        expected = {
            ("objects", "PRIMARY KEY", ("dataset", "partition_key", "sha256")),
            ("objects", "UNIQUE", ("dataset", "sha256")),
            ("objects", "UNIQUE", ("uri",)),
            (
                "coverage",
                "PRIMARY KEY",
                ("dataset", "request_sha256", "object_sha256"),
            ),
        }
        if actual != expected:
            raise CatalogVersionError("catalog identity constraints are invalid")

    def register_publication(
        self, obj: CatalogObject, coverage: CatalogCoverage
    ) -> None:
        if coverage.dataset != obj.dataset or coverage.object_sha256 != obj.sha256:
            raise ValueError("coverage does not reference its published object")
        self._connection.execute("BEGIN TRANSACTION")
        try:
            self._insert_or_compare_object(obj)
            self._insert_or_compare_coverage(coverage)
            self._connection.execute("COMMIT")
        except Exception:
            self._connection.execute("ROLLBACK")
            raise

    def _insert_or_compare_object(self, obj: CatalogObject) -> None:
        existing = self._connection.execute(
            "SELECT * FROM objects WHERE dataset = ? AND partition_key = ? AND sha256 = ?",
            [obj.dataset, obj.partition_key, obj.sha256],
        ).fetchone()
        values = self._object_values(obj)
        if existing is not None:
            if self._object_from_row(existing) != obj:
                raise CatalogConflictError("object metadata conflict")
            return
        try:
            placeholders = ",".join("?" for _ in values)
            self._connection.execute(f"INSERT INTO objects VALUES ({placeholders})", values)
        except duckdb.ConstraintException as error:
            raise CatalogConflictError("object identity conflict") from error

    def _insert_or_compare_coverage(self, coverage: CatalogCoverage) -> None:
        existing = self._connection.execute(
            """
            SELECT * FROM coverage
            WHERE dataset = ? AND request_sha256 = ? AND object_sha256 = ?
            """,
            [coverage.dataset, coverage.request_sha256, coverage.object_sha256],
        ).fetchone()
        values = self._coverage_values(coverage)
        if existing is not None:
            if self._coverage_from_row(existing) != coverage:
                raise CatalogConflictError("coverage metadata conflict")
            return
        self._connection.execute(
            f"INSERT INTO coverage VALUES ({','.join('?' for _ in values)})", values
        )

    def objects(self, dataset: str) -> tuple[CatalogObject, ...]:
        rows = self._connection.execute(
            "SELECT * FROM objects WHERE dataset = ? ORDER BY partition_key, sha256", [dataset]
        ).fetchall()
        return tuple(self._object_from_row(row) for row in rows)

    def coverage(self, request_sha256: str) -> tuple[CatalogCoverage, ...]:
        rows = self._connection.execute(
            "SELECT * FROM coverage WHERE request_sha256 = ? ORDER BY dataset, object_sha256",
            [request_sha256],
        ).fetchall()
        return tuple(self._coverage_from_row(row) for row in rows)

    def coverage_for_object(self, obj: CatalogObject) -> tuple[CatalogCoverage, ...]:
        rows = self._connection.execute(
            """
            SELECT * FROM coverage
            WHERE dataset = ? AND object_sha256 = ?
            ORDER BY request_sha256
            """,
            [obj.dataset, obj.sha256],
        ).fetchall()
        return tuple(self._coverage_from_row(row) for row in rows)

    @staticmethod
    def _object_values(obj: CatalogObject) -> list[Any]:
        return [
            obj.dataset,
            obj.partition_key,
            obj.uri,
            obj.sha256,
            obj.schema_sha256,
            obj.schema_version,
            obj.row_count,
            _timestamp_us(obj.event_time_start),
            _timestamp_us(obj.event_time_end),
            _timestamp_us(obj.known_at_max),
            _canonical_json(obj.fields),
            _canonical_json(obj.coverage_keys),
            obj.coverage_keys_sha256,
            obj.instrument_set_sha256,
            obj.instrument_count,
            _timestamp_us(obj.created_at),
        ]

    @staticmethod
    def _coverage_values(coverage: CatalogCoverage) -> list[Any]:
        return [
            coverage.dataset,
            coverage.request_sha256,
            _timestamp_us(coverage.covered_start),
            _timestamp_us(coverage.covered_end),
            _canonical_json(coverage.fields),
            _canonical_json(coverage.coverage_keys),
            coverage.coverage_keys_sha256,
            coverage.instrument_set_sha256,
            coverage.instrument_count,
            coverage.object_sha256,
            _timestamp_us(coverage.verified_at),
        ]

    @staticmethod
    def _object_from_row(row: tuple[Any, ...]) -> CatalogObject:
        obj = CatalogObject(
            dataset=row[0],
            partition_key=row[1],
            uri=row[2],
            sha256=row[3],
            schema_sha256=row[4],
            schema_version=row[5],
            row_count=row[6],
            event_time_start=_datetime_from_us(row[7]),
            event_time_end=_datetime_from_us(row[8]),
            known_at_max=_datetime_from_us(row[9]),
            fields=tuple(json.loads(row[10])),
            coverage_keys=tuple(json.loads(row[11])),
            instrument_set_sha256=row[13],
            instrument_count=row[14],
            created_at=_datetime_from_us(row[15]),
        )
        if row[12] != obj.coverage_keys_sha256:
            raise CatalogConflictError("object coverage-key digest mismatch")
        return obj

    @staticmethod
    def _coverage_from_row(row: tuple[Any, ...]) -> CatalogCoverage:
        coverage = CatalogCoverage(
            dataset=row[0],
            request_sha256=row[1],
            covered_start=_datetime_from_us(row[2]),
            covered_end=_datetime_from_us(row[3]),
            fields=tuple(json.loads(row[4])),
            coverage_keys=tuple(json.loads(row[5])),
            instrument_set_sha256=row[7],
            instrument_count=row[8],
            object_sha256=row[9],
            verified_at=_datetime_from_us(row[10]),
        )
        if row[6] != coverage.coverage_keys_sha256:
            raise CatalogConflictError("coverage-key digest mismatch")
        return coverage
