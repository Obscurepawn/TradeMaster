import hashlib
import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
import pytest
from pydantic import ValidationError
from trademaster.contracts import (
    AvailabilityPolicy,
    BacktestConfig,
    CoverageResult,
    DatasetCoverage,
    DatasetRequest,
    FactorContext,
    MarketEventKind,
    MarketTableView,
    OrderIntent,
    PortfolioView,
    RunArtifactObject,
    RunManifest,
    SnapshotManifest,
    SnapshotObject,
    SnapshotRequest,
    StrategyView,
    bind_snapshot_provenance,
    run_artifact_schema_sha256,
    run_artifact_schemas,
    run_manifest_schema,
    verify_run_manifest,
)

NOW = datetime(2025, 1, 2, 7, 0, tzinfo=UTC)


def empty_pit_table() -> pa.Table:
    return pa.table(
        {"event_time": pa.array([], type=pa.timestamp("us", tz="UTC"))}
    )


def artifact_objects() -> tuple[RunArtifactObject, ...]:
    return tuple(
        RunArtifactObject(
            artifact_name=name,
            uri=f"{name}.parquet",
            sha256="c" * 64,
            row_count=0,
        )
        for name in sorted(run_artifact_schemas())
    )


def test_order_intent_requires_known_side_utc_and_later_execution() -> None:
    base = {
        "order_id": "o1",
        "signal_id": "s1",
        "instrument_id": "000001.SZ",
        "side": "buy",
        "quantity": 100,
        "signal_time": NOW,
        "eligible_execution_time": NOW + timedelta(days=1),
    }
    valid = OrderIntent.model_validate(base)
    assert valid.side.value == "buy"

    for change in (
        {"side": "anything"},
        {"signal_time": NOW.replace(tzinfo=None)},
        {"eligible_execution_time": NOW},
    ):
        with pytest.raises(ValidationError):
            OrderIntent.model_validate(base | change)


def test_dataset_range_and_initial_cash_are_validated() -> None:
    with pytest.raises(ValidationError):
        DatasetRequest(dataset="daily", start=NOW, end=NOW - timedelta(days=1))

    with pytest.raises(ValidationError):
        BacktestConfig(
            run_id="r1",
            strategy_id="s1",
            snapshot_id="a" * 64,
            initial_cash=Decimal(0),
            fee_schedule_id="fees",
            artifact_dir="artifacts/r1",
        )


def test_snapshot_manifest_has_content_addressed_typed_objects() -> None:
    dataset_request = DatasetRequest(dataset="daily", start=NOW, end=NOW)
    request = SnapshotRequest(datasets=(dataset_request,), as_of=NOW)
    obj = SnapshotObject(
        dataset="daily",
        partition="trade_date=20250102",
        uri="canonical/daily/trade_date=20250102/part.parquet",
        sha256="a" * 64,
        schema_sha256="b" * 64,
        schema_version="daily/v1",
        fields=(),
        coverage_keys=(),
        resolved_instrument_set_sha256="d" * 64,
        resolved_instrument_count=2,
        row_count=2,
        event_time_start=NOW,
        event_time_end=NOW,
        known_at_max=NOW,
    )
    coverage = DatasetCoverage(
        request=dataset_request,
        covered_start=NOW,
        covered_end=NOW,
        fields=(),
        resolved_instrument_set_sha256="d" * 64,
        resolved_instrument_count=2,
        object_sha256s=(obj.sha256,),
        row_count=obj.row_count,
        known_at_max=obj.known_at_max,
    )
    policy = AvailabilityPolicy(
        policy_id="cn-daily/v1",
        known_at_field="known_at",
        publication_lag_policy_id="tushare/v1",
    )
    manifest = SnapshotManifest.build(
        request=request,
        availability_policy=policy,
        objects=(obj,),
        coverages=(coverage,),
    )

    assert manifest.objects[0].sha256 == "a" * 64
    assert len(manifest.snapshot_id) == 64

    assert manifest == SnapshotManifest.build(
        request=request,
        availability_policy=policy,
        objects=tuple(reversed(manifest.objects)),
        coverages=(coverage,),
    )
    manifest_wire = manifest.model_dump(mode="json")
    with pytest.raises(ValidationError):
        SnapshotManifest.model_validate_json(
            json.dumps(manifest_wire | {"unexpected": True})
        )
    invalid_objects = [manifest_wire["objects"][0] | {"row_count": True}]
    with pytest.raises(ValidationError):
        SnapshotManifest.model_validate_json(
            json.dumps(manifest_wire | {"objects": invalid_objects})
        )
    with pytest.raises(ValidationError, match="as_of"):
        SnapshotManifest.build(
            request=SnapshotRequest(
                datasets=(dataset_request,), as_of=NOW - timedelta(seconds=1)
            ),
            availability_policy=policy,
            objects=(obj,),
            coverages=(coverage,),
        )
    with pytest.raises(ValidationError, match="duplicate"):
        SnapshotManifest.build(
            request=request,
            availability_policy=policy,
            objects=(obj, obj),
            coverages=(coverage,),
        )

    with pytest.raises(ValidationError, match="coverage interval"):
        DatasetCoverage(
            request=DatasetRequest(
                dataset="daily", start=NOW - timedelta(days=9), end=NOW
            ),
            covered_start=NOW,
            covered_end=NOW,
            fields=(),
            resolved_instrument_set_sha256="d" * 64,
            resolved_instrument_count=2,
            object_sha256s=(obj.sha256,),
            row_count=obj.row_count,
            known_at_max=obj.known_at_max,
        )
    keyed_request = DatasetRequest(
        dataset="daily",
        start=NOW,
        end=NOW,
        coverage_keys=("20250102",),
    )
    keyed_coverage = DatasetCoverage(
        request=keyed_request,
        covered_start=NOW,
        covered_end=NOW,
        fields=(),
        resolved_instrument_set_sha256="d" * 64,
        resolved_instrument_count=2,
        object_sha256s=(obj.sha256,),
        row_count=obj.row_count,
        known_at_max=obj.known_at_max,
    )
    with pytest.raises(ValidationError, match="session key"):
        SnapshotManifest.build(
            request=SnapshotRequest(datasets=(keyed_request,), as_of=NOW),
            availability_policy=policy,
            objects=(obj,),
            coverages=(keyed_coverage,),
        )


def test_factor_context_cannot_read_a_snapshot_from_the_future() -> None:
    manifest = SnapshotManifest.build(
        request=SnapshotRequest(datasets=(), as_of=NOW),
        availability_policy=AvailabilityPolicy(
            policy_id="empty/v1",
            known_at_field="known_at",
            publication_lag_policy_id="none/v1",
        ),
        objects=(),
        coverages=(),
    )
    with pytest.raises(ValidationError, match="snapshot"):
        FactorContext(
            as_of=NOW - timedelta(seconds=1),
            snapshot=manifest,
            inputs=bind_snapshot_provenance(empty_pit_table(), manifest),
        )
    with pytest.raises(ValidationError, match="provenance"):
        FactorContext(as_of=NOW, snapshot=manifest, inputs=pa.table({}))
    future = bind_snapshot_provenance(
        pa.table(
            {
                "event_time": pa.array(
                    [NOW + timedelta(days=1)], type=pa.timestamp("us", tz="UTC")
                )
            }
        ),
        manifest,
    )
    with pytest.raises(ValidationError, match="future"):
        FactorContext(as_of=NOW, snapshot=manifest, inputs=future)


def test_cross_language_quantity_domain_and_strategy_view_are_explicit() -> None:
    base = {
        "order_id": "o1",
        "signal_id": "s1",
        "instrument_id": "000001.SZ",
        "side": "buy",
        "quantity": 100,
        "signal_time": NOW,
        "eligible_execution_time": NOW + timedelta(days=1),
    }
    with pytest.raises(ValidationError):
        OrderIntent.model_validate(base | {"quantity": 10**38})

    snapshot = SnapshotManifest.build(
        request=SnapshotRequest(datasets=(), as_of=NOW),
        availability_policy=AvailabilityPolicy(
            policy_id="empty/v1",
            known_at_field="known_at",
            publication_lag_policy_id="none/v1",
        ),
        objects=(),
        coverages=(),
    )
    frozen = bind_snapshot_provenance(empty_pit_table(), snapshot)
    future_table = bind_snapshot_provenance(
        pa.table({"event_time": [NOW + timedelta(days=30)]}), snapshot
    )
    with pytest.raises(ValidationError, match="future"):
        MarketTableView(snapshot=snapshot, as_of=NOW, table=future_table)
    view = StrategyView(
        event_time=NOW,
        event_kind=MarketEventKind.SESSION_OPEN,
        market=MarketTableView(snapshot=snapshot, as_of=NOW, table=frozen),
        history=MarketTableView(snapshot=snapshot, as_of=NOW, table=frozen),
        portfolio=PortfolioView(
            event_time=NOW,
            state_hash="e" * 64,
            cash=Decimal("100000.00"),
            positions=(),
        ),
    )
    assert view.event_kind is MarketEventKind.SESSION_OPEN
    with pytest.raises(ValidationError, match="future"):
        StrategyView(
            event_time=NOW,
            event_kind=MarketEventKind.SESSION_OPEN,
            market=MarketTableView(snapshot=snapshot, as_of=NOW, table=frozen),
            history=MarketTableView(
                snapshot=snapshot, as_of=NOW + timedelta(seconds=1), table=frozen
            ),
            portfolio=PortfolioView(
                event_time=NOW,
                state_hash="e" * 64,
                cash=Decimal("100000.00"),
                positions=(),
            ),
        )
    with pytest.raises(ValidationError, match="cash"):
        PortfolioView(
            event_time=NOW, state_hash="e" * 64, cash=Decimal(-1), positions=()
        )


def test_coverage_manifest_and_run_identity_fail_closed() -> None:
    with pytest.raises(ValidationError, match="complete"):
        CoverageResult(
            request=DatasetRequest(dataset="daily", start=NOW, end=NOW),
            complete=True,
            missing_ranges=((NOW, NOW),),
        )
    with pytest.raises(ValidationError):
        BacktestConfig(
            run_id="r1",
            strategy_id="s1",
            snapshot_id="not-a-hash",
            initial_cash=Decimal(1),
            fee_schedule_id="fees",
            artifact_dir="artifacts/r1",
        )

    manifest = RunManifest.build(
        run_id="r1",
        created_at=NOW,
        snapshot_id="a" * 64,
        strategy_id="s1",
        engine_version="0.1.0",
        config_hash="b" * 64,
        artifacts=artifact_objects(),
    )
    assert manifest.artifact_schema_sha256 == run_artifact_schema_sha256()
    required = run_manifest_schema()["required"]
    assert isinstance(required, list)
    assert set(required) == set(RunManifest.model_fields)


def test_python_run_manifest_matches_and_reads_the_canonical_wire_fixture() -> None:
    fixture = (
        Path(__file__).parents[1]
        / "crates/tm-core/schemas/run-manifest-v1.fixture.json"
    ).read_text().strip()
    manifest = RunManifest.build(
        run_id="r1",
        created_at=datetime(1970, 1, 1, tzinfo=UTC),
        snapshot_id="a" * 64,
        strategy_id="strategy",
        engine_version="0.1.0",
        config_hash="b" * 64,
        artifacts=artifact_objects(),
    )
    assert manifest.to_wire_json() == fixture
    assert RunManifest.from_wire_json(fixture) == manifest
    with pytest.raises(ValidationError):
        RunArtifactObject(
            artifact_name="signals",
            uri="signals.parquet",
            sha256="c" * 64,
            row_count=2**64,
        )
    with pytest.raises(ValidationError):
        RunManifest.model_validate(manifest.model_dump() | {"unexpected": True})
    raw = json.loads(fixture)
    for invalid in (
        raw | {"created_at": 0},
        raw | {"created_at": "1970-01-01T00:00:00.1234567Z"},
        raw | {"artifacts": [raw["artifacts"][0] | {"row_count": True}, *raw["artifacts"][1:]]},
    ):
        with pytest.raises((TypeError, ValueError, ValidationError)):
            RunManifest.from_wire_json(json.dumps(invalid, separators=(",", ":")))


def test_completed_manifest_verifies_the_exact_parquet_files(tmp_path: Path) -> None:
    objects: list[RunArtifactObject] = []
    for name, schema in sorted(run_artifact_schemas().items()):
        path = tmp_path / f"{name}.parquet"
        pq.write_table(pa.Table.from_pylist([], schema=schema), path)
        objects.append(
            RunArtifactObject(
                artifact_name=name,
                uri=path.name,
                sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
                row_count=0,
            )
        )
    manifest = RunManifest.build(
        run_id="r1",
        created_at=NOW,
        snapshot_id="a" * 64,
        strategy_id="strategy",
        engine_version="0.1.0",
        config_hash="b" * 64,
        artifacts=tuple(objects),
    )
    assert set(verify_run_manifest(manifest, tmp_path)) == set(run_artifact_schemas())
    (tmp_path / "signals.parquet").write_bytes(b"tampered")
    with pytest.raises(ValueError, match="hash mismatch"):
        verify_run_manifest(manifest, tmp_path)
