from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import cast

import pyarrow as pa
from trademaster.contracts import FactorContext, SignalContext, SnapshotRequest
from trademaster.data import (
    CacheFirstDataPortal,
    DataConfig,
    DuckDbCatalog,
    ParquetObjectStore,
    ProviderPage,
    default_dataset_registry,
    normalize_request,
)
from trademaster.factors import FactorExecutor, FactorRegistry, MomentumFactor
from trademaster.signals import (
    ThresholdQuantitySignalConfig,
    ThresholdQuantitySignalGenerator,
    TopNSignalConfig,
    TopNTargetWeightGenerator,
)

T1 = datetime(2025, 1, 2, 7, tzinfo=UTC)
T2 = datetime(2025, 1, 3, 7, tzinfo=UTC)
T3 = datetime(2025, 1, 6, 7, tzinfo=UTC)
CUTOFF = datetime(2025, 1, 6, 8, tzinfo=UTC)
NEXT_OPEN = datetime(2025, 1, 7, 1, 30, tzinfo=UTC)
INSTRUMENTS = ("510300.SH", "600000.SH", "600001.SH")


class NoFetchProvider:
    def fetch(
        self,
        request: object,
        *,
        coverage_keys: tuple[str, ...],
        page_token: str | None,
    ) -> ProviderPage:
        raise AssertionError("integration fixture must be satisfied from Parquet")


def _bar_rows() -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    prices = {
        "600000.SH": (10.0, 11.0, 12.0),
        "600001.SH": (20.0, 18.0, 22.0),
        "510300.SH": (4.0, 4.2, 4.8),
    }
    for instrument_id in INSTRUMENTS:
        for index, (event_time, close) in enumerate(
            zip((T1, T2, T3), prices[instrument_id], strict=True), start=1
        ):
            rows.append(
                {
                    "instrument_id": instrument_id,
                    "trade_date": event_time.date(),
                    "open": close,
                    "high": close,
                    "low": close,
                    "close": close,
                    "volume": 1000.0,
                    "amount": close * 1000.0,
                    "pre_close": close,
                    "venue": "SSE",
                    "event_time": event_time,
                    "known_at": event_time.replace(hour=8),
                    "source_revision": f"fixture-{index}",
                }
            )
    return rows


def test_real_parquet_duckdb_snapshot_drives_cross_section_and_etf_signal(
    tmp_path: Path,
) -> None:
    registry = default_dataset_registry()
    config = DataConfig(
        data_root=tmp_path / "data",
        log_dir=tmp_path / "logs",
        canonical_source_policy="trusted_imports",
        etf_instruments=("510300.SH",),
    )
    config.paths.ensure_layout()
    store = ParquetObjectStore(config.paths, registry)
    catalog = DuckDbCatalog(config.paths.catalog)
    coverage_keys = (
        "SSE:2025-01-02",
        "SSE:2025-01-03",
        "SSE:2025-01-06",
    )
    request = normalize_request(
        registry,
        dataset="daily_bars",
        start=datetime(2025, 1, 2, tzinfo=UTC),
        end=T3,
        instruments=INSTRUMENTS,
        fields=("close",),
        coverage_keys=coverage_keys,
    )
    table = pa.Table.from_pylist(
        _bar_rows(), schema=registry["daily_bars"].arrow_schema
    )
    store.publish_and_register(
        catalog,
        request,
        table,
        partition_key="trade_year=2025/trade_month=01",
        created_at=datetime(2025, 1, 6, 9, tzinfo=UTC),
    )
    portal = CacheFirstDataPortal(
        catalog=catalog,
        store=store,
        registry=registry,
        provider=NoFetchProvider(),
        clock=lambda: datetime(2025, 1, 7, tzinfo=UTC),
    )
    try:
        bars = portal.query(request, as_of=CUTOFF)
        snapshot = portal.snapshot(SnapshotRequest(datasets=(request,), as_of=CUTOFF))
        # query() binds the same deterministic snapshot that snapshot() returns.
        factor = MomentumFactor(lookback_sessions=1)
        factors = FactorExecutor(FactorRegistry((factor,))).compute(
            factor.spec.factor_id,
            factor.spec.version,
            FactorContext(as_of=CUTOFF, snapshot=snapshot, inputs=bars),
        )
        context = SignalContext(
            as_of=CUTOFF,
            eligible_execution_time=NEXT_OPEN,
            snapshot=snapshot,
            factors=factors,
        )

        cross_section = TopNTargetWeightGenerator(
            TopNSignalConfig(
                strategy_id="weekly_momentum",
                factor_id="momentum_1",
                factor_version="1",
                top_n=2,
                minimum_valid_instruments=3,
            )
        ).generate(context)
        etf_signal = ThresholdQuantitySignalGenerator(
            ThresholdQuantitySignalConfig(
                strategy_id="etf_timing",
                instrument_id="510300.SH",
                factor_id="momentum_1",
                factor_version="1",
                buy_above=0.1,
                sell_below=-0.1,
                target_quantity=1000,
            )
        ).generate(context)

        selected = {
            instrument_id
            for instrument_id, weight in zip(
                cross_section["instrument_id"].to_pylist(),
                cross_section["value"].to_pylist(),
                strict=True,
            )
            if cast(Decimal, weight) > Decimal(0)
        }
        assert selected == {"510300.SH", "600001.SH"}
        assert etf_signal["value"].to_pylist() == [Decimal("1000.00000000")]
        assert etf_signal["eligible_execution_time"].to_pylist() == [NEXT_OPEN]
        assert bars.schema.metadata == factors.schema.metadata
        assert factors.schema.metadata == cross_section.schema.metadata
    finally:
        catalog.close()
