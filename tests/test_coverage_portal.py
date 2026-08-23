from __future__ import annotations

from datetime import UTC, date, datetime
from pathlib import Path

import duckdb
import pyarrow as pa
import pytest
from trademaster.contracts import DatasetRequest, SnapshotRequest
from trademaster.data import (
    CacheFirstDataPortal,
    CoverageGapError,
    DataConfig,
    DuckDbCatalog,
    ParquetObjectStore,
    ProviderPage,
    RequestResolver,
    default_dataset_registry,
    normalize_request,
)


def _calendar_table(*days: int) -> pa.Table:
    rows = [
        {
            "venue": "SSE",
            "session_date": date(2025, 1, day),
            "is_open": True,
            "open_at": datetime(2025, 1, day, 1, 30, tzinfo=UTC),
            "close_at": datetime(2025, 1, day, 7, tzinfo=UTC),
            "event_time": datetime(2025, 1, day, 7, tzinfo=UTC),
            "known_at": datetime(2025, 1, day, 8, tzinfo=UTC),
            "source_revision": "fixture-v1",
        }
        for day in days
    ]
    return pa.Table.from_pylist(
        rows, schema=default_dataset_registry()["trade_calendar"].arrow_schema
    )


def _request(*days: int) -> DatasetRequest:
    return normalize_request(
        default_dataset_registry(),
        dataset="trade_calendar",
        start=datetime(2025, 1, min(days), tzinfo=UTC),
        end=datetime(2025, 1, max(days), 23, 59, tzinfo=UTC),
        fields=("is_open",),
        coverage_keys=tuple(f"SSE:2025-01-{day:02d}" for day in days),
    )


def _daily_table() -> pa.Table:
    return pa.Table.from_pylist(
        [
            {
                "instrument_id": "600000.SH",
                "trade_date": date(2025, 1, 2),
                "open": 10.0,
                "high": 11.0,
                "low": 9.0,
                "close": 10.5,
                "volume": 1000.0,
                "amount": 10500.0,
                "pre_close": 9.9,
                "venue": "SSE",
                "event_time": datetime(2025, 1, 2, 7, tzinfo=UTC),
                "known_at": datetime(2025, 1, 2, 8, tzinfo=UTC),
                "source_revision": "fixture-v1",
            }
        ],
        schema=default_dataset_registry()["daily_bars"].arrow_schema,
    )


class FakeProvider:
    def __init__(self, pages: dict[str | None, ProviderPage]) -> None:
        self.pages = pages
        self.calls: list[tuple[tuple[str, ...], str | None]] = []

    def fetch(
        self,
        request: DatasetRequest,
        *,
        coverage_keys: tuple[str, ...],
        page_token: str | None,
    ) -> ProviderPage:
        self.calls.append((coverage_keys, page_token))
        return self.pages[page_token]


def _components(
    tmp_path: Path, provider: FakeProvider
) -> tuple[ParquetObjectStore, DuckDbCatalog, CacheFirstDataPortal]:
    config = DataConfig(
        data_root=tmp_path / "data",
        log_dir=tmp_path / "logs",
        canonical_source_policy="trusted_imports",
    )
    config.paths.ensure_layout()
    registry = default_dataset_registry()
    store = ParquetObjectStore(config.paths, registry)
    catalog = DuckDbCatalog(config.paths.catalog)
    portal = CacheFirstDataPortal(
        catalog=catalog,
        store=store,
        registry=registry,
        provider=provider,
        clock=lambda: datetime(2025, 1, 4, tzinfo=UTC),
    )
    return store, catalog, portal


def test_complete_cache_never_calls_provider(tmp_path: Path) -> None:
    provider = FakeProvider({})
    store, catalog, portal = _components(tmp_path, provider)
    try:
        publication = store.publish_and_register(
            catalog,
            _request(2, 3),
            _calendar_table(2, 3),
            partition_key="venue=SSE/session_year=2025",
            created_at=datetime(2025, 1, 3, 9, tzinfo=UTC),
        )

        result = portal.ensure(_request(2, 3))
        assert result.complete
        assert result.covered_object_sha256s == (publication.object.sha256,)
        assert provider.calls == []
    finally:
        catalog.close()


def test_missing_catalog_coverage_proof_forces_provider_refetch(tmp_path: Path) -> None:
    initial_provider = FakeProvider({})
    store, catalog, _ = _components(tmp_path, initial_provider)
    request = _request(2, 3)
    store.publish_and_register(
        catalog,
        request,
        _calendar_table(2, 3),
        partition_key="venue=SSE/session_year=2025",
        created_at=datetime(2025, 1, 3, 9, tzinfo=UTC),
    )
    catalog_path = catalog.path
    catalog.close()
    connection = duckdb.connect(str(catalog_path))
    connection.execute("DELETE FROM coverage")
    connection.close()

    provider = FakeProvider(
        {
            None: ProviderPage(
                table=_calendar_table(2, 3),
                partition_key="venue=SSE/session_year=2025",
                coverage_keys=request.coverage_keys,
                next_page_token=None,
            )
        }
    )
    repaired_catalog = DuckDbCatalog(catalog_path)
    try:
        portal = CacheFirstDataPortal(
            catalog=repaired_catalog,
            store=store,
            registry=default_dataset_registry(),
            provider=provider,
            clock=lambda: datetime(2025, 1, 4, tzinfo=UTC),
        )
        assert portal.ensure(request).complete
        assert provider.calls == [(request.coverage_keys, None)]
    finally:
        repaired_catalog.close()


def test_partial_cache_fetches_only_missing_keys_across_pages(tmp_path: Path) -> None:
    provider = FakeProvider(
        {
            None: ProviderPage(
                table=_calendar_table(3),
                partition_key="venue=SSE/session_year=2025",
                coverage_keys=("SSE:2025-01-03",),
                next_page_token="page-2",
            ),
            "page-2": ProviderPage(
                table=_calendar_table(4),
                partition_key="venue=SSE/session_year=2025",
                coverage_keys=("SSE:2025-01-04",),
                next_page_token=None,
            ),
        }
    )
    store, catalog, portal = _components(tmp_path, provider)
    try:
        store.publish_and_register(
            catalog,
            _request(2),
            _calendar_table(2),
            partition_key="venue=SSE/session_year=2025",
            created_at=datetime(2025, 1, 2, 9, tzinfo=UTC),
        )

        result = portal.ensure(_request(2, 3, 4))
        assert result.complete
        assert len(result.covered_object_sha256s) == 2
        assert provider.calls == [
            (("SSE:2025-01-03", "SSE:2025-01-04"), None),
            (("SSE:2025-01-03", "SSE:2025-01-04"), "page-2"),
        ]
    finally:
        catalog.close()


@pytest.mark.parametrize(
    "pages, message",
    [
        (
            {
                None: ProviderPage(
                    table=_calendar_table(2),
                    partition_key="venue=SSE/session_year=2025",
                    coverage_keys=("SSE:2025-01-02",),
                    next_page_token=None,
                )
            },
            "provider did not return every missing coverage key",
        ),
        (
            {
                None: ProviderPage(
                    table=_calendar_table(2),
                    partition_key="venue=SSE/session_year=2025",
                    coverage_keys=("SSE:2025-01-02",),
                    next_page_token="again",
                ),
                "again": ProviderPage(
                    table=_calendar_table(2),
                    partition_key="venue=SSE/session_year=2025",
                    coverage_keys=("SSE:2025-01-02",),
                    next_page_token=None,
                ),
            },
            "duplicate provider coverage key",
        ),
    ],
)
def test_provider_pagination_gaps_and_duplicates_fail_closed(
    tmp_path: Path,
    pages: dict[str | None, ProviderPage],
    message: str,
) -> None:
    provider = FakeProvider(pages)
    _, catalog, portal = _components(tmp_path, provider)
    try:
        with pytest.raises(CoverageGapError, match=message):
            portal.ensure(_request(2, 3))
        assert catalog.objects("trade_calendar") == ()
    finally:
        catalog.close()


def test_portal_rejects_unresolved_calendar_keys_and_market_universe(
    tmp_path: Path,
) -> None:
    provider = FakeProvider(
        {
            None: ProviderPage(
                table=_calendar_table(2, 3),
                partition_key="venue=SSE/session_year=2025",
                coverage_keys=("SSE:2025-01-02", "SSE:2025-01-03"),
                next_page_token=None,
            )
        }
    )
    _, catalog, portal = _components(tmp_path, provider)
    try:
        unresolved_calendar = DatasetRequest(
            dataset="trade_calendar",
            start=datetime(2025, 1, 2, tzinfo=UTC),
            end=datetime(2025, 1, 3, 23, 59, tzinfo=UTC),
            fields=("is_open",),
        )
        with pytest.raises(CoverageGapError, match="coverage keys"):
            portal.ensure(unresolved_calendar)

        unresolved_market = DatasetRequest(
            dataset="daily_bars",
            start=datetime(2025, 1, 2, tzinfo=UTC),
            end=datetime(2025, 1, 2, 23, 59, tzinfo=UTC),
            fields=("close",),
            coverage_keys=("SSE:2025-01-02",),
        )
        with pytest.raises(CoverageGapError, match="instrument universe"):
            portal.ensure(unresolved_market)

        unresolved_business = DatasetRequest(
            dataset="instrument_master",
            start=datetime(2025, 1, 2, tzinfo=UTC),
            end=datetime(2025, 1, 2, 23, 59, tzinfo=UTC),
            fields=("asset_class",),
            coverage_keys=("600000.SH:2025-01-02",),
        )
        with pytest.raises(CoverageGapError, match="instrument universe"):
            portal.ensure(unresolved_business)
    finally:
        catalog.close()


def test_portal_uses_explicit_request_resolver_before_cache_proof(tmp_path: Path) -> None:
    class Resolver:
        def __init__(self) -> None:
            self.calls: list[DatasetRequest] = []

        def resolve(self, request: DatasetRequest) -> DatasetRequest:
            self.calls.append(request)
            return request.model_copy(
                update={
                    "instruments": ("600000.SH",),
                    "coverage_keys": ("SSE:2025-01-02",),
                }
            )

    resolver = Resolver()
    assert isinstance(resolver, RequestResolver)
    provider = FakeProvider({})
    config = DataConfig(
        data_root=tmp_path / "data",
        log_dir=tmp_path / "logs",
        canonical_source_policy="trusted_imports",
    )
    config.paths.ensure_layout()
    registry = default_dataset_registry()
    store = ParquetObjectStore(config.paths, registry)
    catalog = DuckDbCatalog(config.paths.catalog)
    unresolved = DatasetRequest(
        dataset="daily_bars",
        start=datetime(2025, 1, 2, tzinfo=UTC),
        end=datetime(2025, 1, 2, 23, 59, tzinfo=UTC),
        fields=("close",),
    )
    resolved = resolver.resolve(unresolved)
    resolver.calls.clear()
    store.publish_and_register(
        catalog,
        resolved,
        _daily_table(),
        partition_key="trade_year=2025/trade_month=01",
        created_at=datetime(2025, 1, 2, 9, tzinfo=UTC),
    )
    portal = CacheFirstDataPortal(
        catalog=catalog,
        store=store,
        registry=registry,
        provider=provider,
        clock=lambda: datetime(2025, 1, 4, tzinfo=UTC),
        request_resolver=resolver,
    )
    try:
        result = portal.ensure(unresolved)
        assert result.request == resolved
        assert len(resolver.calls) == 1
        assert provider.calls == []
    finally:
        catalog.close()


def test_request_resolver_cannot_replace_existing_universe(tmp_path: Path) -> None:
    class MaliciousResolver:
        def resolve(self, request: DatasetRequest) -> DatasetRequest:
            return request.model_copy(
                update={
                    "instruments": ("000001.SZ",),
                    "coverage_keys": ("SZSE:2025-01-02",),
                }
            )

    provider = FakeProvider({})
    _, catalog, portal = _components(tmp_path, provider)
    portal.request_resolver = MaliciousResolver()
    request = DatasetRequest(
        dataset="daily_bars",
        start=datetime(2025, 1, 2, tzinfo=UTC),
        end=datetime(2025, 1, 2, 23, 59, tzinfo=UTC),
        instruments=("600000.SH",),
        fields=("close",),
    )
    try:
        with pytest.raises(CoverageGapError, match="existing request scope"):
            portal.ensure(request)
    finally:
        catalog.close()


def test_tampered_request_bound_coverage_proof_forces_refetch(tmp_path: Path) -> None:
    provider = FakeProvider({})
    store, catalog, _ = _components(tmp_path, provider)
    request = _request(2, 3)
    store.publish_and_register(
        catalog,
        request,
        _calendar_table(2, 3),
        partition_key="venue=SSE/session_year=2025",
        created_at=datetime(2025, 1, 3, 9, tzinfo=UTC),
    )
    catalog_path = catalog.path
    catalog.close()
    connection = duckdb.connect(str(catalog_path))
    connection.execute(
        """
        UPDATE coverage
        SET request_sha256 = ?, covered_start = 0, covered_end = 1, verified_at = 0
        """,
        ["f" * 64],
    )
    connection.close()
    repaired = DuckDbCatalog(catalog_path)
    refetch = FakeProvider(
        {
            None: ProviderPage(
                table=_calendar_table(2, 3),
                partition_key="venue=SSE/session_year=2025",
                coverage_keys=request.coverage_keys,
                next_page_token=None,
            )
        }
    )
    try:
        portal = CacheFirstDataPortal(
            catalog=repaired,
            store=store,
            registry=default_dataset_registry(),
            provider=refetch,
            clock=lambda: datetime(2025, 1, 4, tzinfo=UTC),
        )
        assert portal.ensure(request).complete
        assert refetch.calls == [(request.coverage_keys, None)]
    finally:
        repaired.close()


@pytest.mark.parametrize("replacement_stays_suspended", [False, True])
def test_sparse_daily_bars_require_and_bind_suspension_absence_proof(
    tmp_path: Path,
    replacement_stays_suspended: bool,
) -> None:
    registry = default_dataset_registry()
    instruments = ("600000.SH", "600001.SH")
    coverage_keys = ("SSE:2025-01-02",)
    status_rows = [
        {
            "instrument_id": instrument_id,
            "trade_date": date(2025, 1, 2),
            "up_limit": 11.0,
            "down_limit": 9.0,
            "suspended": instrument_id == "600001.SH",
            "is_st": False,
            "venue": "SSE",
            "event_time": datetime(2025, 1, 2, 1, 20, tzinfo=UTC),
            "known_at": datetime(2025, 1, 2, 1, 20, tzinfo=UTC),
            "source_revision": instrument_id,
        }
        for instrument_id in instruments
    ]
    bar_table = _daily_table()

    class SparseProvider:
        def fetch(
            self,
            request: DatasetRequest,
            *,
            coverage_keys: tuple[str, ...],
            page_token: str | None,
        ) -> ProviderPage:
            table = (
                pa.Table.from_pylist(
                    [
                        row
                        for row in status_rows
                        if row["instrument_id"] in request.instruments
                    ],
                    schema=registry["daily_limits_status"].arrow_schema,
                )
                if request.dataset == "daily_limits_status"
                else bar_table
            )
            return ProviderPage(
                table=table,
                partition_key="trade_year=2025/trade_month=01",
                coverage_keys=coverage_keys,
                next_page_token=None,
            )

    config = DataConfig(
        data_root=tmp_path / "data",
        log_dir=tmp_path / "logs",
        canonical_source_policy="trusted_imports",
    )
    config.paths.ensure_layout()
    store = ParquetObjectStore(config.paths, registry)
    catalog = DuckDbCatalog(config.paths.catalog)
    portal = CacheFirstDataPortal(
        catalog=catalog,
        store=store,
        registry=registry,
        provider=SparseProvider(),
        clock=lambda: datetime(2025, 1, 3, tzinfo=UTC),
    )
    request = DatasetRequest(
        dataset="daily_bars",
        start=datetime(2025, 1, 2, tzinfo=UTC),
        end=datetime(2025, 1, 2, 23, 59, tzinfo=UTC),
        instruments=instruments,
        fields=("close",),
        coverage_keys=coverage_keys,
    )
    try:
        result = portal.ensure(request)
        assert result.complete
        obj = catalog.objects("daily_bars")[0]
        publication = store.verify_publication(obj)
        assert len(publication.absence_proofs) == 1
        assert publication.absence_proofs[0].startswith(
            "600001.SH|SSE:2025-01-02|suspended|"
        )
        wider_status_request = DatasetRequest(
            dataset="daily_limits_status",
            start=request.start,
            end=request.end,
            instruments=request.instruments,
            fields=("is_st", "suspended"),
            coverage_keys=request.coverage_keys,
        )
        manifest = portal.snapshot(
            SnapshotRequest(
                datasets=tuple(
                    sorted(
                        (request, wider_status_request),
                        key=lambda item: item.dataset,
                    )
                ),
                as_of=datetime(2025, 1, 3, tzinfo=UTC),
            )
        )
        assert len({obj.sha256 for obj in manifest.objects}) == len(manifest.objects)
        revised_status = pa.Table.from_pylist(
            [
                {
                    **row,
                    "suspended": (
                        replacement_stays_suspended
                        if row["instrument_id"] == "600001.SH"
                        else False
                    ),
                    "known_at": datetime(2025, 1, 2, 2, tzinfo=UTC),
                    "source_revision": f'revised-{row["instrument_id"]}',
                }
                for row in status_rows
            ],
            schema=registry["daily_limits_status"].arrow_schema,
        )
        status_request = DatasetRequest(
            dataset="daily_limits_status",
            start=request.start,
            end=request.end,
            instruments=request.instruments,
            fields=("suspended",),
            coverage_keys=request.coverage_keys,
        )
        store.publish_and_register(
            catalog,
            status_request,
            revised_status,
            partition_key="trade_year=2025/trade_month=01",
            created_at=datetime(2025, 1, 3, 1, tzinfo=UTC),
        )
        with pytest.raises(
            CoverageGapError,
            match="exact absence evidence|outside selected object scope",
        ):
            portal.query(request, as_of=datetime(2025, 1, 3, 2, tzinfo=UTC))
    finally:
        catalog.close()


def test_all_suspended_session_publishes_and_queries_an_empty_bar_object(
    tmp_path: Path,
) -> None:
    registry = default_dataset_registry()
    instruments = ("600000.SH", "600001.SH")
    coverage_keys = ("SSE:2025-01-02",)
    status_table = pa.Table.from_pylist(
        [
            {
                "instrument_id": instrument_id,
                "trade_date": date(2025, 1, 2),
                "up_limit": 11.0,
                "down_limit": 9.0,
                "suspended": True,
                "is_st": False,
                "venue": "SSE",
                "event_time": datetime(2025, 1, 2, 1, 20, tzinfo=UTC),
                "known_at": datetime(2025, 1, 2, 1, 20, tzinfo=UTC),
                "source_revision": instrument_id,
            }
            for instrument_id in instruments
        ],
        schema=registry["daily_limits_status"].arrow_schema,
    )
    empty_bars = pa.Table.from_pylist(
        [], schema=registry["daily_bars"].arrow_schema
    )

    class AllSuspendedProvider:
        def fetch(
            self,
            request: DatasetRequest,
            *,
            coverage_keys: tuple[str, ...],
            page_token: str | None,
        ) -> ProviderPage:
            return ProviderPage(
                table=(
                    status_table
                    if request.dataset == "daily_limits_status"
                    else empty_bars
                ),
                partition_key="trade_year=2025/trade_month=01",
                coverage_keys=coverage_keys,
                next_page_token=None,
            )

    config = DataConfig(
        data_root=tmp_path / "data",
        log_dir=tmp_path / "logs",
        canonical_source_policy="trusted_imports",
    )
    config.paths.ensure_layout()
    store = ParquetObjectStore(config.paths, registry)
    catalog = DuckDbCatalog(config.paths.catalog)
    portal = CacheFirstDataPortal(
        catalog=catalog,
        store=store,
        registry=registry,
        provider=AllSuspendedProvider(),
        clock=lambda: datetime(2025, 1, 3, tzinfo=UTC),
    )
    request = DatasetRequest(
        dataset="daily_bars",
        start=datetime(2025, 1, 2, tzinfo=UTC),
        end=datetime(2025, 1, 2, 23, 59, tzinfo=UTC),
        instruments=instruments,
        fields=("close",),
        coverage_keys=coverage_keys,
    )
    try:
        assert portal.ensure(request).complete
        publication = store.verify_publication(catalog.objects("daily_bars")[0])
        assert publication.object.row_count == 0
        assert len(publication.absence_proofs) == 2
        result = portal.query(request, as_of=datetime(2025, 1, 3, tzinfo=UTC))
        assert result.num_rows == 0
        assert result.schema.metadata is not None
        assert b"trademaster.snapshot_id" in result.schema.metadata
    finally:
        catalog.close()


def test_latest_status_revision_inside_one_object_controls_absence_proof(
    tmp_path: Path,
) -> None:
    registry = default_dataset_registry()
    key = ("SSE:2025-01-02",)
    status = pa.Table.from_pylist(
        [
            {
                "instrument_id": "600000.SH",
                "trade_date": date(2025, 1, 2),
                "up_limit": 11.0,
                "down_limit": 9.0,
                "suspended": suspended,
                "is_st": False,
                "venue": "SSE",
                "event_time": datetime(2025, 1, 2, 1, 20, tzinfo=UTC),
                "known_at": known_at,
                "source_revision": revision,
            }
            for suspended, known_at, revision in (
                (True, datetime(2025, 1, 2, 1, 20, tzinfo=UTC), "old-true"),
                (False, datetime(2025, 1, 2, 2, tzinfo=UTC), "new-false"),
            )
        ],
        schema=registry["daily_limits_status"].arrow_schema,
    )
    bars = pa.Table.from_pylist([], schema=registry["daily_bars"].arrow_schema)

    class Provider:
        def fetch(
            self,
            request: DatasetRequest,
            *,
            coverage_keys: tuple[str, ...],
            page_token: str | None,
        ) -> ProviderPage:
            return ProviderPage(
                table=status if request.dataset == "daily_limits_status" else bars,
                partition_key="trade_year=2025/trade_month=01",
                coverage_keys=key,
                next_page_token=None,
            )

    config = DataConfig(
        data_root=tmp_path / "data",
        log_dir=tmp_path / "logs",
        canonical_source_policy="trusted_imports",
    )
    config.paths.ensure_layout()
    catalog = DuckDbCatalog(config.paths.catalog)
    portal = CacheFirstDataPortal(
        catalog=catalog,
        store=ParquetObjectStore(config.paths, registry),
        registry=registry,
        provider=Provider(),
        clock=lambda: datetime(2025, 1, 3, tzinfo=UTC),
    )
    request = DatasetRequest(
        dataset="daily_bars",
        start=datetime(2025, 1, 2, tzinfo=UTC),
        end=datetime(2025, 1, 2, 23, 59, tzinfo=UTC),
        instruments=("600000.SH",),
        fields=("close",),
        coverage_keys=key,
    )
    try:
        with pytest.raises(CoverageGapError, match="suspension absence proof"):
            portal.ensure(request)
        assert catalog.objects("daily_bars") == ()
    finally:
        catalog.close()


def test_absence_freshness_reads_cutoff_eligible_rows_in_mixed_object(
    tmp_path: Path,
) -> None:
    registry = default_dataset_registry()
    instruments = ("600000.SH", "600001.SH")
    key = ("SSE:2025-01-02",)
    config = DataConfig(
        data_root=tmp_path / "data",
        log_dir=tmp_path / "logs",
        canonical_source_policy="trusted_imports",
    )
    config.paths.ensure_layout()
    store = ParquetObjectStore(config.paths, registry)
    catalog = DuckDbCatalog(config.paths.catalog)
    status_request = DatasetRequest(
        dataset="daily_limits_status",
        start=datetime(2025, 1, 2, tzinfo=UTC),
        end=datetime(2025, 1, 2, 23, 59, tzinfo=UTC),
        instruments=instruments,
        fields=("suspended",),
        coverage_keys=key,
    )

    def status_table(
        rows: tuple[tuple[str, bool, datetime, str], ...]
    ) -> pa.Table:
        return pa.Table.from_pylist(
            [
                {
                    "instrument_id": instrument_id,
                    "trade_date": date(2025, 1, 2),
                    "up_limit": 11.0,
                    "down_limit": 9.0,
                    "suspended": suspended,
                    "is_st": False,
                    "venue": "SSE",
                    "event_time": datetime(2025, 1, 2, 1, 20, tzinfo=UTC),
                    "known_at": known_at,
                    "source_revision": revision,
                }
                for instrument_id, suspended, known_at, revision in rows
            ],
            schema=registry["daily_limits_status"].arrow_schema,
        )

    original = store.publish_and_register(
        catalog,
        status_request,
        status_table(
            (
                (instruments[0], False, datetime(2025, 1, 2, 1, 20, tzinfo=UTC), "a"),
                (instruments[1], True, datetime(2025, 1, 2, 1, 20, tzinfo=UTC), "b"),
            )
        ),
        partition_key="trade_year=2025/trade_month=01",
        created_at=datetime(2025, 1, 2, 2, tzinfo=UTC),
    )
    bars_request = DatasetRequest(
        dataset="daily_bars",
        start=status_request.start,
        end=status_request.end,
        instruments=instruments,
        fields=("close",),
        coverage_keys=key,
    )
    proof = f"{instruments[1]}|{key[0]}|suspended|{original.object.sha256}"
    store.publish_and_register(
        catalog,
        bars_request,
        _daily_table(),
        partition_key="trade_year=2025/trade_month=01",
        created_at=datetime(2025, 1, 2, 3, tzinfo=UTC),
        absence_proofs=(proof,),
    )
    portal = CacheFirstDataPortal(
        catalog=catalog,
        store=store,
        registry=registry,
        provider=FakeProvider({}),
        clock=lambda: datetime(2025, 1, 5, tzinfo=UTC),
    )
    manifest = portal.snapshot(
        SnapshotRequest(
            datasets=tuple(
                sorted((bars_request, status_request), key=lambda item: item.dataset)
            ),
            as_of=datetime(2025, 1, 3, tzinfo=UTC),
        )
    )
    store.publish_and_register(
        catalog,
        status_request,
        status_table(
            (
                (instruments[1], False, datetime(2025, 1, 2, 2, tzinfo=UTC), "corrected"),
                (instruments[0], False, datetime(2025, 1, 5, 2, tzinfo=UTC), "future"),
            )
        ),
        partition_key="trade_year=2025/trade_month=01",
        created_at=datetime(2025, 1, 5, 3, tzinfo=UTC),
    )
    try:
        with pytest.raises(
            CoverageGapError,
            match="exact absence evidence|outside selected object scope",
        ):
            portal.load_snapshot(manifest.snapshot_id)
        with pytest.raises(CoverageGapError, match="straddles PIT cutoff"):
            portal.snapshot(
                SnapshotRequest(
                    datasets=tuple(
                        sorted((bars_request, status_request), key=lambda item: item.dataset)
                    ),
                    as_of=datetime(2025, 1, 3, tzinfo=UTC),
                )
            )
    finally:
        catalog.close()
