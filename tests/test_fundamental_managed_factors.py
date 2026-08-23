from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from typing import Any, cast

import pyarrow as pa
import pyarrow.compute as pc
import pytest
from trademaster.contracts import (
    AvailabilityPolicy,
    FactorContext,
    SnapshotManifest,
    SnapshotRequest,
    bind_snapshot_provenance,
)
from trademaster.factors import FactorExecutor, FactorRegistry
from trademaster.factors.cross_section import (
    CrossSectionCompositeFactor,
    CrossSectionTransformSpec,
    WeightedFactorComponent,
    assemble_grouped_factor_inputs,
    composite_valid_counts,
)
from trademaster.factors.fundamental import fundamental_factor_suite
from trademaster.factors.management import ManagedFactorRegistry

T1 = datetime(2025, 5, 6, 7, tzinfo=UTC)
T2 = datetime(2025, 11, 6, 7, tzinfo=UTC)


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


def _wide_inputs(event_time: datetime = T1) -> pa.Table:
    fields: list[Any] = [
        pa.field("instrument_id", pa.string(), nullable=False),
        pa.field("event_time", pa.timestamp("us", tz="UTC"), nullable=False),
        pa.field("industry_id", pa.string(), nullable=False),
        *[
            pa.field(name, pa.float64(), nullable=True)
            for name in (
                "pe_ttm",
                "pb",
                "dv_ttm",
                "roe",
                "grossprofit_margin",
                "ocf_to_or",
                "q_sales_yoy",
                "q_profit_yoy",
                "debt_to_assets",
            )
        ],
    ]
    return pa.Table.from_pylist(
        [
            {
                "instrument_id": "000001.SZ",
                "event_time": event_time,
                "industry_id": "bank",
                "pe_ttm": 6.0,
                "pb": 0.7,
                "dv_ttm": 5.0,
                "roe": 20.0,
                "grossprofit_margin": 45.0,
                "ocf_to_or": 35.0,
                "q_sales_yoy": 20.0,
                "q_profit_yoy": 25.0,
                "debt_to_assets": 25.0,
            },
            {
                "instrument_id": "000002.SZ",
                "event_time": event_time,
                "industry_id": "bank",
                "pe_ttm": 10.0,
                "pb": 1.0,
                "dv_ttm": 2.0,
                "roe": 15.0,
                "grossprofit_margin": 30.0,
                "ocf_to_or": 20.0,
                "q_sales_yoy": 10.0,
                "q_profit_yoy": 12.0,
                "debt_to_assets": 40.0,
            },
            {
                "instrument_id": "000003.SZ",
                "event_time": event_time,
                "industry_id": "bank",
                "pe_ttm": 30.0,
                "pb": 4.0,
                "dv_ttm": 0.0,
                "roe": 5.0,
                "grossprofit_margin": 10.0,
                "ocf_to_or": -5.0,
                "q_sales_yoy": -10.0,
                "q_profit_yoy": -20.0,
                "debt_to_assets": 80.0,
            },
        ],
        schema=pa.schema(fields),
    )


def _atomic_outputs(inputs: pa.Table, snapshot: SnapshotManifest) -> tuple[pa.Table, ...]:
    outputs: list[pa.Table] = []
    suite = fundamental_factor_suite()
    context = FactorContext(
        as_of=snapshot.as_of,
        snapshot=snapshot,
        inputs=bind_snapshot_provenance(inputs, snapshot),
    )
    for registration in suite.atomic:
        outputs.append(
            FactorExecutor(
                FactorRegistry(
                    (registration.factor,),
                    external_dataset_fields=suite.external_dataset_fields,
                )
            ).compute(
                registration.definition.factor_id,
                registration.definition.version,
                context,
            )
        )
    return tuple(outputs)


def test_nine_managed_fundamental_factors_preserve_documented_directions() -> None:
    snapshot = _snapshot(T1)
    outputs = _atomic_outputs(_wide_inputs(), snapshot)
    by_factor = {table["factor_id"][0].as_py(): table.to_pylist() for table in outputs}

    assert len(by_factor) == 9
    assert by_factor["fundamental.earnings_yield"][0]["value"] == pytest.approx(1 / 6)
    assert by_factor["fundamental.book_yield"][0]["value"] == pytest.approx(1 / 0.7)
    assert by_factor["fundamental.debt_safety"][0]["value"] == -25.0
    assert all(row["is_valid"] for rows in by_factor.values() for row in rows)

    suite = fundamental_factor_suite()
    registry = ManagedFactorRegistry(
        suite.registrations,
        external_dataset_fields=suite.external_dataset_fields,
    )
    assert len(registry.identities) == 11
    assert len(registry.plan("fundamental.composite.industry_relative", "1")) == 10


def test_grouped_composite_matches_legacy_winsor_zscore_and_missing_policy() -> None:
    snapshot = _snapshot(T1)
    wide = _wide_inputs()
    suite = fundamental_factor_suite()
    atomic = _atomic_outputs(wide, snapshot)
    grouped = assemble_grouped_factor_inputs(
        atomic,
        dimensions=wide.select(("instrument_id", "event_time", "industry_id")),
        group_field="industry_id",
    )
    composite = suite.industry_composite.factor
    output = FactorExecutor(
        FactorRegistry(
            (composite,),
            external_factor_identities=tuple(
                sorted(item.definition.identity for item in suite.atomic)
            ),
        )
    ).compute(
        composite.spec.factor_id,
        composite.spec.version,
        FactorContext(as_of=T1, snapshot=snapshot, inputs=grouped),
    )

    values = cast(list[float], output["value"].to_pylist())
    assert values[0] > values[1] > values[2]
    assert values == pytest.approx([1.1208573338042511, 0.1738094620398379, -1.294666795844089])
    assert composite_valid_counts(grouped, suite.industry_components) == {
        (T1, "000001.SZ"): 9,
        (T1, "000002.SZ"): 9,
        (T1, "000003.SZ"): 9,
    }


def test_grouped_composite_isolates_event_times_and_requires_minimum_components() -> None:
    snapshot = _snapshot(T2)
    first = _wide_inputs(T1)
    future = _wide_inputs(T2).set_column(
        _wide_inputs(T2).schema.get_field_index("roe"),
        "roe",
        pa.array([10_000.0, 15.0, 5.0]),
    )
    wide = pa.concat_tables((first, future))
    suite = fundamental_factor_suite()
    atomic = _atomic_outputs(wide, snapshot)
    grouped = assemble_grouped_factor_inputs(
        atomic,
        dimensions=wide.select(("instrument_id", "event_time", "industry_id")),
        group_field="industry_id",
    )
    output = suite.industry_composite.factor.compute(
        FactorContext(as_of=T2, snapshot=snapshot, inputs=grouped)
    )

    t1_values = [row["value"] for row in output.to_pylist() if row["event_time"] == T1]
    baseline_snapshot = _snapshot(T1)
    baseline_atomic = _atomic_outputs(first, baseline_snapshot)
    baseline_grouped = assemble_grouped_factor_inputs(
        baseline_atomic,
        dimensions=first.select(("instrument_id", "event_time", "industry_id")),
        group_field="industry_id",
    )
    baseline = suite.industry_composite.factor.compute(
        FactorContext(as_of=T1, snapshot=baseline_snapshot, inputs=baseline_grouped)
    )
    assert t1_values == baseline["value"].to_pylist()

    sparse = grouped.filter(
        pc.not_equal(grouped["factor_id"], pa.scalar("fundamental.profit_growth"))
    )
    sparse = sparse.filter(pc.not_equal(sparse["factor_id"], pa.scalar("fundamental.sales_growth")))
    strict = CrossSectionCompositeFactor(
        factor_id="strict",
        version="1",
        components=tuple(
            WeightedFactorComponent(
                factor_id=item.definition.factor_id,
                factor_version=item.definition.version,
                weight=Decimal("0.11111111"),
                required=False,
            )
            for item in suite.atomic
        ),
        transform=CrossSectionTransformSpec(
            group_field="industry_id",
            winsor_lower=0.05,
            winsor_upper=0.95,
            minimum_valid_components=8,
            missing_policy="neutral_zero",
        ),
    )
    strict_output = strict.compute(FactorContext(as_of=T2, snapshot=snapshot, inputs=sparse))
    assert strict_output["is_valid"].to_pylist() == [False] * 6


def test_ineligible_extreme_record_does_not_pollute_eligible_cross_section() -> None:
    snapshot = _snapshot(T1)
    baseline_wide = _wide_inputs()
    extreme = pa.Table.from_pylist(
        [
            {
                "instrument_id": "000099.SZ",
                "event_time": T1,
                "industry_id": "bank",
                "pe_ttm": -1.0,
                "pb": 1.0,
                "dv_ttm": 1_000_000.0,
                "roe": 1_000_000.0,
                "grossprofit_margin": 1_000_000.0,
                "ocf_to_or": 1_000_000.0,
                "q_sales_yoy": 1_000_000.0,
                "q_profit_yoy": 1_000_000.0,
                "debt_to_assets": 1.0,
            }
        ],
        schema=baseline_wide.schema,
    )
    suite = fundamental_factor_suite()

    def scores(wide: pa.Table) -> list[float]:
        atomic = _atomic_outputs(wide, snapshot)
        grouped = assemble_grouped_factor_inputs(
            atomic,
            dimensions=wide.select(("instrument_id", "event_time", "industry_id")),
            group_field="industry_id",
        )
        result = suite.industry_composite.factor.compute(
            FactorContext(as_of=T1, snapshot=snapshot, inputs=grouped)
        )
        return cast(list[float], result["value"].to_pylist())

    baseline = scores(baseline_wide)
    contaminated = scores(pa.concat_tables((baseline_wide, extreme)))

    assert contaminated[:3] == baseline
    assert contaminated[3] == 0.0
