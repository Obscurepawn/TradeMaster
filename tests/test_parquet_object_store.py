from __future__ import annotations

import hashlib
import json
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import pyarrow as pa
import pyarrow.parquet as pq
import pytest
from trademaster.contracts import DatasetRequest
from trademaster.data import (
    DataConfig,
    DuckDbCatalog,
    ObjectIntegrityError,
    ParquetObjectStore,
    default_dataset_registry,
    normalize_request,
)


def _calendar_table(*, duplicate: bool = False) -> pa.Table:
    rows = [
        {
            "venue": "SSE",
            "session_date": date(2025, 1, 2),
            "is_open": True,
            "open_at": datetime(2025, 1, 2, 1, 30, tzinfo=UTC),
            "close_at": datetime(2025, 1, 2, 7, tzinfo=UTC),
            "event_time": datetime(2025, 1, 2, 7, tzinfo=UTC),
            "known_at": datetime(2025, 1, 2, 8, tzinfo=UTC),
            "source_revision": "fixture-v1",
        },
        {
            "venue": "SSE",
            "session_date": date(2025, 1, 3),
            "is_open": True,
            "open_at": datetime(2025, 1, 3, 1, 30, tzinfo=UTC),
            "close_at": datetime(2025, 1, 3, 7, tzinfo=UTC),
            "event_time": datetime(2025, 1, 3, 7, tzinfo=UTC),
            "known_at": datetime(2025, 1, 3, 8, tzinfo=UTC),
            "source_revision": "fixture-v1",
        },
    ]
    if duplicate:
        rows.append(dict(rows[-1]))
    schema_fields: Any = [
        pa.field("venue", pa.string(), nullable=False),
        pa.field("session_date", pa.date32(), nullable=False),
        pa.field("is_open", pa.bool_(), nullable=False),
        pa.field("open_at", pa.timestamp("us", tz="UTC"), nullable=False),
        pa.field("close_at", pa.timestamp("us", tz="UTC"), nullable=False),
        pa.field("event_time", pa.timestamp("us", tz="UTC"), nullable=False),
        pa.field("known_at", pa.timestamp("us", tz="UTC"), nullable=False),
        pa.field("source_revision", pa.string(), nullable=False),
    ]
    schema = pa.schema(schema_fields)
    return pa.Table.from_pylist(rows, schema=schema)


def _store(tmp_path: Path) -> ParquetObjectStore:
    config = DataConfig(
        data_root=tmp_path / "data",
        log_dir=tmp_path / "logs",
        canonical_source_policy="trusted_imports",
    )
    config.paths.ensure_layout()
    return ParquetObjectStore(config.paths, default_dataset_registry())


def _request() -> DatasetRequest:
    return normalize_request(
        default_dataset_registry(),
        dataset="trade_calendar",
        start=datetime(2025, 1, 2, tzinfo=UTC),
        end=datetime(2025, 1, 3, 23, 59, tzinfo=UTC),
        fields=("is_open",),
        coverage_keys=("SSE:2025-01-02", "SSE:2025-01-03"),
    )


def _write_evidence(table: pa.Table, directory: Path) -> str:
    directory.mkdir(parents=True, exist_ok=True)
    temporary = directory / "test-evidence.parquet"
    pq.write_table(table, temporary)
    digest = hashlib.sha256(temporary.read_bytes()).hexdigest()
    temporary.rename(directory / f"{digest}.parquet")
    return digest


def _raw_evidence(
    *,
    root: Path,
    endpoint: str,
    fields: str,
    params: dict[str, str],
    rows: list[dict[str, object]],
    ingested_at: str = "2025-01-03T00:00:00+00:00",
    page_index: int = 0,
    page_cursor: int = 0,
) -> str:
    request = {"endpoint": endpoint, "fields": fields, "params": params}
    request_digest = hashlib.sha256(
        json.dumps(request, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    revisions = sorted(
        hashlib.sha256(
            json.dumps(row, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        for row in rows
    )
    table = (
        pa.Table.from_pylist(rows)
        if rows
        else pa.table({"_empty": pa.array([], type=pa.bool_())})
    )
    table = table.replace_schema_metadata(
        {
            b"trademaster.provider_request/v1": json.dumps(
                {
                    **request,
                    "ingested_at": ingested_at,
                    "provider_request_sha256": request_digest,
                    "page_cursor": str(page_cursor),
                    "page_index": page_index,
                    "returned_rows": len(rows),
                    "row_source_revisions": revisions,
                },
                sort_keys=True,
                separators=(",", ":"),
            ).encode()
        }
    )
    ingest_date = datetime.fromisoformat(ingested_at).date().isoformat()
    return _write_evidence(table, root / "raw" / endpoint / f"ingest_date={ingest_date}")


def _staging_evidence(
    *,
    root: Path,
    request: DatasetRequest,
    table: pa.Table,
    raw_hashes: tuple[str, ...],
    normalized_at: str = "2025-01-03T00:00:00+00:00",
) -> str:
    request_digest = hashlib.sha256(
        json.dumps(
            request.model_dump(mode="json"),
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
    ).hexdigest()
    table = table.replace_schema_metadata(
        {
            b"trademaster.staging/v1": json.dumps(
                {
                    "dataset": request.dataset,
                    "normalized_at": normalized_at,
                    "request_sha256": request_digest,
                    "coverage_key": request.coverage_keys[0],
                    "raw_object_sha256s": raw_hashes,
                    "source_revision_sha256s": sorted(
                        table["source_revision"].to_pylist()
                    ),
                },
                sort_keys=True,
                separators=(",", ":"),
            ).encode()
        }
    )
    return _write_evidence(
        table, root / "staging" / request.dataset / "split-fixture"
    )


def _status_false_attack_fixture(
    config: DataConfig,
) -> tuple[DatasetRequest, pa.Table, str, str]:
    registry = default_dataset_registry()
    instrument = "600000.SH"
    request = DatasetRequest(
        dataset="daily_limits_status",
        start=datetime(2025, 1, 2, tzinfo=UTC),
        end=datetime(2025, 1, 2, 23, 59, tzinfo=UTC),
        instruments=(instrument,),
        fields=("suspended",),
        coverage_keys=("SSE:2025-01-02",),
    )
    limit: dict[str, object] = {
        "ts_code": instrument,
        "trade_date": "20250102",
        "up_limit": 11.0,
        "down_limit": 9.0,
    }
    params = {"trade_date": "20250102"}
    raw_limit = _raw_evidence(
        root=config.paths.data_root,
        endpoint="stk_limit",
        fields="ts_code,trade_date,up_limit,down_limit",
        params=params,
        rows=[limit],
        ingested_at="2025-01-04T00:00:00+00:00",
    )
    raw_st = _raw_evidence(
        root=config.paths.data_root,
        endpoint="stock_st",
        fields="ts_code,trade_date,type",
        params=params,
        rows=[],
        ingested_at="2025-01-04T00:00:00+00:00",
    )
    false_payload = {**limit, "suspended": False, "is_st": False}
    false_revision = hashlib.sha256(
        json.dumps(false_payload, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    canonical = pa.Table.from_pylist(
        [
            {
                "instrument_id": instrument,
                "trade_date": date(2025, 1, 2),
                "up_limit": 11.0,
                "down_limit": 9.0,
                "suspended": False,
                "is_st": False,
                "venue": "SSE",
                "event_time": datetime(2025, 1, 2, 1, 20, tzinfo=UTC),
                "known_at": datetime(2025, 1, 2, 1, 20, tzinfo=UTC),
                "source_revision": false_revision,
            }
        ],
        schema=registry["daily_limits_status"].arrow_schema,
    )
    return request, canonical, raw_limit, raw_st


def test_object_store_publishes_real_content_addressed_parquet(tmp_path: Path) -> None:
    store = _store(tmp_path)
    table = _calendar_table()
    publication = store.publish(
        _request(),
        table,
        partition_key="venue=SSE/session_year=2025",
        created_at=datetime(2025, 1, 3, 9, tzinfo=UTC),
    )

    assert publication.path == (store.paths.data_root / publication.object.uri)
    payload = publication.path.read_bytes()
    assert hashlib.sha256(payload).hexdigest() == publication.object.sha256
    assert publication.path.name == f"{publication.object.sha256}.parquet"
    assert pq.read_table(publication.path).to_pylist() == table.to_pylist()
    assert store.verify(publication.object) == publication.object
    assert not tuple(store.paths.temporary.iterdir())


def test_tushare_only_store_rejects_canonical_publication_without_upstream(
    tmp_path: Path,
) -> None:
    config = DataConfig(data_root=tmp_path / "data", log_dir=tmp_path / "logs")
    config.paths.ensure_layout()
    store = ParquetObjectStore(config.paths, default_dataset_registry())

    with pytest.raises(ObjectIntegrityError, match="requires raw/staging provenance"):
        store.publish(
            _request(),
            _calendar_table(),
            partition_key="venue=SSE/session_year=2025",
            created_at=datetime(2025, 1, 4, tzinfo=UTC),
        )


def test_composite_status_staging_cannot_split_required_raw_endpoints(
    tmp_path: Path,
) -> None:
    config = DataConfig(data_root=tmp_path / "data", log_dir=tmp_path / "logs")
    config.paths.ensure_layout()
    registry = default_dataset_registry()
    instrument = "600000.SH"
    request = DatasetRequest(
        dataset="daily_limits_status",
        start=datetime(2025, 1, 2, tzinfo=UTC),
        end=datetime(2025, 1, 2, 23, 59, tzinfo=UTC),
        instruments=(instrument,),
        fields=("suspended",),
        coverage_keys=("SSE:2025-01-02",),
    )
    limit: dict[str, object] = {
        "ts_code": instrument,
        "trade_date": "20250102",
        "up_limit": 11.0,
        "down_limit": 9.0,
    }
    suspension: dict[str, object] = {
        "ts_code": instrument,
        "trade_date": "20250102",
        "suspend_type": "S",
    }
    params = {"trade_date": "20250102"}
    raw_limit = _raw_evidence(
        root=config.paths.data_root,
        endpoint="stk_limit",
        fields="ts_code,trade_date,up_limit,down_limit",
        params=params,
        rows=[limit],
    )
    raw_suspend = _raw_evidence(
        root=config.paths.data_root,
        endpoint="suspend_d",
        fields="ts_code,trade_date,suspend_type",
        params={**params, "suspend_type": "S"},
        rows=[suspension],
    )
    raw_st = _raw_evidence(
        root=config.paths.data_root,
        endpoint="stock_st",
        fields="ts_code,trade_date,type",
        params=params,
        rows=[],
    )
    false_payload = {**limit, "suspended": False, "is_st": False}
    false_revision = hashlib.sha256(
        json.dumps(false_payload, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    false_table = pa.Table.from_pylist(
        [
            {
                "instrument_id": instrument,
                "trade_date": date(2025, 1, 2),
                "up_limit": 11.0,
                "down_limit": 9.0,
                "suspended": False,
                "is_st": False,
                "venue": "SSE",
                "event_time": datetime(2025, 1, 2, 1, 20, tzinfo=UTC),
                "known_at": datetime(2025, 1, 2, 1, 20, tzinfo=UTC),
                "source_revision": false_revision,
            }
        ],
        schema=registry["daily_limits_status"].arrow_schema,
    )
    empty_table = pa.Table.from_pylist(
        [], schema=registry["daily_limits_status"].arrow_schema
    )
    staging_false = _staging_evidence(
        root=config.paths.data_root,
        request=request,
        table=false_table,
        raw_hashes=tuple(sorted((raw_limit, raw_st))),
    )
    staging_orphan_suspend = _staging_evidence(
        root=config.paths.data_root,
        request=request,
        table=empty_table,
        raw_hashes=(raw_suspend,),
    )
    store = ParquetObjectStore(config.paths, registry)

    with pytest.raises(ObjectIntegrityError, match="every provider endpoint"):
        store.publish(
            request,
            false_table,
            partition_key="trade_year=2025/trade_month=01",
            created_at=datetime(2025, 1, 3, tzinfo=UTC),
            upstream_object_sha256s=tuple(
                sorted(
                    (
                        raw_limit,
                        raw_suspend,
                        raw_st,
                        staging_false,
                        staging_orphan_suspend,
                    )
                )
            ),
        )


def test_composite_status_rejects_unterminated_full_raw_page(
    tmp_path: Path,
) -> None:
    config = DataConfig(data_root=tmp_path / "data", log_dir=tmp_path / "logs")
    config.paths.ensure_layout()
    registry = default_dataset_registry()
    instrument = "600000.SH"
    request = DatasetRequest(
        dataset="daily_limits_status",
        start=datetime(2025, 1, 2, tzinfo=UTC),
        end=datetime(2025, 1, 2, 23, 59, tzinfo=UTC),
        instruments=(instrument,),
        fields=("suspended",),
        coverage_keys=("SSE:2025-01-02",),
    )
    limit: dict[str, object] = {
        "ts_code": instrument,
        "trade_date": "20250102",
        "up_limit": 11.0,
        "down_limit": 9.0,
    }
    params = {"trade_date": "20250102"}
    raw_limit = _raw_evidence(
        root=config.paths.data_root,
        endpoint="stk_limit",
        fields="ts_code,trade_date,up_limit,down_limit",
        params=params,
        rows=[limit],
    )
    raw_suspend = _raw_evidence(
        root=config.paths.data_root,
        endpoint="suspend_d",
        fields="ts_code,trade_date,suspend_type",
        params={**params, "suspend_type": "S"},
        rows=[
            {
                "ts_code": f"DUMMY{index:05d}.SH",
                "trade_date": "20250102",
                "suspend_type": "S",
            }
            for index in range(5000)
        ],
    )
    raw_st = _raw_evidence(
        root=config.paths.data_root,
        endpoint="stock_st",
        fields="ts_code,trade_date,type",
        params=params,
        rows=[],
    )
    false_payload = {**limit, "suspended": False, "is_st": False}
    false_revision = hashlib.sha256(
        json.dumps(false_payload, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    canonical = pa.Table.from_pylist(
        [
            {
                "instrument_id": instrument,
                "trade_date": date(2025, 1, 2),
                "up_limit": 11.0,
                "down_limit": 9.0,
                "suspended": False,
                "is_st": False,
                "venue": "SSE",
                "event_time": datetime(2025, 1, 2, 1, 20, tzinfo=UTC),
                "known_at": datetime(2025, 1, 2, 1, 20, tzinfo=UTC),
                "source_revision": false_revision,
            }
        ],
        schema=registry["daily_limits_status"].arrow_schema,
    )
    raw_hashes = tuple(sorted((raw_limit, raw_suspend, raw_st)))
    staging = _staging_evidence(
        root=config.paths.data_root,
        request=request,
        table=canonical,
        raw_hashes=raw_hashes,
    )
    store = ParquetObjectStore(config.paths, registry)

    with pytest.raises(ObjectIntegrityError, match="terminal page"):
        store.publish(
            request,
            canonical,
            partition_key="trade_year=2025/trade_month=01",
            created_at=datetime(2025, 1, 3, tzinfo=UTC),
            upstream_object_sha256s=tuple(sorted((*raw_hashes, staging))),
        )


def test_mutable_status_raw_pages_cannot_splice_different_ingestions(
    tmp_path: Path,
) -> None:
    config = DataConfig(data_root=tmp_path / "data", log_dir=tmp_path / "logs")
    config.paths.ensure_layout()
    request, canonical, raw_limit, raw_st = _status_false_attack_fixture(config)
    params = {"trade_date": "20250102", "suspend_type": "S"}
    old_page: list[dict[str, object]] = [
        {
            "ts_code": f"OLD{index:05d}.SH",
            "trade_date": "20250102",
            "suspend_type": "S",
        }
        for index in range(5000)
    ]
    old_page_zero = _raw_evidence(
        root=config.paths.data_root,
        endpoint="suspend_d",
        fields="ts_code,trade_date,suspend_type",
        params=params,
        rows=old_page,
        ingested_at="2025-01-03T00:00:00+00:00",
    )
    _raw_evidence(
        root=config.paths.data_root,
        endpoint="suspend_d",
        fields="ts_code,trade_date,suspend_type",
        params=params,
        rows=[
            {
                "ts_code": "600000.SH" if index == 0 else f"NEW{index:05d}.SH",
                "trade_date": "20250102",
                "suspend_type": "S",
            }
            for index in range(5000)
        ],
        ingested_at="2025-01-04T00:00:00+00:00",
    )
    new_terminal = _raw_evidence(
        root=config.paths.data_root,
        endpoint="suspend_d",
        fields="ts_code,trade_date,suspend_type",
        params=params,
        rows=[],
        ingested_at="2025-01-04T00:00:00+00:00",
        page_index=1,
        page_cursor=5000,
    )
    raw_hashes = tuple(sorted((raw_limit, raw_st, old_page_zero, new_terminal)))
    staging = _staging_evidence(
        root=config.paths.data_root,
        request=request,
        table=canonical,
        raw_hashes=raw_hashes,
        normalized_at="2025-01-04T00:00:00+00:00",
    )
    store = ParquetObjectStore(config.paths, default_dataset_registry())

    with pytest.raises(ObjectIntegrityError, match="mutable raw observation"):
        store.publish(
            request,
            canonical,
            partition_key="trade_year=2025/trade_month=01",
            created_at=datetime(2025, 1, 4, 1, tzinfo=UTC),
            upstream_object_sha256s=tuple(sorted((*raw_hashes, staging))),
        )


def test_status_raw_page_chain_rejects_duplicate_full_page_content(
    tmp_path: Path,
) -> None:
    config = DataConfig(data_root=tmp_path / "data", log_dir=tmp_path / "logs")
    config.paths.ensure_layout()
    request, canonical, raw_limit, raw_st = _status_false_attack_fixture(config)
    params = {"trade_date": "20250102", "suspend_type": "S"}
    repeated_page: list[dict[str, object]] = [
        {
            "ts_code": f"DUP{index:05d}.SH",
            "trade_date": "20250102",
            "suspend_type": "S",
        }
        for index in range(5000)
    ]
    suspend_pages = tuple(
        _raw_evidence(
            root=config.paths.data_root,
            endpoint="suspend_d",
            fields="ts_code,trade_date,suspend_type",
            params=params,
            rows=repeated_page if page_index < 2 else [],
            ingested_at="2025-01-04T00:00:00+00:00",
            page_index=page_index,
            page_cursor=page_index * 5000,
        )
        for page_index in range(3)
    )
    raw_hashes = tuple(sorted((raw_limit, raw_st, *suspend_pages)))
    staging = _staging_evidence(
        root=config.paths.data_root,
        request=request,
        table=canonical,
        raw_hashes=raw_hashes,
        normalized_at="2025-01-04T00:00:00+00:00",
    )
    store = ParquetObjectStore(config.paths, default_dataset_registry())

    with pytest.raises(ObjectIntegrityError, match="repeated a provider page"):
        store.publish(
            request,
            canonical,
            partition_key="trade_year=2025/trade_month=01",
            created_at=datetime(2025, 1, 4, 1, tzinfo=UTC),
            upstream_object_sha256s=tuple(sorted((*raw_hashes, staging))),
        )


def test_object_store_rejects_invalid_rows_before_publishing(tmp_path: Path) -> None:
    store = _store(tmp_path)
    with pytest.raises(ObjectIntegrityError, match="duplicate canonical primary key"):
        store.publish(
            _request(),
            _calendar_table(duplicate=True),
            partition_key="venue=SSE/session_year=2025",
            created_at=datetime(2025, 1, 3, 9, tzinfo=UTC),
        )
    invalid_availability = _calendar_table().set_column(
        6,
        pa.field(
            "known_at", pa.timestamp("us", tz="UTC"), nullable=False
        ),
        pa.array(
            [
                datetime(2025, 1, 2, 6, tzinfo=UTC),
                datetime(2025, 1, 3, 6, tzinfo=UTC),
            ],
            type=pa.timestamp("us", tz="UTC"),
        ),
    )
    with pytest.raises(ObjectIntegrityError, match="known_at cannot precede event_time"):
        store.publish(
            _request(),
            invalid_availability,
            partition_key="venue=SSE/session_year=2025",
            created_at=datetime(2025, 1, 3, 9, tzinfo=UTC),
        )
    assert not tuple(store.paths.canonical.rglob("*.parquet"))


def test_object_store_rejects_schema_drift_and_unresolved_instrument_universe(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path)
    registry = default_dataset_registry()
    request = normalize_request(
        registry,
        dataset="daily_bars",
        start=datetime(2025, 1, 2, tzinfo=UTC),
        end=datetime(2025, 1, 2, 23, 59, tzinfo=UTC),
        fields=("close",),
        coverage_keys=("SSE:2025-01-02",),
    )
    row: dict[str, object] = {
        "instrument_id": "600000.SH",
        "trade_date": date(2025, 1, 2),
        "open": "10.0",
        "high": "11.0",
        "low": "9.0",
        "close": "10.5",
        "volume": "1000",
        "amount": "10500",
        "pre_close": "9.9",
        "venue": "SSE",
        "event_time": datetime(2025, 1, 2, 7, tzinfo=UTC),
        "known_at": datetime(2025, 1, 2, 8, tzinfo=UTC),
        "source_revision": "fixture",
    }
    canonical_schema_fields: Any = [
        pa.field("instrument_id", pa.string(), nullable=False),
        pa.field("trade_date", pa.date32(), nullable=False),
        pa.field("open", pa.float64(), nullable=False),
        pa.field("high", pa.float64(), nullable=False),
        pa.field("low", pa.float64(), nullable=False),
        pa.field("close", pa.float64(), nullable=False),
        pa.field("volume", pa.float64(), nullable=False),
        pa.field("amount", pa.float64(), nullable=False),
        pa.field("pre_close", pa.float64(), nullable=False),
        pa.field("venue", pa.string(), nullable=False),
        pa.field("event_time", pa.timestamp("us", tz="UTC"), nullable=False),
        pa.field("known_at", pa.timestamp("us", tz="UTC"), nullable=False),
        pa.field("source_revision", pa.string(), nullable=False),
    ]
    canonical_schema = pa.schema(canonical_schema_fields)
    canonical_row = {
        **row,
        "open": 10.0,
        "high": 11.0,
        "low": 9.0,
        "close": 10.5,
        "volume": 1000.0,
        "amount": 10500.0,
        "pre_close": 9.9,
    }
    canonical = pa.Table.from_pylist([canonical_row], schema=canonical_schema)
    with pytest.raises(ObjectIntegrityError, match="instrument universe"):
        store.publish(
            request,
            canonical,
            partition_key="trade_year=2025/trade_month=01",
            created_at=datetime(2025, 1, 2, 9, tzinfo=UTC),
        )

    resolved = request.model_copy(update={"instruments": ("600000.SH",)})
    drifted = pa.Table.from_pylist([row])
    with pytest.raises(ObjectIntegrityError, match="canonical field type"):
        store.publish(
            resolved,
            drifted,
            partition_key="trade_year=2025/trade_month=01",
            created_at=datetime(2025, 1, 2, 9, tzinfo=UTC),
        )

    for malformed in (
        canonical.select(tuple(reversed(canonical.column_names))),
        pa.Table.from_arrays(
            canonical.columns,
            schema=pa.schema([field.with_nullable(True) for field in canonical.schema]),
        ),
    ):
        with pytest.raises(ObjectIntegrityError, match="canonical schema"):
            store.publish(
                resolved,
                malformed,
                partition_key="trade_year=2025/trade_month=01",
                created_at=datetime(2025, 1, 2, 9, tzinfo=UTC),
            )


def test_object_store_rejects_incomplete_required_instrument_session_matrix(
    tmp_path: Path,
) -> None:
    registry = default_dataset_registry()
    store = _store(tmp_path)
    request = normalize_request(
        registry,
        dataset="daily_limits_status",
        start=datetime(2025, 1, 2, tzinfo=UTC),
        end=datetime(2025, 1, 3, 23, 59, tzinfo=UTC),
        instruments=("600000.SH", "600001.SH"),
        fields=("suspended",),
        coverage_keys=("SSE:2025-01-02", "SSE:2025-01-03"),
    )
    rows = [
        {
            "instrument_id": instrument_id,
            "trade_date": trade_date,
            "up_limit": 11.0,
            "down_limit": 9.0,
            "suspended": False,
            "is_st": False,
            "venue": "SSE",
            "event_time": datetime(
                trade_date.year, trade_date.month, trade_date.day, 1, 20, tzinfo=UTC
            ),
            "known_at": datetime(
                trade_date.year, trade_date.month, trade_date.day, 1, 20, tzinfo=UTC
            ),
            "source_revision": "fixture",
        }
        for instrument_id, trade_date in (
            ("600000.SH", date(2025, 1, 2)),
            ("600001.SH", date(2025, 1, 3)),
        )
    ]
    partial_matrix = pa.Table.from_pylist(
        rows, schema=registry["daily_limits_status"].arrow_schema
    )
    with pytest.raises(ObjectIntegrityError, match="instrument-session matrix"):
        store.publish(
            request,
            partial_matrix,
            partition_key="trade_year=2025/trade_month=01",
            created_at=datetime(2025, 1, 4, tzinfo=UTC),
        )

    bar_request = request.model_copy(
        update={"dataset": "daily_bars", "fields": ("close",)}
    )
    bar_rows = [
        {
            "instrument_id": instrument_id,
            "trade_date": trade_date,
            "open": 10.0,
            "high": 11.0,
            "low": 9.0,
            "close": 10.5,
            "volume": 1000.0,
            "amount": 10500.0,
            "pre_close": 9.9,
            "venue": "SSE",
            "event_time": datetime(
                trade_date.year, trade_date.month, trade_date.day, 7, tzinfo=UTC
            ),
            "known_at": datetime(
                trade_date.year, trade_date.month, trade_date.day, 8, tzinfo=UTC
            ),
            "source_revision": "fixture",
        }
        for instrument_id, trade_date in (
            ("600000.SH", date(2025, 1, 2)),
            ("600001.SH", date(2025, 1, 3)),
        )
    ]
    sparse_bars = pa.Table.from_pylist(
        bar_rows, schema=registry["daily_bars"].arrow_schema
    )
    with pytest.raises(ObjectIntegrityError, match="instrument-session matrix"):
        store.publish(
            bar_request,
            sparse_bars,
            partition_key="trade_year=2025/trade_month=01",
            created_at=datetime(2025, 1, 4, tzinfo=UTC),
        )


def test_object_store_detects_tampering_without_overwrite(tmp_path: Path) -> None:
    store = _store(tmp_path)
    publication = store.publish(
        _request(),
        _calendar_table(),
        partition_key="venue=SSE/session_year=2025",
        created_at=datetime(2025, 1, 3, 9, tzinfo=UTC),
    )
    publication.path.write_bytes(b"tampered")

    with pytest.raises(ObjectIntegrityError, match="content hash"):
        store.verify(publication.object)


def test_object_store_rejects_unresolvable_upstream_hashes(tmp_path: Path) -> None:
    store = _store(tmp_path)
    with pytest.raises(ObjectIntegrityError, match="cannot be resolved"):
        store.publish(
            _request(),
            _calendar_table(),
            partition_key="venue=SSE/session_year=2025",
            created_at=datetime(2025, 1, 3, 9, tzinfo=UTC),
            upstream_object_sha256s=("a" * 64,),
        )


def test_object_store_rejects_upstream_staging_that_disagrees_with_canonical(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path)
    request = normalize_request(
        default_dataset_registry(),
        dataset="trade_calendar",
        start=datetime(2025, 1, 2, tzinfo=UTC),
        end=datetime(2025, 1, 2, 23, 59, tzinfo=UTC),
        fields=("is_open",),
        coverage_keys=("SSE:2025-01-02",),
    )
    canonical = _calendar_table().slice(0, 1)
    raw_row = {
        "exchange": "SSE",
        "cal_date": "20250102",
        "is_open": 0,
        "pretrade_date": "20241231",
    }
    raw_revision = hashlib.sha256(
        json.dumps(
            raw_row, sort_keys=True, separators=(",", ":")
        ).encode()
    ).hexdigest()
    provider_request = {
        "endpoint": "trade_cal",
        "fields": "exchange,cal_date,is_open,pretrade_date",
        "params": {
            "exchange": "SSE",
            "start_date": "20250102",
            "end_date": "20250102",
        },
    }
    provider_request_sha256 = hashlib.sha256(
        json.dumps(provider_request, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    raw = pa.Table.from_pylist([raw_row])
    raw = raw.replace_schema_metadata(
        {
            b"trademaster.provider_request/v1": json.dumps(
                {
                    **provider_request,
                    "ingested_at": "2025-01-03T09:00:00+00:00",
                    "provider_request_sha256": provider_request_sha256,
                    "page_cursor": "0",
                    "page_index": 0,
                    "returned_rows": 1,
                    "row_source_revisions": [raw_revision],
                },
                sort_keys=True,
                separators=(",", ":"),
            ).encode()
        }
    )
    raw_dir = store.paths.raw / "trade_cal" / "ingest_date=2025-01-03"
    raw_dir.mkdir(parents=True)
    raw_temp = raw_dir / "raw.parquet"
    pq.write_table(raw, raw_temp)
    raw_sha256 = hashlib.sha256(raw_temp.read_bytes()).hexdigest()
    raw_path = raw_temp.with_name(f"{raw_sha256}.parquet")
    raw_temp.rename(raw_path)

    disagreeing = canonical.set_column(
        canonical.column_names.index("is_open"),
        canonical.schema.field("is_open"),
        pa.array([False], type=pa.bool_()),
    )
    request_sha256 = hashlib.sha256(
        json.dumps(
            request.model_dump(mode="json"),
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
    ).hexdigest()
    staging = disagreeing.replace_schema_metadata(
        {
            b"trademaster.staging/v1": json.dumps(
                {
                    "dataset": request.dataset,
                    "normalized_at": "2025-01-03T09:00:00+00:00",
                    "request_sha256": request_sha256,
                    "coverage_key": request.coverage_keys[0],
                    "raw_object_sha256s": [raw_sha256],
                    "source_revision_sha256s": sorted(
                        disagreeing["source_revision"].to_pylist()
                    ),
                },
                sort_keys=True,
                separators=(",", ":"),
            ).encode()
        }
    )
    staging_dir = store.paths.staging / request.dataset / "fixture"
    staging_dir.mkdir(parents=True)
    staging_temp = staging_dir / "staging.parquet"
    pq.write_table(staging, staging_temp)
    staging_sha256 = hashlib.sha256(staging_temp.read_bytes()).hexdigest()
    staging_path = staging_temp.with_name(f"{staging_sha256}.parquet")
    staging_temp.rename(staging_path)

    with pytest.raises(ObjectIntegrityError, match="staging content"):
        store.publish(
            request,
            canonical,
            partition_key="venue=SSE/session_year=2025",
            created_at=datetime(2025, 1, 3, 9, tzinfo=UTC),
            upstream_object_sha256s=tuple(sorted((raw_sha256, staging_sha256))),
        )


def test_startup_recovery_cleans_temps_and_registers_valid_orphan(tmp_path: Path) -> None:
    store = _store(tmp_path)
    publication = store.publish(
        _request(),
        _calendar_table(),
        partition_key="venue=SSE/session_year=2025",
        created_at=datetime(2025, 1, 3, 9, tzinfo=UTC),
    )
    crashed_temp = store.paths.temporary / "crashed.parquet.tmp"
    crashed_temp.write_bytes(b"partial")

    with DuckDbCatalog(store.paths.catalog) as catalog:
        recovered = store.recover(catalog)
        assert recovered == (publication,)
        assert catalog.objects("trade_calendar") == (publication.object,)
        assert catalog.coverage(publication.coverage.request_sha256) == (publication.coverage,)
    assert not crashed_temp.exists()
