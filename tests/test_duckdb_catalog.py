from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path

import duckdb
import pytest
from trademaster.data import (
    CatalogConflictError,
    CatalogCoverage,
    CatalogObject,
    CatalogVersionError,
    DuckDbCatalog,
    default_dataset_registry,
    normalize_request,
)


def _object(**overrides: object) -> CatalogObject:
    values: dict[str, object] = {
        "dataset": "daily_bars",
        "partition_key": "trade_year=2025/trade_month=01",
        "uri": "canonical/daily_bars/trade_year=2025/trade_month=01/a.parquet",
        "sha256": "a" * 64,
        "schema_sha256": "b" * 64,
        "schema_version": "1",
        "row_count": 2,
        "event_time_start": datetime(2025, 1, 2, 7, tzinfo=UTC),
        "event_time_end": datetime(2025, 1, 3, 7, tzinfo=UTC),
        "known_at_max": datetime(2025, 1, 3, 8, tzinfo=UTC),
        "fields": ("close", "instrument_id", "trade_date"),
        "coverage_keys": ("SSE:2025-01-02", "SSE:2025-01-03"),
        "instrument_set_sha256": hashlib.sha256(
            json.dumps(("600000.SH",), separators=(",", ":")).encode()
        ).hexdigest(),
        "instrument_count": 1,
        "created_at": datetime(2025, 1, 3, 9, tzinfo=UTC),
    }
    values.update(overrides)
    return CatalogObject.model_validate(values)


def _coverage(obj: CatalogObject) -> CatalogCoverage:
    request = normalize_request(
        default_dataset_registry(),
        dataset="daily_bars",
        start=datetime(2025, 1, 2, tzinfo=UTC),
        end=datetime(2025, 1, 3, 23, 59, tzinfo=UTC),
        instruments=("600000.SH",),
        fields=("close",),
        coverage_keys=obj.coverage_keys,
    )
    return CatalogCoverage.from_request_object(
        request,
        obj,
        verified_at=datetime(2025, 1, 3, 9, tzinfo=UTC),
    )


def test_catalog_migrates_to_exact_v1_schema(tmp_path: Path) -> None:
    path = tmp_path / "catalog.duckdb"
    with DuckDbCatalog(path) as catalog:
        assert catalog.schema_version == 1

    connection = duckdb.connect(str(path), read_only=True)
    try:
        object_columns = tuple(
            row[0] for row in connection.execute("DESCRIBE objects").fetchall()
        )
        coverage_columns = tuple(
            row[0] for row in connection.execute("DESCRIBE coverage").fetchall()
        )
    finally:
        connection.close()
    assert object_columns == (
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
    assert coverage_columns == (
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


def test_catalog_publication_is_atomic_idempotent_and_queryable(tmp_path: Path) -> None:
    obj = _object()
    coverage = _coverage(obj)
    with DuckDbCatalog(tmp_path / "catalog.duckdb") as catalog:
        catalog.register_publication(obj, coverage)
        catalog.register_publication(obj, coverage)

        assert catalog.objects("daily_bars") == (obj,)
        assert catalog.coverage(coverage.request_sha256) == (coverage,)


def test_catalog_conflict_rolls_back_the_whole_publication(tmp_path: Path) -> None:
    path = tmp_path / "catalog.duckdb"
    obj = _object()
    with DuckDbCatalog(path) as catalog:
        catalog.register_publication(obj, _coverage(obj))
        conflicting = _object(row_count=3)
        with pytest.raises(CatalogConflictError, match="object metadata conflict"):
            catalog.register_publication(conflicting, _coverage(conflicting))

        assert catalog.objects("daily_bars") == (obj,)
        assert len(catalog.coverage(_coverage(obj).request_sha256)) == 1


def test_catalog_rejects_unknown_newer_schema_version(tmp_path: Path) -> None:
    path = tmp_path / "catalog.duckdb"
    connection = duckdb.connect(str(path))
    connection.execute("CREATE TABLE catalog_meta (schema_version INTEGER NOT NULL)")
    connection.execute("INSERT INTO catalog_meta VALUES (999)")
    connection.close()

    with pytest.raises(CatalogVersionError, match="newer catalog schema"):
        DuckDbCatalog(path)


def test_catalog_rejects_multiple_metadata_rows(tmp_path: Path) -> None:
    path = tmp_path / "catalog.duckdb"
    with DuckDbCatalog(path):
        pass
    connection = duckdb.connect(str(path))
    connection.execute("INSERT INTO catalog_meta VALUES (999)")
    connection.close()

    with pytest.raises(CatalogVersionError, match="exactly one row"):
        DuckDbCatalog(path)


def test_catalog_rejects_tampered_persisted_coverage_digest(tmp_path: Path) -> None:
    path = tmp_path / "catalog.duckdb"
    obj = _object()
    coverage = _coverage(obj)
    with DuckDbCatalog(path) as catalog:
        catalog.register_publication(obj, coverage)
    connection = duckdb.connect(str(path))
    connection.execute("UPDATE coverage SET coverage_keys_sha256 = ?", ["d" * 64])
    connection.close()

    with (
        DuckDbCatalog(path) as catalog,
        pytest.raises(CatalogConflictError, match="digest mismatch"),
    ):
        catalog.coverage(coverage.request_sha256)


def test_catalog_rejects_same_named_tables_with_wrong_types_or_constraints(
    tmp_path: Path,
) -> None:
    path = tmp_path / "catalog.duckdb"
    connection = duckdb.connect(str(path))
    connection.execute("CREATE TABLE catalog_meta (schema_version INTEGER NOT NULL)")
    connection.execute("INSERT INTO catalog_meta VALUES (1)")
    object_names = (
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
    coverage_names = (
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
    connection.execute(
        "CREATE TABLE objects (" + ",".join(f"{name} VARCHAR" for name in object_names) + ")"
    )
    connection.execute(
        "CREATE TABLE coverage ("
        + ",".join(f"{name} VARCHAR" for name in coverage_names)
        + ")"
    )
    connection.close()

    with pytest.raises(CatalogVersionError, match="unexpected schema"):
        DuckDbCatalog(path)
