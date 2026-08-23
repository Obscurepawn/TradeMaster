from __future__ import annotations

from datetime import UTC, date, datetime
from pathlib import Path
from typing import cast

import pyarrow as pa
import pytest
from trademaster.contracts import (
    DatasetRequest,
    SnapshotManifest,
    SnapshotRequest,
)
from trademaster.data import (
    CacheFirstDataPortal,
    CoverageGapError,
    DataConfig,
    DuckDbCatalog,
    ObjectIntegrityError,
    ParquetObjectStore,
    ProviderPage,
    default_dataset_registry,
    normalize_request,
)


def _calendar_revision(*, known_at: datetime, is_open: bool, revision: str) -> pa.Table:
    return pa.Table.from_pylist(
        [
            {
                "venue": "SSE",
                "session_date": date(2025, 1, 2),
                "is_open": is_open,
                "open_at": datetime(2025, 1, 2, 1, 30, tzinfo=UTC),
                "close_at": datetime(2025, 1, 2, 7, tzinfo=UTC),
                "event_time": datetime(2025, 1, 2, 7, tzinfo=UTC),
                "known_at": known_at,
                "source_revision": revision,
            }
        ],
        schema=default_dataset_registry()["trade_calendar"].arrow_schema,
    )


class NoFetchProvider:
    def fetch(
        self,
        request: DatasetRequest,
        *,
        coverage_keys: tuple[str, ...],
        page_token: str | None,
    ) -> ProviderPage:
        raise AssertionError("PIT fixture must be satisfied from cache")


def _request() -> DatasetRequest:
    return normalize_request(
        default_dataset_registry(),
        dataset="trade_calendar",
        start=datetime(2025, 1, 2, tzinfo=UTC),
        end=datetime(2025, 1, 2, 23, 59, tzinfo=UTC),
        fields=("is_open",),
        coverage_keys=("SSE:2025-01-02",),
    )


def _portal(
    tmp_path: Path,
) -> tuple[ParquetObjectStore, DuckDbCatalog, CacheFirstDataPortal, str, str]:
    config = DataConfig(
        data_root=tmp_path / "data",
        log_dir=tmp_path / "logs",
        canonical_source_policy="trusted_imports",
    )
    config.paths.ensure_layout()
    registry = default_dataset_registry()
    store = ParquetObjectStore(config.paths, registry)
    catalog = DuckDbCatalog(config.paths.catalog)
    old = store.publish_and_register(
        catalog,
        _request(),
        _calendar_revision(
            known_at=datetime(2025, 1, 2, 8, tzinfo=UTC),
            is_open=True,
            revision="old",
        ),
        partition_key="venue=SSE/session_year=2025",
        created_at=datetime(2025, 1, 2, 9, tzinfo=UTC),
    )
    new = store.publish_and_register(
        catalog,
        _request(),
        _calendar_revision(
            known_at=datetime(2025, 1, 5, 8, tzinfo=UTC),
            is_open=False,
            revision="new",
        ),
        partition_key="venue=SSE/session_year=2025",
        created_at=datetime(2025, 1, 5, 9, tzinfo=UTC),
    )
    portal = CacheFirstDataPortal(
        catalog=catalog,
        store=store,
        registry=registry,
        provider=NoFetchProvider(),
        clock=lambda: datetime(2025, 1, 6, tzinfo=UTC),
    )
    return store, catalog, portal, old.object.sha256, new.object.sha256


def test_snapshot_is_deterministic_and_selects_only_pit_eligible_replacement(
    tmp_path: Path,
) -> None:
    store, catalog, portal, old_sha, new_sha = _portal(tmp_path)
    try:
        request = SnapshotRequest(
            datasets=(_request(),), as_of=datetime(2025, 1, 3, tzinfo=UTC)
        )
        first = portal.snapshot(request)
        second = portal.snapshot(request)

        assert first == second
        assert tuple(obj.sha256 for obj in first.objects) == (old_sha,)
        assert new_sha not in first.snapshot_id
        manifest_path = store.paths.snapshots / first.snapshot_id / "manifest.json"
        assert SnapshotManifest.model_validate_json(manifest_path.read_bytes()) == first
    finally:
        catalog.close()


def test_persisted_snapshot_loader_revalidates_manifest_and_parquet(
    tmp_path: Path,
) -> None:
    store, catalog, portal, old_sha, _ = _portal(tmp_path)
    try:
        manifest = portal.snapshot(
            SnapshotRequest(
                datasets=(_request(),),
                as_of=datetime(2025, 1, 3, tzinfo=UTC),
            )
        )
        assert portal.load_snapshot(manifest.snapshot_id) == manifest

        old = next(
            obj for obj in catalog.objects("trade_calendar") if obj.sha256 == old_sha
        )
        (store.paths.data_root / old.uri).write_bytes(b"tampered")
        with pytest.raises(ObjectIntegrityError, match="content hash"):
            portal.load_snapshot(manifest.snapshot_id)
    finally:
        catalog.close()


def test_snapshot_merges_compatible_field_requests_into_one_object_proof(
    tmp_path: Path,
) -> None:
    _, catalog, portal, old_sha, _ = _portal(tmp_path)
    base = _request()
    open_time = base.model_copy(update={"fields": ("open_at",)})
    try:
        manifest = portal.snapshot(
            SnapshotRequest(
                datasets=tuple(
                    sorted((base, open_time), key=lambda item: item.fields)
                ),
                as_of=datetime(2025, 1, 3, tzinfo=UTC),
            )
        )

        assert tuple(obj.sha256 for obj in manifest.objects) == (old_sha,)
        assert len(manifest.coverages) == 1
        assert manifest.request.datasets[0].fields == ("is_open", "open_at")
    finally:
        catalog.close()


def test_snapshot_accepts_subsumed_requests_and_overlapping_cache_objects(
    tmp_path: Path,
) -> None:
    registry = default_dataset_registry()
    config = DataConfig(
        data_root=tmp_path / "data",
        log_dir=tmp_path / "logs",
        canonical_source_policy="trusted_imports",
    )
    config.paths.ensure_layout()
    store = ParquetObjectStore(config.paths, registry)
    catalog = DuckDbCatalog(config.paths.catalog)

    def request(days: tuple[int, ...]) -> DatasetRequest:
        return DatasetRequest(
            dataset="trade_calendar",
            start=datetime(2025, 1, min(days), tzinfo=UTC),
            end=datetime(2025, 1, max(days), 23, 59, tzinfo=UTC),
            fields=("is_open",),
            coverage_keys=tuple(f"SSE:2025-01-{day:02d}" for day in days),
        )

    def table(days: tuple[int, ...]) -> pa.Table:
        return pa.Table.from_pylist(
            [
                {
                    "venue": "SSE",
                    "session_date": date(2025, 1, day),
                    "is_open": True,
                    "open_at": datetime(2025, 1, day, 1, 30, tzinfo=UTC),
                    "close_at": datetime(2025, 1, day, 7, tzinfo=UTC),
                    "event_time": datetime(2025, 1, day, 7, tzinfo=UTC),
                    "known_at": datetime(2025, 1, day, 8, tzinfo=UTC),
                    "source_revision": f"day-{day}",
                }
                for day in days
            ],
            schema=registry["trade_calendar"].arrow_schema,
        )

    narrow = request((2,))
    wide = request((2, 3))
    store.publish_and_register(
        catalog,
        wide,
        table((2, 3)),
        partition_key="venue=SSE/session_year=2025",
        created_at=datetime(2025, 1, 4, tzinfo=UTC),
    )
    store.publish_and_register(
        catalog,
        narrow,
        table((2,)),
        partition_key="venue=SSE/session_year=2025",
        created_at=datetime(2025, 1, 4, 1, tzinfo=UTC),
    )
    portal = CacheFirstDataPortal(
        catalog=catalog,
        store=store,
        registry=registry,
        provider=NoFetchProvider(),
        clock=lambda: datetime(2025, 1, 5, tzinfo=UTC),
    )
    try:
        manifest = portal.snapshot(
            SnapshotRequest(
                datasets=(narrow, wide),
                as_of=datetime(2025, 1, 5, tzinfo=UTC),
            )
        )
        assert manifest.request.datasets == (wide,)
        assert set(manifest.coverages[0].request.coverage_keys) == {
            "SSE:2025-01-02",
            "SSE:2025-01-03",
        }
    finally:
        catalog.close()


def test_pit_query_keeps_latest_revision_before_cutoff_and_binds_provenance(
    tmp_path: Path,
) -> None:
    _, catalog, portal, _, _ = _portal(tmp_path)
    try:
        table = portal.query(_request(), as_of=datetime(2025, 1, 3, tzinfo=UTC))
        assert table["is_open"].to_pylist() == [True]
        assert table["source_revision"].to_pylist() == ["old"]
        assert table.schema.metadata is not None
        assert b"trademaster.snapshot_id" in table.schema.metadata
        known_times = cast(list[datetime], table["known_at"].to_pylist())
        assert max(known_times) <= datetime(2025, 1, 3, tzinfo=UTC)
    finally:
        catalog.close()


def test_pit_query_fails_closed_when_object_straddles_known_at_cutoff(
    tmp_path: Path,
) -> None:
    registry = default_dataset_registry()
    config = DataConfig(
        data_root=tmp_path / "data",
        log_dir=tmp_path / "logs",
        canonical_source_policy="trusted_imports",
    )
    config.paths.ensure_layout()
    store = ParquetObjectStore(config.paths, registry)
    catalog = DuckDbCatalog(config.paths.catalog)
    request = DatasetRequest(
        dataset="trade_calendar",
        start=datetime(2025, 1, 2, tzinfo=UTC),
        end=datetime(2025, 1, 3, 23, 59, tzinfo=UTC),
        fields=("is_open",),
        coverage_keys=("SSE:2025-01-02", "SSE:2025-01-03"),
    )

    def revisions(
        day2_open: bool,
        day2_known_at: datetime,
        day2_revision: str,
        day3_known_at: datetime,
    ) -> pa.Table:
        return pa.Table.from_pylist(
            [
                {
                    "venue": "SSE",
                    "session_date": date(2025, 1, 2),
                    "is_open": day2_open,
                    "open_at": datetime(2025, 1, 2, 1, 30, tzinfo=UTC),
                    "close_at": datetime(2025, 1, 2, 7, tzinfo=UTC),
                    "event_time": datetime(2025, 1, 2, 7, tzinfo=UTC),
                    "known_at": day2_known_at,
                    "source_revision": day2_revision,
                },
                {
                    "venue": "SSE",
                    "session_date": date(2025, 1, 3),
                    "is_open": True,
                    "open_at": datetime(2025, 1, 3, 1, 30, tzinfo=UTC),
                    "close_at": datetime(2025, 1, 3, 7, tzinfo=UTC),
                    "event_time": datetime(2025, 1, 3, 7, tzinfo=UTC),
                    "known_at": day3_known_at,
                    "source_revision": f"day3-{day3_known_at.date()}",
                },
            ],
            schema=registry["trade_calendar"].arrow_schema,
        )

    store.publish_and_register(
        catalog,
        request,
        revisions(
            True,
            datetime(2025, 1, 2, 9, tzinfo=UTC),
            "old",
            datetime(2025, 1, 3, 8, tzinfo=UTC),
        ),
        partition_key="venue=SSE/session_year=2025",
        created_at=datetime(2025, 1, 3, 9, tzinfo=UTC),
    )
    store.publish_and_register(
        catalog,
        request,
        revisions(
            False,
            datetime(2025, 1, 3, 9, tzinfo=UTC),
            "corrected",
            datetime(2025, 1, 5, 8, tzinfo=UTC),
        ),
        partition_key="venue=SSE/session_year=2025",
        created_at=datetime(2025, 1, 5, 9, tzinfo=UTC),
    )
    portal = CacheFirstDataPortal(
        catalog=catalog,
        store=store,
        registry=registry,
        provider=NoFetchProvider(),
        clock=lambda: datetime(2025, 1, 5, tzinfo=UTC),
    )
    try:
        with pytest.raises(CoverageGapError, match="straddles PIT cutoff"):
            portal.query(request, as_of=datetime(2025, 1, 4, tzinfo=UTC))
    finally:
        catalog.close()


def test_snapshot_revalidates_parquet_and_rejects_tampering(tmp_path: Path) -> None:
    store, catalog, portal, old_sha, _ = _portal(tmp_path)
    try:
        old = next(obj for obj in catalog.objects("trade_calendar") if obj.sha256 == old_sha)
        (store.paths.data_root / old.uri).write_bytes(b"tampered")
        with pytest.raises(ObjectIntegrityError, match="content hash"):
            portal.snapshot(
                SnapshotRequest(
                    datasets=(_request(),),
                    as_of=datetime(2025, 1, 3, tzinfo=UTC),
                )
            )
    finally:
        catalog.close()


def test_financial_revision_ties_use_business_revision_not_source_hash(
    tmp_path: Path,
) -> None:
    config = DataConfig(
        data_root=tmp_path / "data",
        log_dir=tmp_path / "logs",
        canonical_source_policy="trusted_imports",
    )
    config.paths.ensure_layout()
    registry = default_dataset_registry()
    store = ParquetObjectStore(config.paths, registry)
    catalog = DuckDbCatalog(config.paths.catalog)
    request = normalize_request(
        registry,
        dataset="financial_indicators",
        start=datetime(2024, 12, 31, tzinfo=UTC),
        end=datetime(2024, 12, 31, 23, 59, 59, tzinfo=UTC),
        instruments=("600000.SH",),
        fields=("report_values_json",),
        coverage_keys=("600000.SH:2024-12-31:2025-03-01",),
    )
    common: dict[str, object] = {
        "instrument_id": "600000.SH",
        "report_period": date(2024, 12, 31),
        "announcement_id": "2025-03-01",
        "event_time": datetime(2024, 12, 31, 23, 59, tzinfo=UTC),
        "known_at": datetime(2025, 3, 1, 16, tzinfo=UTC),
    }
    table = pa.Table.from_pylist(
        [
            {
                **common,
                "revision": "1",
                "report_values_json": '{"eps":"old"}',
                "source_revision": "z-old",
            },
            {
                **common,
                "revision": "2",
                "report_values_json": '{"eps":"new"}',
                "source_revision": "a-new",
            },
        ],
        schema=registry["financial_indicators"].arrow_schema,
    )
    store.publish_and_register(
        catalog,
        request,
        table,
        partition_key="dataset_scope=all",
        created_at=datetime(2025, 3, 2, tzinfo=UTC),
    )
    portal = CacheFirstDataPortal(
        catalog=catalog,
        store=store,
        registry=registry,
        provider=NoFetchProvider(),
        clock=lambda: datetime(2025, 3, 2, tzinfo=UTC),
    )
    try:
        result = portal.query(request, as_of=datetime(2025, 3, 2, tzinfo=UTC))
        assert result["report_values_json"].to_pylist() == ['{"eps":"new"}']
        assert result["source_revision"].to_pylist() == ["a-new"]
    finally:
        catalog.close()


def test_financial_same_business_revision_is_ambiguous_and_rejected(
    tmp_path: Path,
) -> None:
    config = DataConfig(
        data_root=tmp_path / "data",
        log_dir=tmp_path / "logs",
        canonical_source_policy="trusted_imports",
    )
    config.paths.ensure_layout()
    registry = default_dataset_registry()
    request = normalize_request(
        registry,
        dataset="financial_indicators",
        start=datetime(2024, 12, 31, tzinfo=UTC),
        end=datetime(2024, 12, 31, 23, 59, 59, tzinfo=UTC),
        instruments=("600000.SH",),
        fields=("report_values_json",),
        coverage_keys=("600000.SH:2024-12-31:2025-03-01",),
    )
    common: dict[str, object] = {
        "instrument_id": "600000.SH",
        "report_period": date(2024, 12, 31),
        "announcement_id": "2025-03-01",
        "revision": "1",
        "event_time": datetime(2024, 12, 31, 23, 59, tzinfo=UTC),
        "known_at": datetime(2025, 3, 1, 16, tzinfo=UTC),
    }
    table = pa.Table.from_pylist(
        [
            {**common, "report_values_json": "old", "source_revision": "z-old"},
            {**common, "report_values_json": "new", "source_revision": "a-new"},
        ],
        schema=registry["financial_indicators"].arrow_schema,
    )
    with pytest.raises(ObjectIntegrityError, match="ambiguous business revision"):
        ParquetObjectStore(config.paths, registry).publish(
            request,
            table,
            partition_key="dataset_scope=all",
            created_at=datetime(2025, 3, 2, tzinfo=UTC),
        )
    store = ParquetObjectStore(config.paths, registry)
    catalog = DuckDbCatalog(config.paths.catalog)
    for index, row in enumerate(table.to_pylist()):
        single = pa.Table.from_pylist(
            [row], schema=registry["financial_indicators"].arrow_schema
        )
        store.publish_and_register(
            catalog,
            request,
            single,
            partition_key="dataset_scope=all",
            created_at=datetime(2025, 3, 2, index, tzinfo=UTC),
        )
    portal = CacheFirstDataPortal(
        catalog=catalog,
        store=store,
        registry=registry,
        provider=NoFetchProvider(),
        clock=lambda: datetime(2025, 3, 3, tzinfo=UTC),
    )
    try:
        with pytest.raises(CoverageGapError, match="business revision identity"):
            portal.query(request, as_of=datetime(2025, 3, 3, tzinfo=UTC))
    finally:
        catalog.close()


def test_cross_object_revision_rejects_same_source_identity_with_different_payload(
    tmp_path: Path,
) -> None:
    config = DataConfig(
        data_root=tmp_path / "data",
        log_dir=tmp_path / "logs",
        canonical_source_policy="trusted_imports",
    )
    config.paths.ensure_layout()
    registry = default_dataset_registry()
    request = normalize_request(
        registry,
        dataset="financial_indicators",
        start=datetime(2024, 12, 31, tzinfo=UTC),
        end=datetime(2024, 12, 31, 23, 59, 59, tzinfo=UTC),
        instruments=("600000.SH",),
        fields=("report_values_json",),
        coverage_keys=("600000.SH:2024-12-31:2025-03-01",),
    )
    common: dict[str, object] = {
        "instrument_id": "600000.SH",
        "report_period": date(2024, 12, 31),
        "announcement_id": "2025-03-01",
        "revision": "1",
        "event_time": datetime(2024, 12, 31, 23, 59, tzinfo=UTC),
        "known_at": datetime(2025, 3, 1, 16, tzinfo=UTC),
        "source_revision": "same-source",
    }
    store = ParquetObjectStore(config.paths, registry)
    catalog = DuckDbCatalog(config.paths.catalog)
    for index, payload in enumerate(("old", "new")):
        table = pa.Table.from_pylist(
            [{**common, "report_values_json": payload}],
            schema=registry["financial_indicators"].arrow_schema,
        )
        store.publish_and_register(
            catalog,
            request,
            table,
            partition_key="dataset_scope=all",
            created_at=datetime(2025, 3, 2, index, tzinfo=UTC),
        )
    portal = CacheFirstDataPortal(
        catalog=catalog,
        store=store,
        registry=registry,
        provider=NoFetchProvider(),
        clock=lambda: datetime(2025, 3, 3, tzinfo=UTC),
    )
    try:
        with pytest.raises(CoverageGapError, match="business revision identity"):
            portal.query(request, as_of=datetime(2025, 3, 3, tzinfo=UTC))
    finally:
        catalog.close()


def test_cross_object_market_rows_reject_same_revision_with_different_payload(
    tmp_path: Path,
) -> None:
    config = DataConfig(
        data_root=tmp_path / "data",
        log_dir=tmp_path / "logs",
        canonical_source_policy="trusted_imports",
    )
    config.paths.ensure_layout()
    registry = default_dataset_registry()
    request = normalize_request(
        registry,
        dataset="daily_bars",
        start=datetime(2025, 1, 2, tzinfo=UTC),
        end=datetime(2025, 1, 2, 23, 59, tzinfo=UTC),
        instruments=("600000.SH",),
        fields=("close",),
        coverage_keys=("SSE:2025-01-02",),
    )
    common: dict[str, object] = {
        "instrument_id": "600000.SH",
        "trade_date": date(2025, 1, 2),
        "open": 10.0,
        "high": 11.0,
        "low": 9.0,
        "volume": 1000.0,
        "amount": 10500.0,
        "pre_close": 9.9,
        "venue": "SSE",
        "event_time": datetime(2025, 1, 2, 7, tzinfo=UTC),
        "known_at": datetime(2025, 1, 2, 8, tzinfo=UTC),
        "source_revision": "same-source",
    }
    store = ParquetObjectStore(config.paths, registry)
    catalog = DuckDbCatalog(config.paths.catalog)
    for hour, close in ((9, 10.5), (10, 99.0)):
        table = pa.Table.from_pylist(
            [{**common, "close": close}], schema=registry["daily_bars"].arrow_schema
        )
        store.publish_and_register(
            catalog,
            request,
            table,
            partition_key="trade_year=2025/trade_month=01",
            created_at=datetime(2025, 1, 3, hour, tzinfo=UTC),
        )
    portal = CacheFirstDataPortal(
        catalog=catalog,
        store=store,
        registry=registry,
        provider=NoFetchProvider(),
        clock=lambda: datetime(2025, 1, 3, 12, tzinfo=UTC),
    )
    try:
        with pytest.raises(CoverageGapError, match="revision identity"):
            portal.query(request, as_of=datetime(2025, 1, 3, 12, tzinfo=UTC))
    finally:
        catalog.close()


def test_cross_universe_objects_cannot_disagree_on_the_same_market_fact(
    tmp_path: Path,
) -> None:
    config = DataConfig(
        data_root=tmp_path / "data",
        log_dir=tmp_path / "logs",
        canonical_source_policy="trusted_imports",
    )
    config.paths.ensure_layout()
    registry = default_dataset_registry()
    narrow = normalize_request(
        registry,
        dataset="daily_bars",
        start=datetime(2025, 1, 2, tzinfo=UTC),
        end=datetime(2025, 1, 2, 23, 59, tzinfo=UTC),
        instruments=("600000.SH",),
        fields=("close",),
        coverage_keys=("SSE:2025-01-02",),
    )
    wide = normalize_request(
        registry,
        dataset="daily_bars",
        start=narrow.start,
        end=narrow.end,
        instruments=("600000.SH", "600001.SH"),
        fields=narrow.fields,
        coverage_keys=narrow.coverage_keys,
    )

    def row(instrument_id: str, close: float) -> dict[str, object]:
        return {
            "instrument_id": instrument_id,
            "trade_date": date(2025, 1, 2),
            "open": 10.0,
            "high": 11.0,
            "low": 9.0,
            "close": close,
            "volume": 1000.0,
            "amount": 10500.0,
            "pre_close": 9.9,
            "venue": "SSE",
            "event_time": datetime(2025, 1, 2, 7, tzinfo=UTC),
            "known_at": datetime(2025, 1, 2, 8, tzinfo=UTC),
            "source_revision": "same-provider-revision",
        }

    store = ParquetObjectStore(config.paths, registry)
    catalog = DuckDbCatalog(config.paths.catalog)
    store.publish_and_register(
        catalog,
        narrow,
        pa.Table.from_pylist(
            [row("600000.SH", 10.5)], schema=registry["daily_bars"].arrow_schema
        ),
        partition_key="trade_year=2025/trade_month=01",
        created_at=datetime(2025, 1, 3, 9, tzinfo=UTC),
    )
    store.publish_and_register(
        catalog,
        wide,
        pa.Table.from_pylist(
            [row("600000.SH", 99.0), row("600001.SH", 20.0)],
            schema=registry["daily_bars"].arrow_schema,
        ),
        partition_key="trade_year=2025/trade_month=01",
        created_at=datetime(2025, 1, 3, 10, tzinfo=UTC),
    )
    portal = CacheFirstDataPortal(
        catalog=catalog,
        store=store,
        registry=registry,
        provider=NoFetchProvider(),
        clock=lambda: datetime(2025, 1, 3, 11, tzinfo=UTC),
    )
    try:
        with pytest.raises(CoverageGapError, match="revision identity"):
            portal.query(narrow, as_of=datetime(2025, 1, 3, 11, tzinfo=UTC))
    finally:
        catalog.close()


def test_pit_fails_closed_when_newer_revision_is_only_in_wider_coverage(
    tmp_path: Path,
) -> None:
    registry = default_dataset_registry()
    config = DataConfig(
        data_root=tmp_path / "data",
        log_dir=tmp_path / "logs",
        canonical_source_policy="trusted_imports",
    )
    config.paths.ensure_layout()
    store = ParquetObjectStore(config.paths, registry)
    catalog = DuckDbCatalog(config.paths.catalog)

    def request(*days: int) -> DatasetRequest:
        return DatasetRequest(
            dataset="trade_calendar",
            start=datetime(2025, 1, min(days), tzinfo=UTC),
            end=datetime(2025, 1, max(days), 23, 59, tzinfo=UTC),
            fields=("is_open",),
            coverage_keys=tuple(f"SSE:2025-01-{day:02d}" for day in days),
        )

    def table(rows: tuple[tuple[int, bool, datetime, str], ...]) -> pa.Table:
        return pa.Table.from_pylist(
            [
                {
                    "venue": "SSE",
                    "session_date": date(2025, 1, day),
                    "is_open": is_open,
                    "open_at": datetime(2025, 1, day, 1, 30, tzinfo=UTC),
                    "close_at": datetime(2025, 1, day, 7, tzinfo=UTC),
                    "event_time": datetime(2025, 1, day, 7, tzinfo=UTC),
                    "known_at": known_at,
                    "source_revision": revision,
                }
                for day, is_open, known_at, revision in rows
            ],
            schema=registry["trade_calendar"].arrow_schema,
        )

    narrow = request(2)
    store.publish_and_register(
        catalog,
        narrow,
        table(((2, True, datetime(2025, 1, 2, 8, tzinfo=UTC), "old"),)),
        partition_key="venue=SSE/session_year=2025",
        created_at=datetime(2025, 1, 2, 9, tzinfo=UTC),
    )
    store.publish_and_register(
        catalog,
        request(2, 3),
        table(
            (
                (2, False, datetime(2025, 1, 3, 9, tzinfo=UTC), "corrected"),
                (3, True, datetime(2025, 1, 5, 8, tzinfo=UTC), "future"),
            )
        ),
        partition_key="venue=SSE/session_year=2025",
        created_at=datetime(2025, 1, 5, 9, tzinfo=UTC),
    )
    portal = CacheFirstDataPortal(
        catalog=catalog,
        store=store,
        registry=registry,
        provider=NoFetchProvider(),
        clock=lambda: datetime(2025, 1, 6, tzinfo=UTC),
    )
    try:
        with pytest.raises(CoverageGapError, match="outside selected object scope"):
            portal.query(narrow, as_of=datetime(2025, 1, 4, tzinfo=UTC))
    finally:
        catalog.close()


def test_pit_fails_closed_when_newer_revision_is_only_in_wider_universe(
    tmp_path: Path,
) -> None:
    registry = default_dataset_registry()
    config = DataConfig(
        data_root=tmp_path / "data",
        log_dir=tmp_path / "logs",
        canonical_source_policy="trusted_imports",
    )
    config.paths.ensure_layout()
    store = ParquetObjectStore(config.paths, registry)
    catalog = DuckDbCatalog(config.paths.catalog)

    def request(instruments: tuple[str, ...]) -> DatasetRequest:
        return DatasetRequest(
            dataset="daily_bars",
            start=datetime(2025, 1, 2, tzinfo=UTC),
            end=datetime(2025, 1, 2, 23, 59, tzinfo=UTC),
            instruments=instruments,
            fields=("close",),
            coverage_keys=("SSE:2025-01-02",),
        )

    def row(
        instrument_id: str, close: float, known_at: datetime, revision: str
    ) -> dict[str, object]:
        return {
            "instrument_id": instrument_id,
            "trade_date": date(2025, 1, 2),
            "open": 10.0,
            "high": 11.0,
            "low": 9.0,
            "close": close,
            "volume": 1000.0,
            "amount": close * 1000.0,
            "pre_close": 9.9,
            "venue": "SSE",
            "event_time": datetime(2025, 1, 2, 7, tzinfo=UTC),
            "known_at": known_at,
            "source_revision": revision,
        }

    narrow = request(("600000.SH",))
    store.publish_and_register(
        catalog,
        narrow,
        pa.Table.from_pylist(
            [row("600000.SH", 10.5, datetime(2025, 1, 2, 8, tzinfo=UTC), "old")],
            schema=registry["daily_bars"].arrow_schema,
        ),
        partition_key="trade_year=2025/trade_month=01",
        created_at=datetime(2025, 1, 2, 9, tzinfo=UTC),
    )
    store.publish_and_register(
        catalog,
        request(("600000.SH", "600001.SH")),
        pa.Table.from_pylist(
            [
                row(
                    "600000.SH",
                    99.0,
                    datetime(2025, 1, 3, 9, tzinfo=UTC),
                    "corrected",
                ),
                row(
                    "600001.SH",
                    20.0,
                    datetime(2025, 1, 5, 8, tzinfo=UTC),
                    "future",
                ),
            ],
            schema=registry["daily_bars"].arrow_schema,
        ),
        partition_key="trade_year=2025/trade_month=01",
        created_at=datetime(2025, 1, 5, 9, tzinfo=UTC),
    )
    portal = CacheFirstDataPortal(
        catalog=catalog,
        store=store,
        registry=registry,
        provider=NoFetchProvider(),
        clock=lambda: datetime(2025, 1, 6, tzinfo=UTC),
    )
    try:
        with pytest.raises(CoverageGapError, match="outside selected object scope"):
            portal.query(narrow, as_of=datetime(2025, 1, 4, tzinfo=UTC))
    finally:
        catalog.close()


def test_pit_merges_latest_revisions_per_business_key_across_exact_scope_objects(
    tmp_path: Path,
) -> None:
    registry = default_dataset_registry()
    config = DataConfig(
        data_root=tmp_path / "data",
        log_dir=tmp_path / "logs",
        canonical_source_policy="trusted_imports",
    )
    config.paths.ensure_layout()
    store = ParquetObjectStore(config.paths, registry)
    catalog = DuckDbCatalog(config.paths.catalog)
    request = DatasetRequest(
        dataset="trade_calendar",
        start=datetime(2025, 1, 2, tzinfo=UTC),
        end=datetime(2025, 1, 3, 23, 59, tzinfo=UTC),
        fields=("is_open",),
        coverage_keys=("SSE:2025-01-02", "SSE:2025-01-03"),
    )

    def table(rows: tuple[tuple[int, bool, datetime, str], ...]) -> pa.Table:
        return pa.Table.from_pylist(
            [
                {
                    "venue": "SSE",
                    "session_date": date(2025, 1, day),
                    "is_open": is_open,
                    "open_at": datetime(2025, 1, day, 1, 30, tzinfo=UTC),
                    "close_at": datetime(2025, 1, day, 7, tzinfo=UTC),
                    "event_time": datetime(2025, 1, day, 7, tzinfo=UTC),
                    "known_at": known_at,
                    "source_revision": revision,
                }
                for day, is_open, known_at, revision in rows
            ],
            schema=registry["trade_calendar"].arrow_schema,
        )

    for rows, created_at in (
        (
            (
                (2, True, datetime(2025, 1, 2, 8, tzinfo=UTC), "day2-old"),
                (3, False, datetime(2025, 1, 5, 8, tzinfo=UTC), "day3-new"),
            ),
            datetime(2025, 1, 5, 9, tzinfo=UTC),
        ),
        (
            (
                (2, False, datetime(2025, 1, 4, 8, tzinfo=UTC), "day2-corrected"),
                (3, True, datetime(2025, 1, 3, 8, tzinfo=UTC), "day3-old"),
            ),
            datetime(2025, 1, 4, 9, tzinfo=UTC),
        ),
    ):
        store.publish_and_register(
            catalog,
            request,
            table(rows),
            partition_key="venue=SSE/session_year=2025",
            created_at=created_at,
        )
    portal = CacheFirstDataPortal(
        catalog=catalog,
        store=store,
        registry=registry,
        provider=NoFetchProvider(),
        clock=lambda: datetime(2025, 1, 7, tzinfo=UTC),
    )
    try:
        result = portal.query(request, as_of=datetime(2025, 1, 6, tzinfo=UTC))
        assert result["is_open"].to_pylist() == [False, False]
        assert result["source_revision"].to_pylist() == [
            "day2-corrected",
            "day3-new",
        ]
    finally:
        catalog.close()


def test_persisted_snapshot_rejects_later_catalogued_eligible_correction(
    tmp_path: Path,
) -> None:
    registry = default_dataset_registry()
    config = DataConfig(
        data_root=tmp_path / "data",
        log_dir=tmp_path / "logs",
        canonical_source_policy="trusted_imports",
    )
    config.paths.ensure_layout()
    store = ParquetObjectStore(config.paths, registry)
    catalog = DuckDbCatalog(config.paths.catalog)
    request = _request()
    store.publish_and_register(
        catalog,
        request,
        _calendar_revision(
            known_at=datetime(2025, 1, 2, 8, tzinfo=UTC),
            is_open=True,
            revision="old",
        ),
        partition_key="venue=SSE/session_year=2025",
        created_at=datetime(2025, 1, 2, 9, tzinfo=UTC),
    )
    portal = CacheFirstDataPortal(
        catalog=catalog,
        store=store,
        registry=registry,
        provider=NoFetchProvider(),
        clock=lambda: datetime(2025, 1, 4, tzinfo=UTC),
    )
    try:
        manifest = portal.snapshot(
            SnapshotRequest(datasets=(request,), as_of=datetime(2025, 1, 3, tzinfo=UTC))
        )
        store.publish_and_register(
            catalog,
            request,
            _calendar_revision(
                known_at=datetime(2025, 1, 2, 10, tzinfo=UTC),
                is_open=False,
                revision="corrected",
            ),
            partition_key="venue=SSE/session_year=2025",
            created_at=datetime(2025, 1, 4, tzinfo=UTC),
        )

        with pytest.raises(CoverageGapError, match="outside selected object scope"):
            portal.load_snapshot(manifest.snapshot_id)
    finally:
        catalog.close()


def test_persisted_snapshot_rejects_same_known_at_conflicting_revision(
    tmp_path: Path,
) -> None:
    registry = default_dataset_registry()
    config = DataConfig(
        data_root=tmp_path / "data",
        log_dir=tmp_path / "logs",
        canonical_source_policy="trusted_imports",
    )
    config.paths.ensure_layout()
    store = ParquetObjectStore(config.paths, registry)
    catalog = DuckDbCatalog(config.paths.catalog)
    request = _request()
    known_at = datetime(2025, 1, 2, 8, tzinfo=UTC)
    store.publish_and_register(
        catalog,
        request,
        _calendar_revision(known_at=known_at, is_open=True, revision="z-old"),
        partition_key="venue=SSE/session_year=2025",
        created_at=datetime(2025, 1, 2, 9, tzinfo=UTC),
    )
    portal = CacheFirstDataPortal(
        catalog=catalog,
        store=store,
        registry=registry,
        provider=NoFetchProvider(),
        clock=lambda: datetime(2025, 1, 4, tzinfo=UTC),
    )
    try:
        manifest = portal.snapshot(
            SnapshotRequest(datasets=(request,), as_of=datetime(2025, 1, 3, tzinfo=UTC))
        )
        store.publish_and_register(
            catalog,
            request,
            _calendar_revision(known_at=known_at, is_open=False, revision="a-conflict"),
            partition_key="venue=SSE/session_year=2025",
            created_at=datetime(2025, 1, 4, tzinfo=UTC),
        )

        with pytest.raises(CoverageGapError, match="revision identity"):
            portal.load_snapshot(manifest.snapshot_id)
    finally:
        catalog.close()


def test_global_revision_conflict_in_disjoint_universe_does_not_block_query(
    tmp_path: Path,
) -> None:
    registry = default_dataset_registry()
    config = DataConfig(
        data_root=tmp_path / "data",
        log_dir=tmp_path / "logs",
        canonical_source_policy="trusted_imports",
    )
    config.paths.ensure_layout()
    store = ParquetObjectStore(config.paths, registry)
    catalog = DuckDbCatalog(config.paths.catalog)

    def request(instrument_id: str) -> DatasetRequest:
        return DatasetRequest(
            dataset="daily_bars",
            start=datetime(2025, 1, 2, tzinfo=UTC),
            end=datetime(2025, 1, 2, 23, 59, tzinfo=UTC),
            instruments=(instrument_id,),
            fields=("close",),
            coverage_keys=("SSE:2025-01-02",),
        )

    def table(instrument_id: str, close: float, revision: str) -> pa.Table:
        return pa.Table.from_pylist(
            [
                {
                    "instrument_id": instrument_id,
                    "trade_date": date(2025, 1, 2),
                    "open": 10.0,
                    "high": 11.0,
                    "low": 9.0,
                    "close": close,
                    "volume": 1000.0,
                    "amount": close * 1000.0,
                    "pre_close": 9.9,
                    "venue": "SSE",
                    "event_time": datetime(2025, 1, 2, 7, tzinfo=UTC),
                    "known_at": datetime(2025, 1, 2, 8, tzinfo=UTC),
                    "source_revision": revision,
                }
            ],
            schema=registry["daily_bars"].arrow_schema,
        )

    store.publish_and_register(
        catalog,
        request("600000.SH"),
        table("600000.SH", 10.5, "a"),
        partition_key="trade_year=2025/trade_month=01",
        created_at=datetime(2025, 1, 2, 9, tzinfo=UTC),
    )
    for hour, close, revision in ((10, 20.0, "b-one"), (11, 99.0, "b-two")):
        store.publish_and_register(
            catalog,
            request("600001.SH"),
            table("600001.SH", close, revision),
            partition_key="trade_year=2025/trade_month=01",
            created_at=datetime(2025, 1, 2, hour, tzinfo=UTC),
        )
    portal = CacheFirstDataPortal(
        catalog=catalog,
        store=store,
        registry=registry,
        provider=NoFetchProvider(),
        clock=lambda: datetime(2025, 1, 3, tzinfo=UTC),
    )
    try:
        result = portal.query(
            request("600000.SH"), as_of=datetime(2025, 1, 3, tzinfo=UTC)
        )
        assert result["close"].to_pylist() == [10.5]
    finally:
        catalog.close()


def test_snapshot_merges_partially_overlapping_dataset_requests(
    tmp_path: Path,
) -> None:
    registry = default_dataset_registry()
    config = DataConfig(
        data_root=tmp_path / "data",
        log_dir=tmp_path / "logs",
        canonical_source_policy="trusted_imports",
    )
    config.paths.ensure_layout()
    store = ParquetObjectStore(config.paths, registry)
    catalog = DuckDbCatalog(config.paths.catalog)

    def day_request(*days: int) -> DatasetRequest:
        return DatasetRequest(
            dataset="trade_calendar",
            start=datetime(2025, 1, min(days), tzinfo=UTC),
            end=datetime(2025, 1, max(days), 23, 59, tzinfo=UTC),
            fields=("is_open",),
            coverage_keys=tuple(f"SSE:2025-01-{day:02d}" for day in days),
        )

    for day in (2, 3, 4):
        store.publish_and_register(
            catalog,
            day_request(day),
            pa.Table.from_pylist(
                [
                    {
                        "venue": "SSE",
                        "session_date": date(2025, 1, day),
                        "is_open": True,
                        "open_at": datetime(2025, 1, day, 1, 30, tzinfo=UTC),
                        "close_at": datetime(2025, 1, day, 7, tzinfo=UTC),
                        "event_time": datetime(2025, 1, day, 7, tzinfo=UTC),
                        "known_at": datetime(2025, 1, day, 8, tzinfo=UTC),
                        "source_revision": f"day-{day}",
                    }
                ],
                schema=registry["trade_calendar"].arrow_schema,
            ),
            partition_key="venue=SSE/session_year=2025",
            created_at=datetime(2025, 1, day, 9, tzinfo=UTC),
        )
    portal = CacheFirstDataPortal(
        catalog=catalog,
        store=store,
        registry=registry,
        provider=NoFetchProvider(),
        clock=lambda: datetime(2025, 1, 5, tzinfo=UTC),
    )
    try:
        manifest = portal.snapshot(
            SnapshotRequest(
                datasets=tuple(
                    sorted(
                        (day_request(2, 3), day_request(3, 4)),
                        key=lambda item: item.start,
                    )
                ),
                as_of=datetime(2025, 1, 5, tzinfo=UTC),
            )
        )
        assert len(manifest.request.datasets) == 1
        assert manifest.request.datasets[0].coverage_keys == (
            "SSE:2025-01-02",
            "SSE:2025-01-03",
            "SSE:2025-01-04",
        )
        assert len(manifest.objects) == 3
    finally:
        catalog.close()
