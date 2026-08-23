from __future__ import annotations

import math
from datetime import UTC, datetime, timedelta
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
from trademaster.factors import MomentumFactor, factor_output_schema
from trademaster.factors.management import (
    FactorRegistration,
    ManagedFactorRegistry,
)
from trademaster.factors.storage import (
    FactorIntegrityError,
    FactorManager,
    FactorScope,
)

NOW = datetime(2025, 1, 6, 8, tzinfo=UTC)


def test_factor_definition_hashes_code_parameters_and_canonical_contract() -> None:
    factor = MomentumFactor(lookback_sessions=20, version="1")

    first = FactorRegistration.create(
        factor,
        family="momentum",
        description="20-session adjusted close momentum",
        parameters={"lookback_sessions": 20, "price": "adjusted_close"},
        frequency="daily",
        scope="time_series",
        unit="ratio",
        direction=1,
    )
    second = FactorRegistration.create(
        factor,
        family="momentum",
        description="20-session adjusted close momentum",
        parameters={"price": "adjusted_close", "lookback_sessions": 20},
        frequency="daily",
        scope="time_series",
        unit="ratio",
        direction=1,
    )
    changed = FactorRegistration.create(
        factor,
        family="momentum",
        description="20-session adjusted close momentum",
        parameters={"lookback_sessions": 21, "price": "adjusted_close"},
        frequency="daily",
        scope="time_series",
        unit="ratio",
        direction=1,
    )

    assert first.definition == second.definition
    assert first.definition.definition_sha256 == second.definition.definition_sha256
    assert first.definition.parameters_sha256 != changed.definition.parameters_sha256
    assert first.definition.definition_sha256 != changed.definition.definition_sha256
    assert len(first.definition.code_sha256) == 64
    assert first.definition.dependencies[0].kind == "dataset"
    assert first.definition.model_dump(mode="json")["schema_id"] == (
        "trademaster.factor-definition/v1"
    )

    with pytest.raises(ValueError, match="parameters"):
        FactorRegistration.create(
            factor,
            family="momentum",
            description="invalid",
            parameters={"bad": math.nan},
            frequency="daily",
            scope="time_series",
            unit="ratio",
            direction=1,
        )


class _DependencyFactor:
    def __init__(self, factor_id: str, dependencies: tuple[str, ...]) -> None:
        self.spec = FactorSpec(
            factor_id=factor_id,
            version="1",
            dependencies=dependencies,
            lookback_sessions=0,
        )

    def compute(self, context: object) -> object:
        raise AssertionError("graph tests must not compute factors")


def _registration(factor_id: str, dependencies: tuple[str, ...]) -> FactorRegistration:
    return FactorRegistration.create(
        _DependencyFactor(factor_id, dependencies),  # type: ignore[arg-type]
        family="test",
        description=factor_id,
        parameters={},
        frequency="daily",
        scope="cross_sectional",
        unit="score",
        direction=1,
    )


def test_managed_registry_builds_stable_dag_and_rejects_conflicts() -> None:
    raw = _registration("raw_value", ("daily_basic.total_market_value",))
    quality = _registration("quality", ("financial_indicators.report_values_json",))
    composite = _registration(
        "composite",
        ("factor:quality@1", "factor:raw_value@1"),
    )
    registry = ManagedFactorRegistry((composite, quality, raw))

    assert registry.identities == (
        ("composite", "1"),
        ("quality", "1"),
        ("raw_value", "1"),
    )
    assert [item.definition.factor_id for item in registry.plan("composite", "1")] == [
        "quality",
        "raw_value",
        "composite",
    ]
    assert registry.get_by_definition_sha(raw.definition.definition_sha256) is raw

    conflicting = FactorRegistration.create(
        raw.factor,
        family="test",
        description="different",
        parameters={},
        frequency="daily",
        scope="cross_sectional",
        unit="score",
        direction=1,
    )
    with pytest.raises(ValueError, match="duplicate factor identity"):
        ManagedFactorRegistry((raw, conflicting))


def test_managed_registry_rejects_unknown_dependencies_and_cycles() -> None:
    unknown = _registration("unknown_consumer", ("factor:missing@1",))
    with pytest.raises(ValueError, match="unknown factor dependency"):
        ManagedFactorRegistry((unknown,))

    left = _registration("left", ("factor:right@1",))
    right = _registration("right", ("factor:left@1",))
    with pytest.raises(ValueError, match="cycle"):
        ManagedFactorRegistry((left, right))


class _CountingFactor:
    def __init__(self) -> None:
        self.calls = 0
        self.spec = FactorSpec(
            factor_id="managed_constant",
            version="1",
            dependencies=("daily_bars.close",),
            lookback_sessions=0,
        )

    def compute(self, context: FactorContext) -> pa.Table:
        self.calls += 1
        return pa.Table.from_pylist(
            [
                {
                    "instrument_id": row["instrument_id"],
                    "event_time": row["event_time"],
                    "factor_id": self.spec.factor_id,
                    "factor_version": self.spec.version,
                    "value": float(row["close"]),
                    "is_valid": True,
                }
                for row in context.inputs.to_pylist()
            ],
            schema=factor_output_schema(),
        )


class _ConsumerFactor:
    spec = FactorSpec(
        factor_id="managed_consumer",
        version="1",
        dependencies=("factor:managed_constant@1",),
        lookback_sessions=0,
    )

    def compute(self, context: FactorContext) -> pa.Table:
        return pa.Table.from_pylist(
            [
                {
                    **row,
                    "factor_id": self.spec.factor_id,
                    "factor_version": self.spec.version,
                }
                for row in context.inputs.to_pylist()
            ],
            schema=factor_output_schema(),
        )


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


def test_factor_manager_materializes_parquet_and_reuses_verified_cache(
    tmp_path: Path,
) -> None:
    factor = _CountingFactor()
    registration = FactorRegistration.create(
        factor,
        family="test",
        description="cacheable constant",
        parameters={},
        frequency="daily",
        scope="cross_sectional",
        unit="price",
        direction=0,
    )
    registry = ManagedFactorRegistry((registration,))
    snapshot = _snapshot()
    event_time = NOW - timedelta(hours=1)
    inputs = bind_snapshot_provenance(
        pa.table(
            {
                "instrument_id": ["600000.SH", "000001.SZ"],
                "event_time": pa.array([event_time, event_time], type=pa.timestamp("us", tz="UTC")),
                "close": [10.0, 8.0],
            }
        ),
        snapshot,
    )
    context = FactorContext(as_of=NOW, snapshot=snapshot, inputs=inputs)
    scope = FactorScope(
        start=event_time,
        end=event_time,
        as_of=NOW,
        instruments=("000001.SZ", "600000.SH"),
        coverage_keys=("SSE:2025-01-06", "SZSE:2025-01-06"),
    )

    with FactorManager(root=tmp_path, registry=registry, clock=lambda: NOW) as manager:
        first = manager.materialize("managed_constant", "1", context=context, scope=scope)
        second = manager.materialize("managed_constant", "1", context=context, scope=scope)

        assert not first.from_cache
        assert second.from_cache
        assert factor.calls == 1
        assert first.manifest == second.manifest
        assert first.path == second.path
        assert first.table.equals(second.table)
        assert first.path.is_file() and first.path.suffix == ".parquet"
        assert first.manifest.definition_sha256 == registration.definition.definition_sha256
        assert first.manifest.input_snapshot_id == snapshot.snapshot_id
        assert first.manifest.output_sha256 == first.path.name.removesuffix(".parquet")
        assert first.table.schema.metadata is not None
        assert first.table.schema.metadata[b"trademaster.factor.definition_sha256"] == (
            registration.definition.definition_sha256.encode()
        )
        assert manager.catalog.definition_count() == 1
        assert manager.catalog.materialization_count() == 1
        assert manager.catalog.definitions() == (
            (
                "managed_constant",
                "1",
                registration.definition.definition_sha256,
            ),
        )
        queried = manager.query_values(
            "managed_constant",
            "1",
            start=event_time,
            end=event_time,
            instruments=("600000.SH",),
        )
        assert queried["instrument_id"].to_pylist() == ["600000.SH"]
        assert queried["value"].to_pylist() == [10.0]


def test_factor_manager_fails_closed_when_materialized_parquet_is_corrupt(
    tmp_path: Path,
) -> None:
    factor = _CountingFactor()
    registration = FactorRegistration.create(
        factor,
        family="test",
        description="cacheable constant",
        parameters={},
        frequency="daily",
        scope="cross_sectional",
        unit="price",
        direction=0,
    )
    snapshot = _snapshot()
    event_time = NOW - timedelta(hours=1)
    inputs = bind_snapshot_provenance(
        pa.table(
            {
                "instrument_id": ["600000.SH"],
                "event_time": pa.array([event_time], type=pa.timestamp("us", tz="UTC")),
                "close": [10.0],
            }
        ),
        snapshot,
    )
    context = FactorContext(as_of=NOW, snapshot=snapshot, inputs=inputs)
    scope = FactorScope(
        start=event_time,
        end=event_time,
        as_of=NOW,
        instruments=("600000.SH",),
        coverage_keys=("SSE:2025-01-06",),
    )
    with FactorManager(
        root=tmp_path,
        registry=ManagedFactorRegistry((registration,)),
        clock=lambda: NOW,
    ) as manager:
        artifact = manager.materialize("managed_constant", "1", context=context, scope=scope)
        artifact.path.write_bytes(b"corrupt")
        with pytest.raises(FactorIntegrityError, match="hash"):
            manager.materialize("managed_constant", "1", context=context, scope=scope)


def test_factor_manager_rejects_composite_inputs_that_do_not_match_declared_parents(
    tmp_path: Path,
) -> None:
    parent_factor = _CountingFactor()
    parent_registration = FactorRegistration.create(
        parent_factor,
        family="test",
        description="parent",
        parameters={},
        frequency="daily",
        scope="cross_sectional",
        unit="price",
        direction=0,
    )
    consumer_registration = FactorRegistration.create(
        _ConsumerFactor(),
        family="test",
        description="consumer",
        parameters={},
        frequency="daily",
        scope="cross_sectional",
        unit="price",
        direction=0,
    )
    snapshot = _snapshot()
    event_time = NOW - timedelta(hours=1)
    scope = FactorScope(
        start=event_time,
        end=event_time,
        as_of=NOW,
        instruments=("600000.SH",),
        coverage_keys=("SSE:2025-01-06",),
    )
    source = bind_snapshot_provenance(
        pa.table(
            {
                "instrument_id": ["600000.SH"],
                "event_time": pa.array([event_time], type=pa.timestamp("us", tz="UTC")),
                "close": [10.0],
            }
        ),
        snapshot,
    )
    with FactorManager(
        root=tmp_path,
        registry=ManagedFactorRegistry((parent_registration, consumer_registration)),
        clock=lambda: NOW,
    ) as manager:
        parent = manager.materialize(
            "managed_constant",
            "1",
            context=FactorContext(as_of=NOW, snapshot=snapshot, inputs=source),
            scope=scope,
        )
        forged = parent.table.set_column(
            parent.table.schema.get_field_index("value"),
            "value",
            pa.array([999.0]),
        )
        with pytest.raises(ValueError, match="declared parent artifacts"):
            manager.materialize(
                "managed_consumer",
                "1",
                context=FactorContext(
                    as_of=NOW,
                    snapshot=snapshot,
                    inputs=forged,
                ),
                scope=scope,
                parents=(parent,),
            )
