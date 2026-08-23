from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import cast

import duckdb
import pyarrow as pa
import pyarrow.parquet as pq
import pytest
from trademaster.contracts import (
    AvailabilityPolicy,
    DatasetCoverage,
    DatasetRequest,
    FactorContext,
    SnapshotManifest,
    SnapshotObject,
    SnapshotRequest,
    bind_snapshot_provenance,
)
from trademaster.factors.management import ManagedFactorRegistry
from trademaster.factors.public_factors import public_executable_factor_suite
from trademaster.factors.storage import FactorManager, FactorScope

CACHE_ROOT = Path("data/strategies/industry-fundamental-top5-10y")
AS_OF = datetime(2026, 8, 23, tzinfo=UTC)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _instrument_hash(instruments: tuple[str, ...]) -> str:
    return hashlib.sha256(json.dumps(instruments, separators=(",", ":")).encode()).hexdigest()


def _schema_hash(table: pa.Table) -> str:
    return hashlib.sha256(table.schema.remove_metadata().to_string().encode()).hexdigest()


def _event_time(value: str) -> datetime:
    return datetime.strptime(value, "%Y%m%d").replace(hour=7, tzinfo=UTC)


def _snapshot(
    objects: tuple[SnapshotObject, ...],
    requests: tuple[DatasetRequest, ...],
) -> SnapshotManifest:
    by_dataset = {item.dataset: item for item in objects}
    coverages = tuple(
        DatasetCoverage(
            request=request,
            covered_start=request.start,
            covered_end=request.end,
            fields=request.fields,
            resolved_instrument_set_sha256=by_dataset[
                request.dataset
            ].resolved_instrument_set_sha256,
            resolved_instrument_count=by_dataset[request.dataset].resolved_instrument_count,
            object_sha256s=(by_dataset[request.dataset].sha256,),
            row_count=by_dataset[request.dataset].row_count,
            known_at_max=AS_OF,
        )
        for request in requests
    )
    return SnapshotManifest.build(
        request=SnapshotRequest(datasets=requests, as_of=AS_OF),
        availability_policy=AvailabilityPolicy(
            policy_id="real-tushare-cache-smoke/v1",
            known_at_field="cache_verified_at",
            publication_lag_policy_id="raw-object-catalog/v1",
        ),
        objects=objects,
        coverages=coverages,
    )


def _object(
    *,
    dataset: str,
    path: Path,
    table: pa.Table,
    fields: tuple[str, ...],
    instrument: str,
    start: datetime,
    end: datetime,
    coverage_key: str,
) -> SnapshotObject:
    return SnapshotObject(
        dataset=dataset,
        partition=f"instrument={instrument}",
        uri=path.relative_to(CACHE_ROOT).as_posix(),
        sha256=_sha256(path),
        schema_sha256=_schema_hash(table),
        schema_version="real-tushare-raw/v1",
        fields=fields,
        coverage_keys=(coverage_key,),
        resolved_instrument_set_sha256=_instrument_hash((instrument,)),
        resolved_instrument_count=1,
        row_count=table.num_rows,
        event_time_start=start,
        event_time_end=end,
        known_at_max=AS_OF,
    )


@pytest.mark.skipif(not CACHE_ROOT.is_dir(), reason="real Tushare strategy cache is absent")
def test_public_executable_factors_materialize_from_real_tushare_cache(tmp_path: Path) -> None:
    daily_path = next(iter(sorted((CACHE_ROOT / "raw/daily").glob("*.parquet"))))
    daily = pq.read_table(daily_path)
    instruments = set(daily["ts_code"].to_pylist())
    assert len(instruments) == 1
    instrument = str(next(iter(instruments)))

    with duckdb.connect() as connection:
        row = connection.execute(
            """
            SELECT filename FROM read_parquet(?, filename=true)
            WHERE ts_code = ? LIMIT 1
            """,
            [str(CACHE_ROOT / "raw/adj_factor/*.parquet"), instrument],
        ).fetchone()
    assert row is not None
    adj_path = Path(str(row[0]))
    adj = pq.read_table(adj_path)

    with duckdb.connect(str(CACHE_ROOT / "catalog.duckdb"), read_only=True) as catalog:
        raw_rows = catalog.execute(
            """
            SELECT endpoint, relative_path, content_sha256 FROM strategy_requests
            WHERE relative_path IN (?, ?) ORDER BY endpoint
            """,
            [
                daily_path.relative_to(CACHE_ROOT).as_posix(),
                adj_path.relative_to(CACHE_ROOT).as_posix(),
            ],
        ).fetchall()
    assert {(str(row[0]), str(row[2])) for row in raw_rows} == {
        ("daily", _sha256(daily_path)),
        ("adj_factor", _sha256(adj_path)),
    }

    adj_by_date = {str(row["trade_date"]): float(row["adj_factor"]) for row in adj.to_pylist()}
    joined = [
        row
        for row in daily.to_pylist()
        if str(row["trade_date"]) in adj_by_date
        and row["open"] is not None
        and row["close"] is not None
    ]
    joined.sort(key=lambda row: str(row["trade_date"]))
    assert len(joined) > 2_000
    event_times = tuple(_event_time(str(row["trade_date"])) for row in joined)
    factor_inputs = pa.table(
        {
            "instrument_id": [instrument] * len(joined),
            "event_time": pa.array(event_times, type=pa.timestamp("us", tz="UTC")),
            "close": [float(row["close"]) for row in joined],
            "open": [float(row["open"]) for row in joined],
            "adj_factor": [adj_by_date[str(row["trade_date"])] for row in joined],
        }
    )
    start, end = min(event_times), max(event_times)
    daily_key = f"daily:{instrument}"
    adj_key = f"adj_factor:{instrument}"
    daily_request = DatasetRequest(
        dataset="daily_bars",
        start=start,
        end=end,
        instruments=(instrument,),
        fields=("close", "open"),
        coverage_keys=(daily_key,),
    )
    adj_request = DatasetRequest(
        dataset="adj_factors",
        start=start,
        end=end,
        instruments=(instrument,),
        fields=("adj_factor",),
        coverage_keys=(adj_key,),
    )
    snapshot = _snapshot(
        tuple(
            sorted(
                (
                    _object(
                        dataset="daily_bars",
                        path=daily_path,
                        table=daily,
                        fields=("close", "open"),
                        instrument=instrument,
                        start=start,
                        end=end,
                        coverage_key=daily_key,
                    ),
                    _object(
                        dataset="adj_factors",
                        path=adj_path,
                        table=adj,
                        fields=("adj_factor",),
                        instrument=instrument,
                        start=start,
                        end=end,
                        coverage_key=adj_key,
                    ),
                ),
                key=lambda item: (item.dataset, item.partition, item.uri),
            )
        ),
        tuple(sorted((daily_request, adj_request), key=lambda item: item.dataset)),
    )
    context = FactorContext(
        as_of=AS_OF,
        snapshot=snapshot,
        inputs=bind_snapshot_provenance(factor_inputs, snapshot),
    )
    scope = FactorScope(
        start=start,
        end=end,
        as_of=AS_OF,
        instruments=(instrument,),
        coverage_keys=tuple(sorted((daily_key, adj_key))),
    )
    suite = public_executable_factor_suite()
    registry = ManagedFactorRegistry(
        suite.registrations,
        external_dataset_fields=suite.external_dataset_fields,
    )
    fundamental_path = next(
        iter(sorted((CACHE_ROOT / "canonical/fundamental_factor_inputs").glob("*.parquet")))
    )
    fundamental = pq.read_table(fundamental_path)
    fundamental_inputs = fundamental.select(("instrument_id", "event_time", "total_market_value"))
    fundamental_instruments = tuple(
        sorted(set(cast(list[str], fundamental_inputs["instrument_id"].to_pylist())))
    )
    fundamental_times = tuple(cast(list[datetime], fundamental_inputs["event_time"].to_pylist()))
    fundamental_key = f"fundamental-input:{fundamental_times[0].date().isoformat()}"
    fundamental_request = DatasetRequest(
        dataset="fundamental_inputs",
        start=min(fundamental_times),
        end=max(fundamental_times),
        instruments=fundamental_instruments,
        fields=("total_market_value",),
        coverage_keys=(fundamental_key,),
    )
    fundamental_object = SnapshotObject(
        dataset="fundamental_inputs",
        partition=fundamental_key.replace(":", "="),
        uri=fundamental_path.relative_to(CACHE_ROOT).as_posix(),
        sha256=_sha256(fundamental_path),
        schema_sha256=_schema_hash(fundamental),
        schema_version="fundamental-factor-input/v1",
        fields=("total_market_value",),
        coverage_keys=(fundamental_key,),
        resolved_instrument_set_sha256=_instrument_hash(fundamental_instruments),
        resolved_instrument_count=len(fundamental_instruments),
        row_count=fundamental.num_rows,
        event_time_start=min(fundamental_times),
        event_time_end=max(fundamental_times),
        known_at_max=AS_OF,
    )
    fundamental_snapshot = _snapshot((fundamental_object,), (fundamental_request,))
    fundamental_context = FactorContext(
        as_of=AS_OF,
        snapshot=fundamental_snapshot,
        inputs=bind_snapshot_provenance(fundamental_inputs, fundamental_snapshot),
    )
    fundamental_scope = FactorScope(
        start=min(fundamental_times),
        end=max(fundamental_times),
        as_of=AS_OF,
        instruments=fundamental_instruments,
        coverage_keys=(fundamental_key,),
    )
    with FactorManager(
        root=tmp_path / "factors", registry=registry, clock=lambda: AS_OF
    ) as manager:
        artifacts = tuple(
            manager.materialize(
                registration.definition.factor_id,
                registration.definition.version,
                context=context,
                scope=scope,
            )
            for registration in suite.registrations[1:]
        )
        cached = manager.materialize("gtja191.alpha014", "1", context=context, scope=scope)
        size = manager.materialize(
            "huatai53.size.log_total_market_value",
            "1",
            context=fundamental_context,
            scope=fundamental_scope,
        )
        assert cached.from_cache
        assert size.table.num_rows == fundamental.num_rows
        assert all(size.table["is_valid"].to_pylist())
        assert manager.catalog.definition_count() == 11
        assert manager.catalog.materialization_count() == 11

    assert all(artifact.table.num_rows == len(joined) for artifact in artifacts)
    assert all(any(artifact.table["is_valid"].to_pylist()) for artifact in artifacts)
    assert all(
        artifact.manifest.input_snapshot_id == snapshot.snapshot_id for artifact in artifacts
    )
