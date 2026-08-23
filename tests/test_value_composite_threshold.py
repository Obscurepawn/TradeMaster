from __future__ import annotations

import math
from datetime import UTC, datetime
from decimal import Decimal

import pyarrow as pa
import pytest
from trademaster.contracts import (
    AvailabilityPolicy,
    FactorContext,
    SignalContext,
    SnapshotManifest,
    SnapshotRequest,
    bind_snapshot_provenance,
    run_artifact_schemas,
    validate_artifact_table,
)
from trademaster.factors import (
    CompositeComponent,
    CompositeFactor,
    FactorExecutor,
    FactorRegistry,
    ValueFactor,
    factor_output_schema,
)
from trademaster.signals import (
    ThresholdQuantitySignalConfig,
    ThresholdQuantitySignalGenerator,
    signal_output_schema,
)

T1 = datetime(2025, 1, 2, 7, tzinfo=UTC)
T2 = datetime(2025, 1, 3, 7, tzinfo=UTC)
NEXT_OPEN = datetime(2025, 1, 6, 1, 30, tzinfo=UTC)


def _snapshot(as_of: datetime) -> SnapshotManifest:
    return SnapshotManifest.build(
        request=SnapshotRequest(datasets=(), as_of=as_of),
        availability_policy=AvailabilityPolicy(
            policy_id="test/v1",
            known_at_field="known_at",
            publication_lag_policy_id="none/v1",
        ),
        objects=(),
        coverages=(),
    )


def test_value_factor_ranks_smaller_positive_market_value_higher() -> None:
    snapshot = _snapshot(T2)
    inputs = bind_snapshot_provenance(
        pa.table(
            {
                "instrument_id": ["600000.SH", "600001.SH", "600002.SH"],
                "event_time": pa.array(
                    [T2, T2, T2], type=pa.timestamp("us", tz="UTC")
                ),
                "float_market_value": [100.0, 400.0, 0.0],
            }
        ),
        snapshot,
    )
    factor = ValueFactor(market_value_field="float_market_value")
    output = FactorExecutor(FactorRegistry((factor,))).compute(
        factor.spec.factor_id,
        factor.spec.version,
        FactorContext(as_of=T2, snapshot=snapshot, inputs=inputs),
    )

    assert output["value"].to_pylist() == pytest.approx(
        [-math.log(100.0), -math.log(400.0), 0.0]
    )
    assert output["is_valid"].to_pylist() == [True, True, False]


def test_composite_factor_zscores_components_with_direction_and_weight() -> None:
    snapshot = _snapshot(T2)
    rows = []
    for instrument_id, first, second in (
        ("600000.SH", 1.0, 3.0),
        ("600001.SH", 2.0, 2.0),
        ("600002.SH", 3.0, 1.0),
    ):
        rows.extend(
            [
                {
                    "instrument_id": instrument_id,
                    "event_time": T2,
                    "factor_id": "quality",
                    "factor_version": "1",
                    "value": first,
                    "is_valid": True,
                },
                {
                    "instrument_id": instrument_id,
                    "event_time": T2,
                    "factor_id": "leverage",
                    "factor_version": "1",
                    "value": second,
                    "is_valid": True,
                },
            ]
        )
    inputs = bind_snapshot_provenance(
        pa.Table.from_pylist(rows, schema=factor_output_schema()), snapshot
    )
    factor = CompositeFactor(
        factor_id="quality_minus_leverage",
        components=(
            CompositeComponent("quality", "1", weight=Decimal("0.75")),
            CompositeComponent(
                "leverage", "1", weight=Decimal("0.25"), direction=-1
            ),
        ),
        winsor_z=3.0,
    )
    output = FactorExecutor(
        FactorRegistry(
            (factor,),
            external_factor_identities=(("leverage", "1"), ("quality", "1")),
        )
    ).compute(
        factor.spec.factor_id,
        factor.spec.version,
        FactorContext(as_of=T2, snapshot=snapshot, inputs=inputs),
    )

    assert output["instrument_id"].to_pylist() == [
        "600000.SH",
        "600001.SH",
        "600002.SH",
    ]
    assert output["value"].to_pylist() == pytest.approx(
        [-math.sqrt(1.5), 0.0, math.sqrt(1.5)]
    )
    assert output["is_valid"].to_pylist() == [True, True, True]


def test_single_etf_threshold_crossing_emits_target_quantity_for_next_session() -> None:
    snapshot = _snapshot(T2)
    factors = bind_snapshot_provenance(
        pa.Table.from_pylist(
            [
                {
                    "instrument_id": "510300.SH",
                    "event_time": T1,
                    "factor_id": "timing",
                    "factor_version": "1",
                    "value": 0.4,
                    "is_valid": True,
                },
                {
                    "instrument_id": "510300.SH",
                    "event_time": T2,
                    "factor_id": "timing",
                    "factor_version": "1",
                    "value": 0.6,
                    "is_valid": True,
                },
            ],
            schema=factor_output_schema(),
        ),
        snapshot,
    )
    generator = ThresholdQuantitySignalGenerator(
        ThresholdQuantitySignalConfig(
            strategy_id="etf_timing",
            instrument_id="510300.SH",
            factor_id="timing",
            factor_version="1",
            buy_above=0.5,
            sell_below=-0.2,
            target_quantity=1000,
        )
    )
    output = generator.generate(
        SignalContext(
            as_of=T2,
            eligible_execution_time=NEXT_OPEN,
            snapshot=snapshot,
            factors=factors,
        ),
    )

    assert output.schema.remove_metadata() == signal_output_schema()
    assert output.num_rows == 1
    assert output["instrument_id"].to_pylist() == ["510300.SH"]
    assert output["intent_type"].to_pylist() == ["quantity"]
    assert output["value"].to_pylist() == [Decimal("1000.00000000")]
    assert output["reason"].to_pylist() == ["crossed_above_buy_threshold"]
    assert output["eligible_execution_time"].to_pylist() == [NEXT_OPEN]
    assert output.schema.metadata == factors.schema.metadata
    artifact = pa.Table.from_pylist(
        [
            {
                "run_id": "test-run",
                "event_seq": 1,
                "signal_seq": 0,
                **output.to_pylist()[0],
            }
        ],
        schema=run_artifact_schemas()["signals"],
    )
    validate_artifact_table("signals", artifact)


def test_threshold_without_crossing_returns_typed_empty_table() -> None:
    snapshot = _snapshot(T2)
    factors = bind_snapshot_provenance(
        pa.Table.from_pylist(
            [
                {
                    "instrument_id": "600000.SH",
                    "event_time": T1,
                    "factor_id": "timing",
                    "factor_version": "1",
                    "value": 0.6,
                    "is_valid": True,
                },
                {
                    "instrument_id": "600000.SH",
                    "event_time": T2,
                    "factor_id": "timing",
                    "factor_version": "1",
                    "value": 0.7,
                    "is_valid": True,
                },
            ],
            schema=factor_output_schema(),
        ),
        snapshot,
    )
    output = ThresholdQuantitySignalGenerator(
        ThresholdQuantitySignalConfig(
            strategy_id="stock_timing",
            instrument_id="600000.SH",
            factor_id="timing",
            factor_version="1",
            buy_above=0.5,
            sell_below=-0.2,
            target_quantity=1000,
        )
    ).generate(
        SignalContext(
            as_of=T2,
            eligible_execution_time=NEXT_OPEN,
            snapshot=snapshot,
            factors=factors,
        ),
    )

    assert output.num_rows == 0
    assert output.schema.remove_metadata() == signal_output_schema()
    assert output.schema.metadata == factors.schema.metadata
