from __future__ import annotations

from datetime import UTC, datetime

import pyarrow as pa
import pytest
from trademaster.contracts import (
    AvailabilityPolicy,
    FactorContext,
    FactorSpec,
    SnapshotManifest,
    SnapshotRequest,
    bind_snapshot_provenance,
)
from trademaster.factors import (
    FactorExecutor,
    FactorOutputError,
    FactorRegistry,
    factor_output_schema,
)

NOW = datetime(2025, 1, 2, 7, tzinfo=UTC)


def _snapshot() -> SnapshotManifest:
    return SnapshotManifest.build(
        request=SnapshotRequest(datasets=(), as_of=NOW),
        availability_policy=AvailabilityPolicy(
            policy_id="test/v1",
            known_at_field="known_at",
            publication_lag_policy_id="none/v1",
        ),
        objects=(),
        coverages=(),
    )


class ConstantFactor:
    spec = FactorSpec(
        factor_id="constant",
        version="1",
        dependencies=("daily_bars.close",),
        lookback_sessions=0,
    )

    def compute(self, context: FactorContext) -> pa.Table:
        return pa.Table.from_pylist(
            [
                {
                    "instrument_id": "600000.SH",
                    "event_time": NOW,
                    "factor_id": self.spec.factor_id,
                    "factor_version": self.spec.version,
                    "value": 1.5,
                    "is_valid": True,
                }
            ],
            schema=factor_output_schema(),
        )


def test_factor_registry_has_stable_versioned_identity() -> None:
    factor = ConstantFactor()
    registry = FactorRegistry((factor,))

    assert registry.get("constant", "1") is factor
    assert registry.identities == (("constant", "1"),)
    with pytest.raises(ValueError, match="duplicate factor"):
        FactorRegistry((factor, factor))
    with pytest.raises(KeyError, match="unknown factor"):
        registry.get("missing", "1")


def test_factor_executor_freezes_schema_and_snapshot_provenance() -> None:
    snapshot = _snapshot()
    inputs = bind_snapshot_provenance(
        pa.table(
            {
                "event_time": pa.array([NOW], type=pa.timestamp("us", tz="UTC"))
            }
        ),
        snapshot,
    )
    context = FactorContext(as_of=NOW, snapshot=snapshot, inputs=inputs)
    output = FactorExecutor(FactorRegistry((ConstantFactor(),))).compute(
        "constant", "1", context
    )

    assert output.schema.remove_metadata() == factor_output_schema()
    assert output["value"].to_pylist() == [1.5]
    assert output.schema.metadata == inputs.schema.metadata


def test_factor_executor_rejects_output_identity_or_schema_drift() -> None:
    class DriftedFactor(ConstantFactor):
        def compute(self, context: FactorContext) -> pa.Table:
            return pa.table(
                {
                    "instrument_id": ["600000.SH"],
                    "event_time": pa.array(
                        [NOW], type=pa.timestamp("us", tz="UTC")
                    ),
                    "factor_id": ["wrong"],
                    "factor_version": ["1"],
                    "value": [1.0],
                    "is_valid": [True],
                }
            )

    snapshot = _snapshot()
    context = FactorContext(
        as_of=NOW,
        snapshot=snapshot,
        inputs=bind_snapshot_provenance(
            pa.table(
                {
                    "event_time": pa.array(
                        [NOW], type=pa.timestamp("us", tz="UTC")
                    )
                }
            ),
            snapshot,
        ),
    )
    with pytest.raises(FactorOutputError, match="schema"):
        FactorExecutor(FactorRegistry((DriftedFactor(),))).compute(
            "constant", "1", context
        )
