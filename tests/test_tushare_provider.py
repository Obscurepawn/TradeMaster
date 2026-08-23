from __future__ import annotations

import hashlib
import json
import os
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any, cast

import pyarrow as pa
import pyarrow.parquet as pq
import pytest
from trademaster.data import (
    CacheFirstDataPortal,
    DataConfig,
    DuckDbCatalog,
    ObjectIntegrityError,
    ParquetObjectStore,
    TushareCredentialError,
    TushareDataError,
    TushareProvider,
    default_dataset_registry,
    normalize_request,
)


class FakeTushareClient:
    def __init__(self, responses: dict[str, list[dict[str, object]]]) -> None:
        self.responses = responses
        self.calls: list[tuple[str, dict[str, str], str]] = []

    def query(self, api_name: str, *, fields: str, **params: str) -> Any:
        self.calls.append((api_name, params, fields))
        return self.responses[api_name]


def _config(
    tmp_path: Path, *, etf_instruments: tuple[str, ...] = ()
) -> DataConfig:
    config = DataConfig(
        data_root=tmp_path / "data",
        log_dir=tmp_path / "logs",
        etf_instruments=etf_instruments,
    )
    config.paths.ensure_layout()
    return config


def _write_content_addressed(table: pa.Table, directory: Path) -> str:
    directory.mkdir(parents=True, exist_ok=True)
    temporary = directory / "evidence.parquet"
    pq.write_table(table, temporary)
    digest = hashlib.sha256(temporary.read_bytes()).hexdigest()
    temporary.rename(directory / f"{digest}.parquet")
    return digest


def test_tushare_factory_reads_only_named_environment_secret(tmp_path: Path) -> None:
    config = _config(tmp_path)
    called = False

    def client_factory(_: str) -> FakeTushareClient:
        nonlocal called
        called = True
        return FakeTushareClient({})

    with pytest.raises(TushareCredentialError, match="TUSHARE_TOKEN"):
        TushareProvider.from_environment(
            config=config,
            registry=default_dataset_registry(),
            clock=lambda: datetime(2025, 1, 4, tzinfo=UTC),
            environment={},
            client_factory=client_factory,
        )
    assert not called


def test_trade_calendar_normalization_persists_raw_and_staging(tmp_path: Path) -> None:
    config = _config(tmp_path)
    client = FakeTushareClient(
        {
            "trade_cal": [
                {
                    "exchange": "SSE",
                    "cal_date": "20250102",
                    "is_open": 1,
                    "pretrade_date": "20241231",
                }
            ]
        }
    )
    provider = TushareProvider(
        client=client,
        paths=config.paths,
        registry=default_dataset_registry(),
        clock=lambda: datetime(2025, 1, 2, 8, tzinfo=UTC),
    )
    request = normalize_request(
        default_dataset_registry(),
        dataset="trade_calendar",
        start=datetime(2025, 1, 2, tzinfo=UTC),
        end=datetime(2025, 1, 2, 23, 59, tzinfo=UTC),
        fields=("is_open",),
        coverage_keys=("SSE:2025-01-02",),
    )

    page = provider.fetch(
        request, coverage_keys=request.coverage_keys, page_token=None
    )
    assert page.coverage_keys == request.coverage_keys
    assert page.partition_key == "venue=SSE/session_year=2025"
    assert page.next_page_token is None
    assert page.table.to_pylist() == [
        {
            "venue": "SSE",
            "session_date": date(2025, 1, 2),
            "is_open": True,
            "open_at": datetime(2025, 1, 2, 1, 30, tzinfo=UTC),
            "close_at": datetime(2025, 1, 2, 7, tzinfo=UTC),
            "event_time": datetime(2025, 1, 2, 7, tzinfo=UTC),
            "known_at": datetime(2025, 1, 2, 8, tzinfo=UTC),
            "source_revision": page.table["source_revision"][0].as_py(),
        }
    ]
    assert client.calls[0][0:2] == (
        "trade_cal",
        {
            "exchange": "SSE",
            "start_date": "20250102",
            "end_date": "20250102",
            "limit": "6000",
            "offset": "0",
        },
    )
    assert len(tuple(config.paths.raw.rglob("*.parquet"))) == 1
    assert len(tuple(config.paths.staging.rglob("*.parquet"))) == 1
    raw_metadata = pq.read_schema(next(config.paths.raw.rglob("*.parquet"))).metadata or {}
    staging_metadata = (
        pq.read_schema(next(config.paths.staging.rglob("*.parquet"))).metadata or {}
    )
    raw_provenance = json.loads(raw_metadata[b"trademaster.provider_request/v1"])
    staging_provenance = json.loads(staging_metadata[b"trademaster.staging/v1"])
    assert {
        "fields",
        "provider_request_sha256",
        "page_cursor",
        "page_index",
        "returned_rows",
        "row_source_revisions",
    } <= set(raw_provenance)
    assert {"request_sha256", "coverage_key", "source_revision_sha256s"} <= set(
        staging_provenance
    )
    assert staging_provenance["raw_object_sha256s"] == [
        path.stem for path in sorted(config.paths.raw.rglob("*.parquet"))
    ]
    publication = ParquetObjectStore(
        config.paths, default_dataset_registry()
    ).publish(
        request,
        page.table,
        partition_key=page.partition_key,
        created_at=datetime(2025, 1, 2, 9, tzinfo=UTC),
        upstream_object_sha256s=page.upstream_object_sha256s,
    )
    canonical_metadata = pq.read_schema(publication.path).metadata or {}
    canonical_provenance = json.loads(canonical_metadata[b"trademaster.object/v1"])
    assert canonical_provenance["upstream_object_sha256s"] == list(
        page.upstream_object_sha256s
    )


def test_object_store_replays_raw_trade_calendar_semantics_before_publish(
    tmp_path: Path,
) -> None:
    config = _config(tmp_path)
    registry = default_dataset_registry()
    provider = TushareProvider(
        client=FakeTushareClient(
            {
                "trade_cal": [
                    {
                        "exchange": "SSE",
                        "cal_date": "20250102",
                        "is_open": 0,
                        "pretrade_date": "20241231",
                    }
                ]
            }
        ),
        paths=config.paths,
        registry=registry,
        clock=lambda: datetime(2025, 1, 2, 8, tzinfo=UTC),
    )
    request = normalize_request(
        registry,
        dataset="trade_calendar",
        start=datetime(2025, 1, 2, tzinfo=UTC),
        end=datetime(2025, 1, 2, 23, 59, tzinfo=UTC),
        fields=("is_open",),
        coverage_keys=("SSE:2025-01-02",),
    )
    page = provider.fetch(
        request, coverage_keys=request.coverage_keys, page_token=None
    )
    forged = page.table.set_column(
        page.table.column_names.index("is_open"),
        page.table.schema.field("is_open"),
        pa.array([True], type=pa.bool_()),
    )
    original_staging = next(config.paths.staging.rglob("*.parquet"))
    forged_staging = forged.replace_schema_metadata(
        pq.read_schema(original_staging).metadata
    )
    staging_dir = config.paths.staging / "trade_calendar" / "forged"
    staging_dir.mkdir(parents=True)
    temporary = staging_dir / "forged.parquet"
    pq.write_table(forged_staging, temporary)
    staging_sha256 = hashlib.sha256(temporary.read_bytes()).hexdigest()
    temporary.rename(staging_dir / f"{staging_sha256}.parquet")
    raw_sha256s = tuple(path.stem for path in config.paths.raw.rglob("*.parquet"))

    with pytest.raises(ObjectIntegrityError, match="raw normalization"):
        ParquetObjectStore(config.paths, registry).publish(
            request,
            forged,
            partition_key=page.partition_key,
            created_at=datetime(2025, 1, 2, 9, tzinfo=UTC),
            upstream_object_sha256s=tuple(
                sorted((*raw_sha256s, staging_sha256))
            ),
        )


def test_object_store_rejects_staging_time_before_referenced_raw_ingestion(
    tmp_path: Path,
) -> None:
    config = _config(tmp_path)
    registry = default_dataset_registry()
    actual_ingestion = datetime(2025, 3, 2, tzinfo=UTC)
    forged_known_at = datetime(2025, 1, 2, 7, tzinfo=UTC)
    provider = TushareProvider(
        client=FakeTushareClient(
            {
                "trade_cal": [
                    {
                        "exchange": "SSE",
                        "cal_date": "20250102",
                        "is_open": 1,
                        "pretrade_date": "20241231",
                    }
                ]
            }
        ),
        paths=config.paths,
        registry=registry,
        clock=lambda: actual_ingestion,
    )
    request = normalize_request(
        registry,
        dataset="trade_calendar",
        start=datetime(2025, 1, 2, tzinfo=UTC),
        end=datetime(2025, 1, 2, 23, 59, tzinfo=UTC),
        fields=("is_open",),
        coverage_keys=("SSE:2025-01-02",),
    )
    page = provider.fetch(request, coverage_keys=request.coverage_keys, page_token=None)
    forged = page.table.set_column(
        page.table.column_names.index("known_at"),
        page.table.schema.field("known_at"),
        pa.array([forged_known_at], type=pa.timestamp("us", tz="UTC")),
    )
    staging_metadata = dict(
        pq.read_schema(next(config.paths.staging.rglob("*.parquet"))).metadata or {}
    )
    staging_payload = json.loads(staging_metadata[b"trademaster.staging/v1"])
    staging_payload["normalized_at"] = forged_known_at.isoformat()
    staging_metadata[b"trademaster.staging/v1"] = json.dumps(
        staging_payload, sort_keys=True, separators=(",", ":")
    ).encode()
    staging_sha256 = _write_content_addressed(
        forged.replace_schema_metadata(staging_metadata),
        config.paths.staging / "trade_calendar" / "forged-time",
    )
    raw_sha256s = tuple(path.stem for path in config.paths.raw.rglob("*.parquet"))
    with pytest.raises(ObjectIntegrityError, match="raw ingestion time"):
        ParquetObjectStore(config.paths, registry).publish(
            request,
            forged,
            partition_key=page.partition_key,
            created_at=actual_ingestion,
            upstream_object_sha256s=tuple(sorted((*raw_sha256s, staging_sha256))),
        )


def test_object_store_binds_raw_provider_params_to_partial_canonical_request(
    tmp_path: Path,
) -> None:
    config = _config(tmp_path)
    registry = default_dataset_registry()
    provider = TushareProvider(
        client=FakeTushareClient(
            {
                "trade_cal": [
                    {
                        "exchange": "SSE",
                        "cal_date": "20250102",
                        "is_open": 1,
                        "pretrade_date": "20241231",
                    }
                ]
            }
        ),
        paths=config.paths,
        registry=registry,
        clock=lambda: datetime(2025, 1, 3, 9, tzinfo=UTC),
    )
    request = normalize_request(
        registry,
        dataset="trade_calendar",
        start=datetime(2025, 1, 2, tzinfo=UTC),
        end=datetime(2025, 1, 2, 23, 59, tzinfo=UTC),
        fields=("is_open",),
        coverage_keys=("SSE:2025-01-02",),
    )
    page = provider.fetch(request, coverage_keys=request.coverage_keys, page_token=None)
    raw_table = pq.read_table(next(config.paths.raw.rglob("*.parquet")))
    raw_metadata = dict(raw_table.schema.metadata or {})
    raw_payload = json.loads(raw_metadata[b"trademaster.provider_request/v1"])
    raw_payload["params"] = {
        "exchange": "SSE",
        "start_date": "20990101",
        "end_date": "20990101",
    }
    request_payload = {
        key: raw_payload[key] for key in ("endpoint", "fields", "params")
    }
    raw_payload["provider_request_sha256"] = hashlib.sha256(
        json.dumps(
            request_payload, sort_keys=True, separators=(",", ":")
        ).encode()
    ).hexdigest()
    raw_metadata[b"trademaster.provider_request/v1"] = json.dumps(
        raw_payload, sort_keys=True, separators=(",", ":")
    ).encode()
    raw_sha256 = _write_content_addressed(
        raw_table.replace_schema_metadata(raw_metadata),
        config.paths.raw / "trade_cal" / "ingest_date=2025-01-03",
    )
    staging_table = pq.read_table(next(config.paths.staging.rglob("*.parquet")))
    staging_metadata = dict(staging_table.schema.metadata or {})
    staging_payload = json.loads(staging_metadata[b"trademaster.staging/v1"])
    staging_payload["raw_object_sha256s"] = [raw_sha256]
    staging_metadata[b"trademaster.staging/v1"] = json.dumps(
        staging_payload, sort_keys=True, separators=(",", ":")
    ).encode()
    staging_sha256 = _write_content_addressed(
        staging_table.replace_schema_metadata(staging_metadata),
        config.paths.staging / "trade_calendar" / "forged-request",
    )
    with pytest.raises(ObjectIntegrityError, match="raw provider request scope"):
        ParquetObjectStore(config.paths, registry).publish(
            request,
            page.table,
            partition_key=page.partition_key,
            created_at=datetime(2025, 1, 3, 10, tzinfo=UTC),
            upstream_object_sha256s=tuple(sorted((raw_sha256, staging_sha256))),
        )


def test_daily_bars_normalization_filters_venue_and_advances_key_pages(
    tmp_path: Path,
) -> None:
    config = _config(tmp_path)
    client = FakeTushareClient(
        {
            "daily": [
                {
                    "ts_code": "600000.SH",
                    "trade_date": "20250102",
                    "open": 10.0,
                    "high": 11.0,
                    "low": 9.5,
                    "close": 10.5,
                    "vol": 1000.0,
                    "amount": 10500.0,
                    "pre_close": 9.9,
                },
                {
                    "ts_code": "000001.SZ",
                    "trade_date": "20250102",
                    "open": 12.0,
                    "high": 12.5,
                    "low": 11.8,
                    "close": 12.2,
                    "vol": 2000.0,
                    "amount": 24400.0,
                    "pre_close": 12.0,
                },
            ]
        }
    )
    provider = TushareProvider(
        client=client,
        paths=config.paths,
        registry=default_dataset_registry(),
        clock=lambda: datetime(2030, 1, 2, 8, tzinfo=UTC),
    )
    request = normalize_request(
        default_dataset_registry(),
        dataset="daily_bars",
        start=datetime(2025, 1, 2, tzinfo=UTC),
        end=datetime(2025, 1, 3, 23, 59, tzinfo=UTC),
        fields=("close",),
        coverage_keys=("SSE:2025-01-02", "SSE:2025-01-03"),
    )

    first = provider.fetch(
        request, coverage_keys=request.coverage_keys, page_token=None
    )
    assert first.next_page_token == "1"
    assert first.table["instrument_id"].to_pylist() == ["600000.SH"]
    assert first.table["venue"].to_pylist() == ["SSE"]
    assert first.table["known_at"].to_pylist() == [
        datetime(2025, 1, 2, 8, tzinfo=UTC)
    ]


def test_tushare_empty_response_fails_closed(tmp_path: Path) -> None:
    config = _config(tmp_path)
    provider = TushareProvider(
        client=FakeTushareClient({"trade_cal": []}),
        paths=config.paths,
        registry=default_dataset_registry(),
        clock=lambda: datetime(2025, 1, 2, 8, tzinfo=UTC),
    )
    request = normalize_request(
        default_dataset_registry(),
        dataset="trade_calendar",
        start=datetime(2025, 1, 2, tzinfo=UTC),
        end=datetime(2025, 1, 2, 23, 59, tzinfo=UTC),
        fields=("is_open",),
        coverage_keys=("SSE:2025-01-02",),
    )
    with pytest.raises(TushareDataError, match="empty response"):
        provider.fetch(request, coverage_keys=request.coverage_keys, page_token=None)


@pytest.mark.parametrize(
    ("dataset", "start", "end", "coverage_key", "field"),
    [
        ("daily_limits_status", "2025-01-02", "2025-01-02", "SSE:2025-01-02", "up_limit"),
        ("adj_factors", "2025-01-02", "2025-01-02", "SSE:2025-01-02", "adj_factor"),
        ("daily_basic", "2025-01-02", "2025-01-02", "SSE:2025-01-02", "turnover_rate"),
        ("index_bars", "2025-01-02", "2025-01-02", "SSE:2025-01-02", "close"),
        (
            "instrument_master",
            "1999-11-10",
            "1999-11-10",
            "600000.SH:1999-11-10",
            "asset_class",
        ),
        (
            "index_membership",
            "2025-01-02",
            "2025-01-02",
            "000300.SH:2025-01-02",
            "weight",
        ),
        (
            "industry_membership",
            "2020-01-01",
            "2020-01-01",
            "SW2021:2020-01-01",
            "industry_id",
        ),
        (
            "financial_indicators",
            "2024-12-31",
            "2024-12-31",
            "600000.SH:2024-12-31:2025-03-01",
            "report_values_json",
        ),
    ],
)
def test_every_v1_registry_dataset_has_a_tushare_normalizer_and_publishable_schema(
    tmp_path: Path,
    dataset: str,
    start: str,
    end: str,
    coverage_key: str,
    field: str,
) -> None:
    config = _config(tmp_path)
    responses: dict[str, list[dict[str, object]]] = {
        "stk_limit": [
            {
                "ts_code": "600000.SH",
                "trade_date": "20250102",
                "up_limit": 11.0,
                "down_limit": 9.0,
            }
        ],
        "suspend_d": [],
        "stock_st": [],
        "adj_factor": [
            {"ts_code": "600000.SH", "trade_date": "20250102", "adj_factor": 1.2}
        ],
        "daily_basic": [
            {
                "ts_code": "600000.SH",
                "trade_date": "20250102",
                "turnover_rate": 1.5,
                "total_mv": 1000.0,
                "circ_mv": 800.0,
            }
        ],
        "index_daily": [
            {
                "ts_code": "000001.SH",
                "trade_date": "20250102",
                "open": 3000.0,
                "high": 3100.0,
                "low": 2990.0,
                "close": 3050.0,
                "vol": 100.0,
                "amount": 200.0,
            }
        ],
        "stock_basic": [
            {
                "ts_code": "600000.SH",
                "exchange": "SSE",
                "market": "主板",
                "curr_type": "CNY",
                "list_date": "19991110",
                "delist_date": None,
            }
        ],
        "index_weight": [
            {
                "index_code": "000300.SH",
                "con_code": "600000.SH",
                "trade_date": "20250102",
                "weight": 1.1,
            }
        ],
        "index_member_all": [
            {
                "l1_code": "801000.SI",
                "l2_code": "801010.SI",
                "l3_code": "850111.SI",
                "ts_code": "600000.SH",
                "in_date": "20200101",
                "out_date": None,
            }
        ],
        "fina_indicator": [
            {
                "ts_code": "600000.SH",
                "ann_date": "20250301",
                "end_date": "20241231",
                "eps": 1.23,
                "update_flag": "1",
            }
        ],
    }
    registry = default_dataset_registry()
    provider = TushareProvider(
        client=FakeTushareClient(responses),
        paths=config.paths,
        registry=registry,
        clock=lambda: datetime(2025, 3, 2, tzinfo=UTC),
    )
    request = normalize_request(
        registry,
        dataset=dataset,
        start=datetime.fromisoformat(start).replace(tzinfo=UTC),
        end=datetime.fromisoformat(end).replace(hour=23, minute=59, tzinfo=UTC),
        instruments=("000001.SH",) if dataset == "index_bars" else ("600000.SH",),
        fields=(field,),
        coverage_keys=(coverage_key,),
    )
    page = provider.fetch(
        request, coverage_keys=request.coverage_keys, page_token=None
    )
    publication = ParquetObjectStore(config.paths, registry).publish(
        request,
        page.table,
        partition_key=page.partition_key,
        created_at=datetime(2025, 3, 2, tzinfo=UTC),
        upstream_object_sha256s=page.upstream_object_sha256s,
    )
    assert set(registry[dataset].required_fields) <= set(page.table.column_names)
    assert publication.object.coverage_keys == (coverage_key,)
    if dataset == "index_bars":
        assert any(
            params.get("ts_code") == "000001.SH"
            for endpoint, params, _ in cast(
                FakeTushareClient, provider.client
            ).calls
            if endpoint == "index_daily"
        )
    if dataset == "industry_membership":
        assert page.table["instrument_id"].to_pylist() == ["600000.SH"]
        assert page.table["industry_id"].to_pylist() == ["850111.SI"]
        assert page.table["effective_from"].to_pylist() == [date(2020, 1, 1)]
    if dataset == "index_membership":
        assert page.table["effective_to"].to_pylist() == [date(2025, 1, 2)]
        assert page.table["known_at"].to_pylist() == [
            datetime(2025, 3, 2, tzinfo=UTC)
        ]
    assert page.upstream_object_sha256s


def test_instrument_master_does_not_backdate_current_delist_information(
    tmp_path: Path,
) -> None:
    config = _config(tmp_path)
    client = FakeTushareClient(
        {
            "stock_basic": [
                {
                    "ts_code": "600000.SH",
                    "exchange": "SSE",
                    "market": "主板",
                    "curr_type": "CNY",
                    "list_date": "20000101",
                    "delist_date": "20240101",
                }
            ]
        }
    )
    registry = default_dataset_registry()
    provider = TushareProvider(
        client=client,
        paths=config.paths,
        registry=registry,
        clock=lambda: datetime(2025, 1, 1, tzinfo=UTC),
    )
    request = normalize_request(
        registry,
        dataset="instrument_master",
        start=datetime(2000, 1, 1, tzinfo=UTC),
        end=datetime(2000, 1, 1, 23, 59, tzinfo=UTC),
        instruments=("600000.SH",),
        fields=("delist_date",),
        coverage_keys=("600000.SH:2000-01-01",),
    )
    page = provider.fetch(
        request, coverage_keys=request.coverage_keys, page_token=None
    )
    assert page.table["delist_date"].to_pylist() == [date(9999, 12, 31)]
    assert page.table["known_at"].to_pylist() == [datetime(2000, 1, 1, tzinfo=UTC)]
    assert {params["list_status"] for _, params, _ in client.calls} == {
        "L",
        "D",
        "P",
        "G",
    }


def test_instrument_master_emits_a_distinct_delisting_event(tmp_path: Path) -> None:
    config = _config(tmp_path)
    row: dict[str, object] = {
        "ts_code": "600000.SH",
        "exchange": "SSE",
        "market": "主板",
        "curr_type": "CNY",
        "list_date": "20000101",
        "delist_date": "20240101",
    }
    registry = default_dataset_registry()
    provider = TushareProvider(
        client=FakeTushareClient({"stock_basic": [row]}),
        paths=config.paths,
        registry=registry,
        clock=lambda: datetime(2025, 1, 1, tzinfo=UTC),
    )
    request = normalize_request(
        registry,
        dataset="instrument_master",
        start=datetime(2024, 1, 1, tzinfo=UTC),
        end=datetime(2024, 1, 1, 23, 59, tzinfo=UTC),
        instruments=("600000.SH",),
        fields=("delist_date",),
        coverage_keys=("600000.SH:2024-01-01",),
    )
    page = provider.fetch(request, coverage_keys=request.coverage_keys, page_token=None)
    assert page.table["effective_from"].to_pylist() == [date(2024, 1, 1)]
    assert page.table["list_date"].to_pylist() == [date(2000, 1, 1)]
    assert page.table["delist_date"].to_pylist() == [date(2024, 1, 1)]
    assert page.table["known_at"].to_pylist() == [datetime(2024, 1, 1, tzinfo=UTC)]


@pytest.mark.parametrize(
    "coverage_key",
    ("FAKE_TAXONOMY:2020-01-01", "SW2021:2025-01-02"),
)
def test_industry_membership_binds_taxonomy_and_provider_in_date(
    tmp_path: Path, coverage_key: str
) -> None:
    config = _config(tmp_path)
    registry = default_dataset_registry()
    provider = TushareProvider(
        client=FakeTushareClient(
            {
                "index_member_all": [
                    {
                        "l1_code": "801000.SI",
                        "l2_code": "801010.SI",
                        "l3_code": "850111.SI",
                        "ts_code": "600000.SH",
                        "in_date": "20200101",
                        "out_date": None,
                    }
                ]
            }
        ),
        paths=config.paths,
        registry=registry,
        clock=lambda: datetime(2025, 1, 1, tzinfo=UTC),
    )
    requested_date = coverage_key.rsplit(":", 1)[1]
    request = normalize_request(
        registry,
        dataset="industry_membership",
        start=datetime.fromisoformat(requested_date).replace(tzinfo=UTC),
        end=datetime.fromisoformat(requested_date).replace(hour=23, minute=59, tzinfo=UTC),
        instruments=("600000.SH",),
        fields=("industry_id",),
        coverage_keys=(coverage_key,),
    )
    with pytest.raises(TushareDataError, match="taxonomy|effective date"):
        provider.fetch(request, coverage_keys=request.coverage_keys, page_token=None)


def test_resume_rows_are_not_marked_as_suspended(tmp_path: Path) -> None:
    config = _config(tmp_path)
    client = FakeTushareClient(
        {
            "stk_limit": [
                {
                    "ts_code": "600000.SH",
                    "trade_date": "20250102",
                    "up_limit": 11.0,
                    "down_limit": 9.0,
                }
            ],
            "suspend_d": [
                {
                    "ts_code": "600000.SH",
                    "trade_date": "20250102",
                    "suspend_type": "R",
                }
            ],
            "stock_st": [],
        }
    )
    registry = default_dataset_registry()
    provider = TushareProvider(
        client=client,
        paths=config.paths,
        registry=registry,
        clock=lambda: datetime(2025, 1, 2, 8, tzinfo=UTC),
    )
    request = normalize_request(
        registry,
        dataset="daily_limits_status",
        start=datetime(2025, 1, 2, tzinfo=UTC),
        end=datetime(2025, 1, 2, 23, 59, tzinfo=UTC),
        instruments=("600000.SH",),
        fields=("suspended",),
        coverage_keys=("SSE:2025-01-02",),
    )
    page = provider.fetch(
        request, coverage_keys=request.coverage_keys, page_token=None
    )
    assert page.table["suspended"].to_pylist() == [False]
    assert page.table["known_at"].to_pylist() == [
        datetime(2025, 1, 2, 1, 20, tzinfo=UTC)
    ]
    suspend_call = next(call for call in client.calls if call[0] == "suspend_d")
    assert suspend_call[1]["suspend_type"] == "S"


def test_auxiliary_status_rows_from_another_date_are_ignored(tmp_path: Path) -> None:
    config = _config(tmp_path)
    client = FakeTushareClient(
        {
            "stk_limit": [
                {
                    "ts_code": "600000.SH",
                    "trade_date": "20250102",
                    "up_limit": 11.0,
                    "down_limit": 9.0,
                }
            ],
            "suspend_d": [
                {
                    "ts_code": "600000.SH",
                    "trade_date": "20250103",
                    "suspend_type": "S",
                }
            ],
            "stock_st": [
                {
                    "ts_code": "600000.SH",
                    "trade_date": "20250103",
                    "type": "ST",
                }
            ],
        }
    )
    registry = default_dataset_registry()
    provider = TushareProvider(
        client=client,
        paths=config.paths,
        registry=registry,
        clock=lambda: datetime(2025, 1, 2, 8, tzinfo=UTC),
    )
    request = normalize_request(
        registry,
        dataset="daily_limits_status",
        start=datetime(2025, 1, 2, tzinfo=UTC),
        end=datetime(2025, 1, 2, 23, 59, tzinfo=UTC),
        instruments=("600000.SH",),
        fields=("suspended", "is_st"),
        coverage_keys=("SSE:2025-01-02",),
    )
    page = provider.fetch(request, coverage_keys=request.coverage_keys, page_token=None)
    assert page.table["suspended"].to_pylist() == [False]
    assert page.table["is_st"].to_pylist() == [False]


def test_endpoint_row_pagination_is_independent_from_coverage_key_pagination(
    tmp_path: Path,
) -> None:
    class PagingClient:
        def __init__(self) -> None:
            self.offsets: list[str] = []

        def query(self, api_name: str, *, fields: str, **params: str) -> Any:
            assert api_name == "daily"
            self.offsets.append(params["offset"])
            offset = int(params["offset"])
            count = 6000 if offset == 0 else 1
            return [
                {
                    "ts_code": f"{offset + index:06d}.SH",
                    "trade_date": "20250102",
                    "open": 10.0,
                    "high": 11.0,
                    "low": 9.0,
                    "close": 10.5,
                    "vol": 1000.0,
                    "amount": 10500.0,
                    "pre_close": 9.9,
                }
                for index in range(count)
            ]

    config = _config(tmp_path)
    client = PagingClient()
    registry = default_dataset_registry()
    provider = TushareProvider(
        client=client,
        paths=config.paths,
        registry=registry,
        clock=lambda: datetime(2025, 1, 2, 8, tzinfo=UTC),
    )
    request = normalize_request(
        registry,
        dataset="daily_bars",
        start=datetime(2025, 1, 2, tzinfo=UTC),
        end=datetime(2025, 1, 2, 23, 59, tzinfo=UTC),
        fields=("close",),
        coverage_keys=("SSE:2025-01-02",),
    )
    page = provider.fetch(
        request, coverage_keys=request.coverage_keys, page_token=None
    )
    assert page.table.num_rows == 6001
    assert client.offsets == ["0", "6000"]


def test_configured_etf_uses_etf_basic_and_fund_daily(tmp_path: Path) -> None:
    config = _config(tmp_path, etf_instruments=("510300.SH",))
    client = FakeTushareClient(
        {
            "etf_basic": [
                {
                    "ts_code": "510300.SH",
                    "exchange": "SH",
                    "list_date": "20120528",
                    "list_status": "L",
                }
            ],
            "fund_daily": [
                {
                    "ts_code": "510300.SH",
                    "trade_date": "20250102",
                    "open": 4.0,
                    "high": 4.1,
                    "low": 3.9,
                    "close": 4.05,
                    "vol": 1000.0,
                    "amount": 4050.0,
                    "pre_close": 4.0,
                }
            ],
        }
    )
    registry = default_dataset_registry()
    provider = TushareProvider(
        client=client,
        paths=config.paths,
        registry=registry,
        clock=lambda: datetime(2025, 1, 3, tzinfo=UTC),
    )
    master_request = normalize_request(
        registry,
        dataset="instrument_master",
        start=datetime(2012, 5, 28, tzinfo=UTC),
        end=datetime(2012, 5, 28, 23, 59, tzinfo=UTC),
        instruments=("510300.SH",),
        fields=("asset_class",),
        coverage_keys=("510300.SH:2012-05-28",),
    )
    master = provider.fetch(
        master_request, coverage_keys=master_request.coverage_keys, page_token=None
    )
    assert master.table["asset_class"].to_pylist() == ["etf"]
    assert master.table["venue"].to_pylist() == ["SSE"]
    assert master.partition_key == "dataset_scope=all"

    bars_request = normalize_request(
        registry,
        dataset="daily_bars",
        start=datetime(2025, 1, 2, tzinfo=UTC),
        end=datetime(2025, 1, 2, 23, 59, tzinfo=UTC),
        instruments=("510300.SH",),
        fields=("close",),
        coverage_keys=("SSE:2025-01-02",),
    )
    bars = provider.fetch(
        bars_request, coverage_keys=bars_request.coverage_keys, page_token=None
    )
    assert bars.table["instrument_id"].to_pylist() == ["510300.SH"]
    assert {endpoint for endpoint, _, _ in client.calls} == {
        "etf_basic",
        "fund_daily",
    }
    unsupported = bars_request.model_copy(
        update={"dataset": "adj_factors", "fields": ("adj_factor",)}
    )
    with pytest.raises(TushareDataError, match="ETF provider contract"):
        provider.fetch(
            unsupported,
            coverage_keys=unsupported.coverage_keys,
            page_token=None,
        )


def test_object_store_rejects_stock_endpoint_evidence_for_configured_etf(
    tmp_path: Path,
) -> None:
    registry = default_dataset_registry()
    unclassified = _config(tmp_path)
    provider = TushareProvider(
        client=FakeTushareClient(
            {
                "daily": [
                    {
                        "ts_code": "510300.SH",
                        "trade_date": "20250102",
                        "open": 4.0,
                        "high": 4.1,
                        "low": 3.9,
                        "close": 4.05,
                        "vol": 1000.0,
                        "amount": 4050.0,
                        "pre_close": 4.0,
                    }
                ]
            }
        ),
        paths=unclassified.paths,
        registry=registry,
        clock=lambda: datetime(2025, 1, 3, tzinfo=UTC),
    )
    request = normalize_request(
        registry,
        dataset="daily_bars",
        start=datetime(2025, 1, 2, tzinfo=UTC),
        end=datetime(2025, 1, 2, 23, 59, tzinfo=UTC),
        instruments=("510300.SH",),
        fields=("close",),
        coverage_keys=("SSE:2025-01-02",),
    )
    page = provider.fetch(
        request, coverage_keys=request.coverage_keys, page_token=None
    )
    authoritative = DataConfig(
        data_root=unclassified.data_root,
        log_dir=unclassified.log_dir,
        etf_instruments=("510300.SH",),
    )

    with pytest.raises(ObjectIntegrityError, match="ETF identity"):
        ParquetObjectStore(authoritative.paths, registry).publish(
            request,
            page.table,
            partition_key=page.partition_key,
            created_at=datetime(2025, 1, 3, 1, tzinfo=UTC),
            upstream_object_sha256s=page.upstream_object_sha256s,
        )


def test_object_store_rejects_unsupported_dataset_evidence_for_configured_etf(
    tmp_path: Path,
) -> None:
    registry = default_dataset_registry()
    unclassified = _config(tmp_path)
    provider = TushareProvider(
        client=FakeTushareClient(
            {
                "daily_basic": [
                    {
                        "ts_code": "510300.SH",
                        "trade_date": "20250102",
                        "turnover_rate": 1.5,
                        "total_mv": 1000.0,
                        "circ_mv": 800.0,
                    }
                ]
            }
        ),
        paths=unclassified.paths,
        registry=registry,
        clock=lambda: datetime(2025, 1, 3, tzinfo=UTC),
    )
    request = normalize_request(
        registry,
        dataset="daily_basic",
        start=datetime(2025, 1, 2, tzinfo=UTC),
        end=datetime(2025, 1, 2, 23, 59, tzinfo=UTC),
        instruments=("510300.SH",),
        fields=("turnover_rate",),
        coverage_keys=("SSE:2025-01-02",),
    )
    page = provider.fetch(
        request, coverage_keys=request.coverage_keys, page_token=None
    )
    authoritative = DataConfig(
        data_root=unclassified.data_root,
        log_dir=unclassified.log_dir,
        etf_instruments=("510300.SH",),
    )

    with pytest.raises(ObjectIntegrityError, match="unsupported ETF dataset"):
        ParquetObjectStore(authoritative.paths, registry).publish(
            request,
            page.table,
            partition_key=page.partition_key,
            created_at=datetime(2025, 1, 3, 1, tzinfo=UTC),
            upstream_object_sha256s=page.upstream_object_sha256s,
        )


def test_complete_etf_bars_work_through_portal_and_snapshot_without_stock_status(
    tmp_path: Path,
) -> None:
    config = _config(tmp_path, etf_instruments=("510300.SH",))
    registry = default_dataset_registry()
    client = FakeTushareClient(
        {
            "fund_daily": [
                {
                    "ts_code": "510300.SH",
                    "trade_date": "20250102",
                    "open": 4.0,
                    "high": 4.1,
                    "low": 3.9,
                    "close": 4.05,
                    "vol": 1000.0,
                    "amount": 4050.0,
                    "pre_close": 4.0,
                }
            ]
        }
    )
    provider = TushareProvider(
        client=client,
        paths=config.paths,
        registry=registry,
        clock=lambda: datetime(2025, 1, 3, tzinfo=UTC),
    )
    catalog = DuckDbCatalog(config.paths.catalog)
    portal = CacheFirstDataPortal(
        catalog=catalog,
        store=ParquetObjectStore(config.paths, registry),
        registry=registry,
        provider=provider,
        clock=lambda: datetime(2025, 1, 3, tzinfo=UTC),
    )
    request = normalize_request(
        registry,
        dataset="daily_bars",
        start=datetime(2025, 1, 2, tzinfo=UTC),
        end=datetime(2025, 1, 2, 23, 59, tzinfo=UTC),
        instruments=("510300.SH",),
        fields=("close",),
        coverage_keys=("SSE:2025-01-02",),
    )
    try:
        result = portal.query(request, as_of=datetime(2025, 1, 3, tzinfo=UTC))
        assert result["instrument_id"].to_pylist() == ["510300.SH"]
        assert {endpoint for endpoint, _, _ in client.calls} == {"fund_daily"}
        assert catalog.objects("daily_limits_status") == ()
    finally:
        catalog.close()


def test_etf_master_rejects_unknown_or_suffix_mismatched_exchange(
    tmp_path: Path,
) -> None:
    config = _config(tmp_path, etf_instruments=("510300.SH",))
    registry = default_dataset_registry()
    request = normalize_request(
        registry,
        dataset="instrument_master",
        start=datetime(2012, 5, 28, tzinfo=UTC),
        end=datetime(2012, 5, 28, 23, 59, tzinfo=UTC),
        instruments=("510300.SH",),
        fields=("asset_class",),
        coverage_keys=("510300.SH:2012-05-28",),
    )

    for exchange in ("NYSE", "SZ"):
        provider = TushareProvider(
            client=FakeTushareClient(
                {
                    "etf_basic": [
                        {
                            "ts_code": "510300.SH",
                            "exchange": exchange,
                            "list_date": "20120528",
                            "list_status": "L",
                        }
                    ]
                }
            ),
            paths=config.paths,
            registry=registry,
            clock=lambda: datetime(2025, 1, 3, tzinfo=UTC),
        )
        with pytest.raises(TushareDataError, match="ETF exchange"):
            provider.fetch(
                request, coverage_keys=request.coverage_keys, page_token=None
            )


def test_mutable_empty_endpoint_cache_chooses_latest_revision_without_conflict(
    tmp_path: Path,
) -> None:
    config = _config(tmp_path)
    registry = default_dataset_registry()
    request = normalize_request(
        registry,
        dataset="daily_limits_status",
        start=datetime(2025, 1, 2, tzinfo=UTC),
        end=datetime(2025, 1, 2, 23, 59, tzinfo=UTC),
        instruments=("600000.SH",),
        fields=("suspended",),
        coverage_keys=("SSE:2025-01-02",),
    )
    limit = {
        "ts_code": "600000.SH",
        "trade_date": "20250102",
        "up_limit": 11.0,
        "down_limit": 9.0,
    }
    suspension: dict[str, object] = {
        "ts_code": "600000.SH",
        "trade_date": "20250102",
        "suspend_type": "S",
    }

    def fetch_at(day: int, suspension_rows: list[dict[str, object]]) -> bool:
        provider = TushareProvider(
            client=FakeTushareClient(
                {
                    "stk_limit": [limit],
                    "suspend_d": suspension_rows,
                    "stock_st": [],
                }
            ),
            paths=config.paths,
            registry=registry,
            clock=lambda: datetime(2025, 1, day, 8, tzinfo=UTC),
        )
        page = provider.fetch(
            request, coverage_keys=request.coverage_keys, page_token=None
        )
        return bool(page.table["suspended"][0].as_py())

    assert fetch_at(2, []) is False
    assert fetch_at(3, [suspension]) is True
    assert fetch_at(4, [suspension]) is True


def test_mutable_endpoint_refreshes_every_cached_page_before_normalizing(
    tmp_path: Path,
) -> None:
    target = "000001.SZ"
    first_page: list[dict[str, object]] = [
        {
            "ts_code": f"{index:06d}.SZ",
            "trade_date": "20250102",
            "suspend_type": "S",
        }
        for index in range(1, 5001)
    ]
    corrected_page: list[dict[str, object]] = [dict(row) for row in first_page]
    corrected_page[0] = {
        "ts_code": "999998.SZ",
        "trade_date": "20250102",
        "suspend_type": "S",
    }
    terminal: list[dict[str, object]] = [
        {
            "ts_code": "999999.SZ",
            "trade_date": "20250102",
            "suspend_type": "S",
        }
    ]

    class MutableStatusClient:
        def __init__(self, page0: list[dict[str, object]]) -> None:
            self.page0 = page0
            self.suspend_offsets: list[str] = []

        def query(self, api_name: str, *, fields: str, **params: str) -> Any:
            if api_name == "stk_limit":
                return [
                    {
                        "ts_code": target,
                        "trade_date": "20250102",
                        "up_limit": 11.0,
                        "down_limit": 9.0,
                    }
                ]
            if api_name == "suspend_d":
                self.suspend_offsets.append(params["offset"])
                return self.page0 if params["offset"] == "0" else terminal
            if api_name == "stock_st":
                return []
            raise AssertionError(api_name)

    config = _config(tmp_path)
    registry = default_dataset_registry()
    request = normalize_request(
        registry,
        dataset="daily_limits_status",
        start=datetime(2025, 1, 2, tzinfo=UTC),
        end=datetime(2025, 1, 2, 23, 59, tzinfo=UTC),
        instruments=(target,),
        fields=("suspended",),
        coverage_keys=("SZSE:2025-01-02",),
    )
    first_client = MutableStatusClient(first_page)
    first = TushareProvider(
        client=first_client,
        paths=config.paths,
        registry=registry,
        clock=lambda: datetime(2025, 1, 3, tzinfo=UTC),
    )
    assert first.fetch(
        request, coverage_keys=request.coverage_keys, page_token=None
    ).table["suspended"].to_pylist() == [True]

    corrected_client = MutableStatusClient(corrected_page)
    corrected = TushareProvider(
        client=corrected_client,
        paths=config.paths,
        registry=registry,
        clock=lambda: datetime(2025, 1, 4, tzinfo=UTC),
    )
    page = corrected.fetch(
        request, coverage_keys=request.coverage_keys, page_token=None
    )

    assert corrected_client.suspend_offsets == ["0", "5000"]
    assert page.table["suspended"].to_pylist() == [False]


def test_mutable_endpoint_rejects_empty_to_nonempty_change_at_same_ingestion_time(
    tmp_path: Path,
) -> None:
    config = _config(tmp_path)
    registry = default_dataset_registry()
    request = normalize_request(
        registry,
        dataset="daily_limits_status",
        start=datetime(2025, 1, 2, tzinfo=UTC),
        end=datetime(2025, 1, 2, 23, 59, tzinfo=UTC),
        instruments=("600000.SH",),
        fields=("suspended",),
        coverage_keys=("SSE:2025-01-02",),
    )
    limit: dict[str, object] = {
        "ts_code": "600000.SH",
        "trade_date": "20250102",
        "up_limit": 11.0,
        "down_limit": 9.0,
    }
    suspension: dict[str, object] = {
        "ts_code": "600000.SH",
        "trade_date": "20250102",
        "suspend_type": "S",
    }
    observed_at = datetime(2025, 1, 3, 8, tzinfo=UTC)
    first = TushareProvider(
        client=FakeTushareClient(
            {"stk_limit": [limit], "suspend_d": [], "stock_st": []}
        ),
        paths=config.paths,
        registry=registry,
        clock=lambda: observed_at,
    )
    first.fetch(request, coverage_keys=request.coverage_keys, page_token=None)
    second = TushareProvider(
        client=FakeTushareClient(
            {
                "stk_limit": [limit],
                "suspend_d": [suspension],
                "stock_st": [],
            }
        ),
        paths=config.paths,
        registry=registry,
        clock=lambda: observed_at,
    )
    with pytest.raises(TushareDataError, match="ambiguous revision"):
        second.fetch(request, coverage_keys=request.coverage_keys, page_token=None)


def test_mixed_stock_etf_sparse_bars_only_request_stock_status(
    tmp_path: Path,
) -> None:
    stock = "600000.SH"
    etf = "510300.SH"
    config = _config(tmp_path, etf_instruments=(etf,))
    registry = default_dataset_registry()
    client = FakeTushareClient(
        {
            "daily": [],
            "fund_daily": [
                {
                    "ts_code": etf,
                    "trade_date": "20250102",
                    "open": 4.0,
                    "high": 4.1,
                    "low": 3.9,
                    "close": 4.05,
                    "vol": 1000.0,
                    "amount": 4050.0,
                    "pre_close": 4.0,
                }
            ],
            "stk_limit": [
                {
                    "ts_code": stock,
                    "trade_date": "20250102",
                    "up_limit": 11.0,
                    "down_limit": 9.0,
                }
            ],
            "suspend_d": [
                {
                    "ts_code": stock,
                    "trade_date": "20250102",
                    "suspend_type": "S",
                }
            ],
            "stock_st": [],
        }
    )
    provider = TushareProvider(
        client=client,
        paths=config.paths,
        registry=registry,
        clock=lambda: datetime(2025, 1, 3, tzinfo=UTC),
    )
    catalog = DuckDbCatalog(config.paths.catalog)
    portal = CacheFirstDataPortal(
        catalog=catalog,
        store=ParquetObjectStore(config.paths, registry),
        registry=registry,
        provider=provider,
        clock=lambda: datetime(2025, 1, 3, tzinfo=UTC),
    )
    request = normalize_request(
        registry,
        dataset="daily_bars",
        start=datetime(2025, 1, 2, tzinfo=UTC),
        end=datetime(2025, 1, 2, 23, 59, tzinfo=UTC),
        instruments=(etf, stock),
        fields=("close",),
        coverage_keys=("SSE:2025-01-02",),
    )
    try:
        result = portal.ensure(request)
        assert result.complete
        publication = ParquetObjectStore(
            config.paths, registry
        ).verify_publication(catalog.objects("daily_bars")[0])
        assert publication.absence_proofs[0].startswith(f"{stock}|")
        status_calls = [
            params
            for endpoint, params, _ in client.calls
            if endpoint == "stk_limit"
        ]
        assert status_calls
    finally:
        catalog.close()


def test_endpoint_pagination_resumes_from_last_verified_raw_page(
    tmp_path: Path,
) -> None:
    class FirstAttempt:
        def query(self, api_name: str, *, fields: str, **params: str) -> Any:
            assert api_name == "daily"
            if params["offset"] == "6000":
                raise RuntimeError("simulated crash")
            return [
                {
                    "ts_code": f"{index:06d}.SH",
                    "trade_date": "20250102",
                    "open": 10.0,
                    "high": 11.0,
                    "low": 9.0,
                    "close": 10.5,
                    "vol": 1000.0,
                    "amount": 10500.0,
                    "pre_close": 9.9,
                }
                for index in range(6000)
            ]

    class Retry:
        def __init__(self) -> None:
            self.offsets: list[str] = []

        def query(self, api_name: str, *, fields: str, **params: str) -> Any:
            assert api_name == "daily"
            self.offsets.append(params["offset"])
            assert params["offset"] == "6000"
            return [
                {
                    "ts_code": "006000.SH",
                    "trade_date": "20250102",
                    "open": 10.0,
                    "high": 11.0,
                    "low": 9.0,
                    "close": 10.5,
                    "vol": 1000.0,
                    "amount": 10500.0,
                    "pre_close": 9.9,
                }
            ]

    config = _config(tmp_path)
    registry = default_dataset_registry()
    request = normalize_request(
        registry,
        dataset="daily_bars",
        start=datetime(2025, 1, 2, tzinfo=UTC),
        end=datetime(2025, 1, 2, 23, 59, tzinfo=UTC),
        fields=("close",),
        coverage_keys=("SSE:2025-01-02",),
    )
    first = TushareProvider(
        client=FirstAttempt(),
        paths=config.paths,
        registry=registry,
        clock=lambda: datetime(2025, 1, 2, 8, tzinfo=UTC),
    )
    with pytest.raises(TushareDataError, match="request failed"):
        first.fetch(request, coverage_keys=request.coverage_keys, page_token=None)

    retry_client = Retry()
    retry = TushareProvider(
        client=retry_client,
        paths=config.paths,
        registry=registry,
        clock=lambda: datetime(2025, 1, 3, 8, tzinfo=UTC),
    )
    page = retry.fetch(request, coverage_keys=request.coverage_keys, page_token=None)
    assert page.table.num_rows == 6001
    assert retry_client.offsets == ["6000"]


@pytest.mark.skipif(not os.environ.get("TUSHARE_TOKEN"), reason="TUSHARE_TOKEN not set")
def test_real_tushare_trade_calendar_smoke(tmp_path: Path) -> None:
    config = _config(tmp_path)
    provider = TushareProvider.from_environment(
        config=config,
        registry=default_dataset_registry(),
        clock=lambda: datetime.now(UTC),
    )
    request = normalize_request(
        default_dataset_registry(),
        dataset="trade_calendar",
        start=datetime(2025, 1, 2, tzinfo=UTC),
        end=datetime(2025, 1, 2, 23, 59, tzinfo=UTC),
        fields=("is_open",),
        coverage_keys=("SSE:2025-01-02",),
    )
    page = provider.fetch(
        request, coverage_keys=request.coverage_keys, page_token=None
    )
    assert page.table.num_rows == 1
