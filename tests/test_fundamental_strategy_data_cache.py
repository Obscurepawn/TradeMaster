from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pyarrow as pa
import pytest
from trademaster.strategies.industry_fundamental_top5.data import (
    DataCacheError,
    StrategyDataCache,
)


class FakeClient:
    def __init__(self) -> None:
        self.calls: list[tuple[str, int]] = []
        schema_fields: dict[str, pa.DataType] = {
            "ts_code": pa.string(),
            "value": pa.float64(),
        }
        self.schema = pa.schema(schema_fields)

    def query(
        self,
        api_name: str,
        *,
        fields: str,
        limit: int,
        offset: int,
        **params: object,
    ) -> pa.Table:
        del fields, params
        self.calls.append((api_name, offset))
        rows = (
            [{"ts_code": "000001.SZ", "value": 1.0}, {"ts_code": "600000.SH", "value": 2.0}]
            if offset == 0
            else []
        )
        return pa.Table.from_pylist(rows, schema=self.schema)


def test_cache_rejects_filesystem_root_as_storage_root(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="filesystem root"):
        StrategyDataCache(
            root=Path(tmp_path.anchor),
            client=FakeClient(),
            clock=lambda: datetime(2026, 8, 23, 1, tzinfo=UTC),
        )


def test_cache_queries_duckdb_and_parquet_before_tushare_and_rejects_corruption(
    tmp_path: Path,
) -> None:
    client = FakeClient()
    cache = StrategyDataCache(
        root=tmp_path,
        client=client,
        clock=lambda: datetime(2026, 8, 12, 1, tzinfo=UTC),
    )
    try:
        first_result = cache.query_with_evidence(
            "daily_basic",
            params={"trade_date": "20250506"},
            fields=("ts_code", "value"),
            page_limit=2,
        )
        first = first_result.table
        calls_after_first = tuple(client.calls)
        second = cache.query(
            "daily_basic",
            params={"trade_date": "20250506"},
            fields=("ts_code", "value"),
            page_limit=2,
        )

        assert first.equals(second)
        assert first_result.evidence.endpoint == "daily_basic"
        assert first_result.evidence.path == cache.cached_objects()[0]
        assert len(first_result.evidence.request_sha256) == 64
        assert len(first_result.evidence.content_sha256) == 64
        assert calls_after_first == (("daily_basic", 0), ("daily_basic", 2))
        assert tuple(client.calls) == calls_after_first
        cached_path = cache.cached_objects()[0]
        assert cached_path.suffix == ".parquet"
        assert cached_path.is_file()

        cached_path.write_bytes(b"corrupt")
        with pytest.raises(DataCacheError, match="hash"):
            cache.query(
                "daily_basic",
                params={"trade_date": "20250506"},
                fields=("ts_code", "value"),
                page_limit=2,
            )
    finally:
        cache.close()


@pytest.mark.parametrize("endpoint", ("../../outside", "/tmp/absolute-endpoint", "bad/name"))
def test_cache_rejects_endpoint_paths_that_are_not_stable_identifiers(
    tmp_path: Path,
    endpoint: str,
) -> None:
    cache = StrategyDataCache(
        root=tmp_path / "cache",
        client=FakeClient(),
        clock=lambda: datetime(2026, 8, 23, 1, tzinfo=UTC),
    )
    try:
        with pytest.raises(ValueError, match="endpoint"):
            cache.query(
                endpoint,
                params={},
                fields=("ts_code", "value"),
                page_limit=2,
            )
    finally:
        cache.close()


@pytest.mark.parametrize("dataset", ("../../outside", "/tmp/absolute-dataset", "bad/name"))
def test_cache_rejects_dataset_paths_that_are_not_stable_identifiers(
    tmp_path: Path,
    dataset: str,
) -> None:
    cache = StrategyDataCache(
        root=tmp_path / "cache",
        client=FakeClient(),
        clock=lambda: datetime(2026, 8, 23, 1, tzinfo=UTC),
    )
    try:
        table = pa.Table.from_pylist([{"value": 1.0}])
        with pytest.raises(ValueError, match="dataset"):
            cache.publish_canonical(dataset, table)
    finally:
        cache.close()
