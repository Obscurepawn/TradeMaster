from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

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
from trademaster.factors import factor_output_schema
from trademaster.factors.evaluation import (
    EvaluationLabelContext,
    FactorEvaluationConfig,
    FactorEvaluationStore,
    FactorEvaluator,
    forward_return_schema,
)
from trademaster.factors.management import FactorRegistration, ManagedFactorRegistry
from trademaster.factors.storage import FactorArtifact, FactorManager, FactorScope

T1 = datetime(2025, 1, 2, 7, tzinfo=UTC)
T2 = datetime(2025, 1, 3, 7, tzinfo=UTC)
T3 = datetime(2025, 1, 6, 7, tzinfo=UTC)
AS_OF = datetime(2025, 2, 1, tzinfo=UTC)
INSTRUMENTS = ("000001.SZ", "000002.SZ", "600001.SH", "600002.SH")


class _InputValueFactor:
    spec = FactorSpec(
        factor_id="evaluation.input_value",
        version="1",
        dependencies=("daily_basic.total_market_value",),
        lookback_sessions=0,
    )

    def compute(self, context: FactorContext) -> pa.Table:
        return pa.Table.from_pylist(
            [
                {
                    "instrument_id": row["instrument_id"],
                    "event_time": row["event_time"],
                    "factor_id": self.spec.factor_id,
                    "factor_version": self.spec.version,
                    "value": row["input_value"],
                    "is_valid": True,
                }
                for row in context.inputs.to_pylist()
            ],
            schema=factor_output_schema(),
        )


def _snapshot() -> SnapshotManifest:
    return SnapshotManifest.build(
        request=SnapshotRequest(datasets=(), as_of=AS_OF),
        availability_policy=AvailabilityPolicy(
            policy_id="evaluation/v1",
            known_at_field="known_at",
            publication_lag_policy_id="none/v1",
        ),
        objects=(),
        coverages=(),
    )


def _label_context() -> EvaluationLabelContext:
    snapshot = SnapshotManifest.build(
        request=SnapshotRequest(datasets=(), as_of=AS_OF),
        availability_policy=AvailabilityPolicy(
            policy_id="evaluation-label/v1",
            known_at_field="known_at",
            publication_lag_policy_id="evaluation-label/v1",
        ),
        objects=(),
        coverages=(),
    )
    return EvaluationLabelContext(
        label_snapshot=snapshot,
        eligible_entry_times=(T2, T3),
    )


def _artifact(tmp_path: Path) -> tuple[FactorManager, FactorArtifact]:
    factor = _InputValueFactor()
    registration = FactorRegistration.create(
        factor,
        family="test",
        description="evaluation fixture",
        parameters={},
        frequency="daily",
        scope="cross_sectional",
        unit="score",
        direction=1,
    )
    snapshot = _snapshot()
    inputs = bind_snapshot_provenance(
        pa.table(
            {
                "instrument_id": list(INSTRUMENTS) * 2,
                "event_time": pa.array(
                    [T1] * len(INSTRUMENTS) + [T2] * len(INSTRUMENTS),
                    type=pa.timestamp("us", tz="UTC"),
                ),
                "input_value": [1.0, 2.0, 3.0, 4.0] * 2,
            }
        ),
        snapshot,
    )
    manager = FactorManager(
        root=tmp_path,
        registry=ManagedFactorRegistry((registration,)),
        clock=lambda: AS_OF,
    )
    artifact = manager.materialize(
        factor.spec.factor_id,
        factor.spec.version,
        context=FactorContext(as_of=AS_OF, snapshot=snapshot, inputs=inputs),
        scope=FactorScope(
            start=T1,
            end=T2,
            as_of=AS_OF,
            instruments=INSTRUMENTS,
            coverage_keys=("2025-01-02", "2025-01-03"),
        ),
    )
    return manager, artifact


def test_factor_evaluator_computes_ic_rank_ic_quantiles_spread_and_turnover(
    tmp_path: Path,
) -> None:
    manager, artifact = _artifact(tmp_path / "factors")
    try:
        label_context = _label_context()
        returns = bind_snapshot_provenance(
            pa.Table.from_pylist(
                [
                    {
                        "instrument_id": instrument,
                        "event_time": event_time,
                        "entry_time": entry_time,
                        "exit_time": exit_time,
                        "horizon_sessions": 1,
                        "return_alignment": "next_eligible_close",
                        "label_snapshot_id": (label_context.label_snapshot.snapshot_id),
                        "forward_return": value,
                        "is_valid": True,
                    }
                    for event_time, entry_time, exit_time, values in (
                        (T1, T2, T3, (0.01, 0.02, 0.03, 0.04)),
                        (
                            T2,
                            T3,
                            datetime(2025, 1, 7, 7, tzinfo=UTC),
                            (0.04, 0.03, 0.02, 0.01),
                        ),
                    )
                    for instrument, value in zip(INSTRUMENTS, values, strict=True)
                ],
                schema=forward_return_schema(),
            ),
            label_context.label_snapshot,
        )
        result = FactorEvaluator().evaluate(
            artifact,
            returns,
            config=FactorEvaluationConfig(
                horizons=(1,),
                quantiles=2,
                minimum_observations=3,
                return_alignment="next_eligible_close",
            ),
            label_context=label_context,
        )

        assert result.summary.coverage == 1.0
        horizon = result.summary.horizons[0]
        assert horizon.mean_ic == pytest.approx(0.0)
        assert horizon.mean_rank_ic == pytest.approx(0.0)
        assert horizon.positive_ic_ratio == pytest.approx(0.5)
        assert horizon.mean_long_short_spread == pytest.approx(0.0)
        assert horizon.mean_top_quantile_turnover == pytest.approx(0.0)
        assert result.ic_table["pearson_ic"].to_pylist() == pytest.approx([1.0, -1.0])
        assert result.ic_table["rank_ic"].to_pylist() == pytest.approx([1.0, -1.0])
        assert result.summary.evaluation_id
        assert result.summary.factor_materialization_id == artifact.manifest.materialization_id
        store = FactorEvaluationStore(
            root=tmp_path / "factors",
            catalog=manager.catalog,
            clock=lambda: AS_OF,
        )
        first_manifest = store.persist(result)
        second_manifest = store.persist(result)
        assert first_manifest == second_manifest
        assert manager.catalog.evaluation_count() == 1
        assert {item.name for item in first_manifest.objects} == {
            "forward_returns",
            "ic",
            "quantiles",
            "summary",
        }
    finally:
        manager.close()


def test_factor_evaluation_rejects_duplicate_or_unrequested_forward_returns(
    tmp_path: Path,
) -> None:
    manager, artifact = _artifact(tmp_path / "factors")
    try:
        label_context = _label_context()
        duplicate = bind_snapshot_provenance(
            pa.Table.from_pylist(
                [
                    {
                        "instrument_id": "000001.SZ",
                        "event_time": T1,
                        "entry_time": T2,
                        "exit_time": T3,
                        "horizon_sessions": 5,
                        "return_alignment": "next_eligible_close",
                        "label_snapshot_id": (label_context.label_snapshot.snapshot_id),
                        "forward_return": value,
                        "is_valid": True,
                    }
                    for value in (0.1, 0.2)
                ],
                schema=forward_return_schema(),
            ),
            label_context.label_snapshot,
        )
        with pytest.raises(ValueError, match="duplicate"):
            FactorEvaluator().evaluate(
                artifact,
                duplicate,
                config=FactorEvaluationConfig(
                    horizons=(5,),
                    quantiles=2,
                    minimum_observations=2,
                    return_alignment="next_eligible_close",
                ),
                label_context=label_context,
            )
    finally:
        manager.close()


def test_factor_evaluation_rejects_declared_alignment_or_same_cycle_entry(
    tmp_path: Path,
) -> None:
    manager, artifact = _artifact(tmp_path / "factors")
    try:
        label_context = _label_context()

        def labels(*, entry_time: datetime, alignment: str) -> pa.Table:
            return bind_snapshot_provenance(
                pa.Table.from_pylist(
                    [
                        {
                            "instrument_id": instrument,
                            "event_time": T1,
                            "entry_time": entry_time,
                            "exit_time": T3,
                            "horizon_sessions": 1,
                            "return_alignment": alignment,
                            "label_snapshot_id": (label_context.label_snapshot.snapshot_id),
                            "forward_return": value,
                            "is_valid": True,
                        }
                        for instrument, value in zip(
                            INSTRUMENTS,
                            (0.01, 0.02, 0.03, 0.04),
                            strict=True,
                        )
                    ],
                    schema=forward_return_schema(),
                ),
                label_context.label_snapshot,
            )

        close_config = FactorEvaluationConfig(
            horizons=(1,),
            quantiles=2,
            minimum_observations=3,
            return_alignment="next_eligible_close",
        )
        with pytest.raises(ValueError, match="timing|alignment"):
            FactorEvaluator().evaluate(
                artifact,
                labels(entry_time=T2, alignment="next_eligible_open"),
                config=close_config,
                label_context=label_context,
            )
        with pytest.raises(ValueError, match="timing|alignment"):
            FactorEvaluator().evaluate(
                artifact,
                labels(entry_time=T1, alignment="next_eligible_close"),
                config=close_config,
                label_context=label_context,
            )
    finally:
        manager.close()
