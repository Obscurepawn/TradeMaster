from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import cast

import pyarrow as pa
import pytest
from pydantic import ValidationError
from trademaster.contracts import (
    AvailabilityPolicy,
    FactorContext,
    FactorSpec,
    SignalContext,
    SignalGenerator,
    SnapshotManifest,
    SnapshotRequest,
    bind_snapshot_provenance,
)
from trademaster.factors import FactorRegistry, factor_output_schema
from trademaster.signals import (
    ThresholdQuantitySignalConfig,
    ThresholdQuantitySignalGenerator,
    TopNSignalConfig,
    TopNTargetWeightGenerator,
)

T1 = datetime(2025, 1, 2, 7, tzinfo=UTC)
T2 = datetime(2025, 1, 3, 7, tzinfo=UTC)
T3 = datetime(2025, 1, 6, 7, tzinfo=UTC)
NEXT_OPEN = T3 + timedelta(days=1)


def _snapshot() -> SnapshotManifest:
    return SnapshotManifest.build(
        request=SnapshotRequest(datasets=(), as_of=T3),
        availability_policy=AvailabilityPolicy(
            policy_id="test/v1",
            known_at_field="known_at",
            publication_lag_policy_id="none/v1",
        ),
        objects=(),
        coverages=(),
    )


def _factor_table(rows: list[dict[str, object]]) -> pa.Table:
    return bind_snapshot_provenance(
        pa.Table.from_pylist(rows, schema=factor_output_schema()), _snapshot()
    )


def test_factor_context_rejects_future_known_at() -> None:
    snapshot = _snapshot()
    table = bind_snapshot_provenance(
        pa.table(
            {
                "event_time": pa.array([T2], type=pa.timestamp("us", tz="UTC")),
                "known_at": pa.array(
                    [T3 + timedelta(seconds=1)],
                    type=pa.timestamp("us", tz="UTC"),
                ),
            }
        ),
        snapshot,
    )
    with pytest.raises(ValidationError, match="future known_at"):
        FactorContext(as_of=T3, snapshot=snapshot, inputs=table)


def test_factor_registry_rejects_unknown_dataset_field_dependency() -> None:
    class UnknownDependencyFactor:
        spec = FactorSpec(
            factor_id="unknown_dependency",
            version="1",
            dependencies=("no_such_dataset.no_such_field",),
            lookback_sessions=0,
        )

        def compute(self, context: FactorContext) -> pa.Table:
            return pa.Table.from_pylist([], schema=factor_output_schema())

    with pytest.raises(ValueError, match="unknown factor dependency"):
        FactorRegistry((UnknownDependencyFactor(),))


def test_top_n_binds_one_factor_identity_and_protocol_signature() -> None:
    rows: list[dict[str, object]] = []
    for instrument_id, alpha, beta in (
        ("600000.SH", 3.0, 1.0),
        ("600001.SH", 2.0, 2.0),
        ("600002.SH", 1.0, 3.0),
    ):
        for factor_id, value in (("alpha", alpha), ("beta", beta)):
            rows.append(
                {
                    "instrument_id": instrument_id,
                    "event_time": T3,
                    "factor_id": factor_id,
                    "factor_version": "1",
                    "value": value,
                    "is_valid": True,
                }
            )
    context = SignalContext(
        as_of=T3,
        eligible_execution_time=NEXT_OPEN,
        snapshot=_snapshot(),
        factors=_factor_table(list(reversed(rows))),
    )
    generator = TopNTargetWeightGenerator(
        TopNSignalConfig(
            strategy_id="alpha_only",
            factor_id="alpha",
            factor_version="1",
            top_n=1,
            minimum_valid_instruments=3,
        )
    )

    assert isinstance(generator, SignalGenerator)
    output = generator.generate(context)
    selected = [
        instrument_id
        for instrument_id, value in zip(
            output["instrument_id"].to_pylist(),
            output["value"].to_pylist(),
            strict=True,
        )
        if cast(Decimal, value) > 0
    ]
    assert selected == ["600000.SH"]


def test_threshold_does_not_replay_crossing_when_latest_observation_is_invalid() -> None:
    factors = _factor_table(
        [
            {
                "instrument_id": "510300.SH",
                "event_time": event_time,
                "factor_id": "timing",
                "factor_version": "1",
                "value": value,
                "is_valid": is_valid,
            }
            for event_time, value, is_valid in (
                (T1, 0.4, True),
                (T2, 0.6, True),
                (T3, 0.0, False),
            )
        ]
    )
    context = SignalContext(
        as_of=T3,
        eligible_execution_time=NEXT_OPEN,
        snapshot=_snapshot(),
        factors=factors,
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

    assert isinstance(generator, SignalGenerator)
    assert generator.generate(context).num_rows == 0
