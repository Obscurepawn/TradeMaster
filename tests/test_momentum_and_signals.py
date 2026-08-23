from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pyarrow as pa
import pytest
from pydantic import ValidationError
from trademaster.contracts import (
    AvailabilityPolicy,
    FactorContext,
    SignalContext,
    SnapshotManifest,
    SnapshotRequest,
    bind_snapshot_provenance,
)
from trademaster.factors import (
    FactorExecutor,
    FactorRegistry,
    MomentumFactor,
    factor_output_schema,
)
from trademaster.signals import (
    TopNSignalConfig,
    TopNTargetWeightGenerator,
    signal_output_schema,
)

T1 = datetime(2025, 1, 2, 7, tzinfo=UTC)
T2 = datetime(2025, 1, 3, 7, tzinfo=UTC)
T3 = datetime(2025, 1, 6, 7, tzinfo=UTC)
NEXT_OPEN = datetime(2025, 1, 7, 1, 30, tzinfo=UTC)


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


def test_two_session_momentum_matches_hand_calculation_per_instrument() -> None:
    snapshot = _snapshot(T3)
    inputs = bind_snapshot_provenance(
        pa.table(
            {
                "instrument_id": [
                    "600000.SH",
                    "600000.SH",
                    "600000.SH",
                    "600001.SH",
                    "600001.SH",
                    "600001.SH",
                ],
                "event_time": pa.array(
                    [T1, T2, T3, T1, T2, T3],
                    type=pa.timestamp("us", tz="UTC"),
                ),
                "close": [10.0, 11.0, 12.0, 20.0, 18.0, 22.0],
            }
        ),
        snapshot,
    )
    context = FactorContext(as_of=T3, snapshot=snapshot, inputs=inputs)
    factor = MomentumFactor(lookback_sessions=2)
    output = FactorExecutor(FactorRegistry((factor,))).compute(
        factor.spec.factor_id, factor.spec.version, context
    )

    assert output["instrument_id"].to_pylist() == [
        "600000.SH",
        "600000.SH",
        "600000.SH",
        "600001.SH",
        "600001.SH",
        "600001.SH",
    ]
    assert output["is_valid"].to_pylist() == [False, False, True] * 2
    assert output["value"].to_pylist() == pytest.approx(
        [0.0, 0.0, 0.2, 0.0, 0.0, 0.1]
    )


def test_top_n_target_weights_are_stable_exact_and_include_zero_exits() -> None:
    snapshot = _snapshot(T3)
    factors = bind_snapshot_provenance(
        pa.Table.from_pylist(
            [
                {
                    "instrument_id": instrument_id,
                    "event_time": T3,
                    "factor_id": "momentum_2",
                    "factor_version": "1",
                    "value": value,
                    "is_valid": True,
                }
                for instrument_id, value in (
                    ("600000.SH", 0.2),
                    ("600001.SH", 0.1),
                    ("600002.SH", 0.2),
                )
            ],
            schema=factor_output_schema(),
        ),
        snapshot,
    )
    context = SignalContext(
        as_of=T3,
        eligible_execution_time=NEXT_OPEN,
        snapshot=snapshot,
        factors=factors,
    )
    generator = TopNTargetWeightGenerator(
        TopNSignalConfig(
            strategy_id="weekly_momentum",
            factor_id="momentum_2",
            factor_version="1",
            top_n=2,
            minimum_valid_instruments=3,
            ascending=False,
        )
    )
    output = generator.generate(context)

    assert output.schema.remove_metadata() == signal_output_schema()
    assert output["instrument_id"].to_pylist() == [
        "600000.SH",
        "600001.SH",
        "600002.SH",
    ]
    assert output["value"].to_pylist() == [
        Decimal("0.50000000"),
        Decimal("0E-8"),
        Decimal("0.50000000"),
    ]
    assert set(output["intent_type"].to_pylist()) == {"target_weight"}
    assert set(output["eligible_execution_time"].to_pylist()) == {NEXT_OPEN}
    assert output.schema.metadata == factors.schema.metadata


def test_signal_context_cannot_execute_at_signal_close() -> None:
    snapshot = _snapshot(T3)
    factors = bind_snapshot_provenance(
        pa.Table.from_pylist([], schema=factor_output_schema()), snapshot
    )
    with pytest.raises(ValidationError, match="later"):
        SignalContext(
            as_of=T3,
            eligible_execution_time=T3,
            snapshot=snapshot,
            factors=factors,
        )
    valid = SignalContext(
        as_of=T3,
        eligible_execution_time=T3 + timedelta(days=1),
        snapshot=snapshot,
        factors=factors,
    )
    assert valid.eligible_execution_time > valid.as_of
