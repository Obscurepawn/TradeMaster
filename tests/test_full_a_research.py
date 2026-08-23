from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from collections.abc import Mapping
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import duckdb
import pyarrow as pa
import pyarrow.parquet as pq
import pytest
from trademaster.data.research import ResearchObjectEvidence, ResearchQueryResult
from trademaster.research import full_a
from trademaster.research.__main__ import _register_reconciled_plan
from trademaster.research.full_a import (
    DownloadPlanManifest,
    FullAResearchConfig,
    FullAResearchDownloader,
    FullAResearchPlanner,
    FullAResearchStore,
    ResearchInstrument,
)

NOW = datetime(2026, 8, 23, tzinfo=UTC)


def _canonical_json(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode()


def _request_sha256(
    endpoint: str,
    params: Mapping[str, object],
    fields: tuple[str, ...],
    page_limit: int,
) -> str:
    return hashlib.sha256(
        _canonical_json(
            {
                "endpoint": endpoint,
                "params": dict(params),
                "fields": fields,
                "page_limit": page_limit,
            }
        )
    ).hexdigest()


def _config() -> FullAResearchConfig:
    return FullAResearchConfig(
        start=date(2006, 8, 23),
        requested_end=date(2026, 8, 23),
        benchmarks=("000016.SH", "000300.SH"),
    )


def _instruments() -> tuple[ResearchInstrument, ...]:
    return (
        ResearchInstrument(
            instrument_id="000001.SZ",
            venue="SZSE",
            list_date=date(1991, 4, 3),
            delist_date=None,
            listing_status="L",
        ),
        ResearchInstrument(
            instrument_id="430001.BJ",
            venue="BSE",
            list_date=date(2021, 11, 15),
            delist_date=None,
            listing_status="L",
        ),
        ResearchInstrument(
            instrument_id="600001.SH",
            venue="SSE",
            list_date=date(1990, 12, 19),
            delist_date=date(2007, 1, 1),
            listing_status="D",
        ),
        ResearchInstrument(
            instrument_id="600002.SH",
            venue="SSE",
            list_date=date(1990, 12, 19),
            delist_date=date(2005, 1, 1),
            listing_status="D",
        ),
    )


def test_full_a_planner_is_deterministic_and_includes_delisted_overlap() -> None:
    sessions = (date(2006, 8, 23), date(2006, 8, 24))
    planner = FullAResearchPlanner()

    first = planner.build(_config(), instruments=_instruments(), sessions=sessions)
    second = planner.build(_config(), instruments=reversed(_instruments()), sessions=sessions)

    assert first == second
    assert first.resolved_end == date(2006, 8, 24)
    assert first.instruments == ("000001.SZ", "600001.SH")
    assert first.excluded_instruments == ("430001.BJ", "600002.SH")
    assert len(first.tasks) == 33
    assert len({item.task_id for item in first.tasks}) == 33
    assert first.task("market.daily.20060823").params == (("trade_date", "20060823"),)
    assert first.task("statement.fina_indicator.600001.SH").params == (
        ("end_date", "20060824"),
        ("start_date", "20050101"),
        ("ts_code", "600001.SH"),
    )
    assert len(first.plan_sha256) == 64


def test_download_plan_rejects_missing_or_noncanonical_task_identity() -> None:
    plan = FullAResearchPlanner().build(
        _config(), instruments=_instruments(), sessions=(date(2006, 8, 23),)
    )
    payload = plan.model_dump(mode="python")
    payload["tasks"] = tuple(reversed(plan.tasks))
    with pytest.raises(ValueError, match="canonical"):
        DownloadPlanManifest.model_validate(payload)


def test_planner_builds_universe_and_sessions_from_tushare_bootstrap_tables() -> None:
    stock_columns = {
        "ts_code": ["000001.SZ", "430001.BJ", "900901.SH"],
        "symbol": ["000001", "430001", "900901"],
        "name": ["平安银行", "北交样本", "B股样本"],
        "market": ["主板", "北交所", "主板"],
        "exchange": ["SZSE", "BSE", "SSE"],
        "curr_type": ["CNY", "CNY", "USD"],
        "list_status": ["L", "L", "L"],
        "list_date": ["19910403", "20211115", "19920221"],
        "delist_date": [None, None, None],
    }
    calendar = pa.table(
        {
            "exchange": ["SSE", "SSE", "SSE"],
            "cal_date": ["20060823", "20060824", "20060825"],
            "is_open": [1, 1, 0],
            "pretrade_date": ["20060822", "20060823", "20060824"],
        }
    )
    stock_table = pa.table(stock_columns)
    bootstrap = {
        "bootstrap.stock_basic.L": stock_table,
        "bootstrap.stock_basic.D": stock_table.slice(0, 0),
        "bootstrap.stock_basic.P": stock_table.slice(0, 0),
        "bootstrap.stock_basic.G": stock_table.slice(0, 0),
        "bootstrap.trade_cal.SSE": calendar,
        "bootstrap.trade_cal.SZSE": calendar.set_column(
            0, "exchange", pa.array(["SZSE", "SZSE", "SZSE"])
        ),
    }

    plan = FullAResearchPlanner().build_from_bootstrap(_config(), bootstrap)

    assert plan.instruments == ("000001.SZ",)
    assert "430001.BJ" in plan.excluded_instruments
    assert "900901.SH" not in plan.instruments
    assert plan.sessions == (date(2006, 8, 23), date(2006, 8, 24))

    reconciled = FullAResearchPlanner().build_from_bootstrap(
        _config(),
        bootstrap,
        market_lifecycles={"000999.SZ": (date(2006, 8, 23), date(2006, 8, 24))},
    )
    assert reconciled.instruments == ("000001.SZ", "000999.SZ")
    assert len(reconciled.tasks) == len(plan.tasks) + 4


def test_planner_rejects_disagreeing_sse_szse_calendars() -> None:
    empty_stock = pa.table(
        {
            "ts_code": pa.array([], pa.string()),
            "symbol": pa.array([], pa.string()),
            "name": pa.array([], pa.string()),
            "market": pa.array([], pa.string()),
            "exchange": pa.array([], pa.string()),
            "curr_type": pa.array([], pa.string()),
            "list_status": pa.array([], pa.string()),
            "list_date": pa.array([], pa.string()),
            "delist_date": pa.array([], pa.string()),
        }
    )
    bootstrap = {
        **{f"bootstrap.stock_basic.{status}": empty_stock for status in ("D", "G", "P")},
        "bootstrap.stock_basic.L": pa.table(
            {
                "ts_code": ["000001.SZ"],
                "symbol": ["000001"],
                "name": ["平安银行"],
                "market": ["主板"],
                "exchange": ["SZSE"],
                "curr_type": ["CNY"],
                "list_status": ["L"],
                "list_date": ["19910403"],
                "delist_date": [None],
            }
        ),
        "bootstrap.trade_cal.SSE": pa.table(
            {
                "exchange": ["SSE"],
                "cal_date": ["20060823"],
                "is_open": [1],
                "pretrade_date": ["20060822"],
            }
        ),
        "bootstrap.trade_cal.SZSE": pa.table(
            {
                "exchange": ["SZSE"],
                "cal_date": ["20060824"],
                "is_open": [1],
                "pretrade_date": ["20060823"],
            }
        ),
    }
    with pytest.raises(ValueError, match="calendars disagree"):
        FullAResearchPlanner().build_from_bootstrap(_config(), bootstrap)


class _FakeResearchSource:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.calls: list[str] = []
        self.paths: list[Path] = []

    def query_with_evidence(
        self,
        endpoint: str,
        *,
        params: Mapping[str, object],
        fields: tuple[str, ...],
        page_limit: int,
    ) -> ResearchQueryResult:
        identity = f"{endpoint}:{params}:{fields}:{page_limit}"
        self.calls.append(identity)
        return self._write_result(
            endpoint=endpoint,
            params=params,
            fields=fields,
            page_limit=page_limit,
        )

    def _write_result(
        self,
        *,
        endpoint: str,
        params: Mapping[str, object],
        fields: tuple[str, ...],
        page_limit: int,
        empty: bool = False,
        metadata_params: Mapping[str, object] | None = None,
        evidence_request_sha256: str | None = None,
    ) -> ResearchQueryResult:
        values = [] if empty else ["value"]
        table = pa.table({field: pa.array(values, type=pa.string()) for field in fields})
        persisted_params = dict(metadata_params if metadata_params is not None else params)
        metadata_request_sha256 = _request_sha256(
            endpoint,
            persisted_params,
            fields,
            page_limit,
        )
        table = table.replace_schema_metadata(
            {
                b"trademaster.strategy.endpoint": endpoint.encode(),
                b"trademaster.strategy.params": _canonical_json(persisted_params),
                b"trademaster.strategy.fields": _canonical_json(fields),
                b"trademaster.strategy.request_sha256": metadata_request_sha256.encode(),
                b"trademaster.strategy.fetched_at": NOW.isoformat().encode(),
            }
        )
        temporary = self.root / f"{len(self.calls)}.parquet"
        pq.write_table(table, temporary)
        content_sha256 = hashlib.sha256(temporary.read_bytes()).hexdigest()
        path = self.root / f"{content_sha256}.parquet"
        temporary.replace(path)
        self.paths.append(path)
        return ResearchQueryResult(
            table=table,
            evidence=ResearchObjectEvidence(
                endpoint=endpoint,
                request_sha256=(
                    evidence_request_sha256
                    if evidence_request_sha256 is not None
                    else _request_sha256(endpoint, params, fields, page_limit)
                ),
                content_sha256=content_sha256,
                path=path,
                row_count=table.num_rows,
                fetched_at=NOW,
            ),
        )


class _WrongEvidenceRequestSource(_FakeResearchSource):
    def query_with_evidence(
        self,
        endpoint: str,
        *,
        params: Mapping[str, object],
        fields: tuple[str, ...],
        page_limit: int,
    ) -> ResearchQueryResult:
        result = super().query_with_evidence(
            endpoint,
            params=params,
            fields=fields,
            page_limit=page_limit,
        )
        return ResearchQueryResult(
            table=result.table,
            evidence=ResearchObjectEvidence(
                endpoint=result.evidence.endpoint,
                request_sha256="f" * 64,
                content_sha256=result.evidence.content_sha256,
                path=result.evidence.path,
                row_count=result.evidence.row_count,
                fetched_at=result.evidence.fetched_at,
            ),
        )


class _WrongParquetRequestSource(_FakeResearchSource):
    def query_with_evidence(
        self,
        endpoint: str,
        *,
        params: Mapping[str, object],
        fields: tuple[str, ...],
        page_limit: int,
    ) -> ResearchQueryResult:
        self.calls.append(f"{endpoint}:{params}:{fields}:{page_limit}")
        wrong_params = {**dict(params), "wrong_scope": "1"}
        return self._write_result(
            endpoint=endpoint,
            params=params,
            fields=fields,
            page_limit=page_limit,
            metadata_params=wrong_params,
        )


class _FirstEmptyResearchSource(_FakeResearchSource):
    def query_with_evidence(
        self,
        endpoint: str,
        *,
        params: Mapping[str, object],
        fields: tuple[str, ...],
        page_limit: int,
    ) -> ResearchQueryResult:
        first = not self.calls
        self.calls.append(f"{endpoint}:{params}:{fields}:{page_limit}")
        return self._write_result(
            endpoint=endpoint,
            params=params,
            fields=fields,
            page_limit=page_limit,
            empty=first,
        )


class _BlockedResearchSource:
    def __init__(self, outcome: str) -> None:
        self.outcome = outcome

    def query_with_evidence(
        self,
        endpoint: str,
        *,
        params: Mapping[str, object],
        fields: tuple[str, ...],
        page_limit: int,
    ) -> ResearchQueryResult:
        del endpoint, params, fields, page_limit
        raise full_a.AcquisitionBlockedError(self.outcome, "provider cannot serve request")


def test_download_store_resumes_and_cache_probe_makes_no_source_calls(tmp_path: Path) -> None:
    plan = FullAResearchPlanner().build(
        _config(), instruments=_instruments(), sessions=(date(2006, 8, 23),)
    )
    source = _FakeResearchSource(tmp_path)

    with FullAResearchStore(tmp_path, clock=lambda: NOW) as store:
        store.register(plan)
        first = FullAResearchDownloader(source=source, store=store).run(plan, max_tasks=3)
        assert first.completed == 3
        assert first.pending == len(plan.tasks) - 3
        assert len(source.calls) == 3

    with FullAResearchStore(tmp_path, clock=lambda: NOW) as store:
        remaining = FullAResearchDownloader(source=source, store=store).run(plan)
        assert remaining.completed == len(plan.tasks)
        assert remaining.pending == 0
        calls_after_completion = len(source.calls)
        probe = FullAResearchDownloader(source=source, store=store).run(plan)
        assert probe.completed == len(plan.tasks)
        assert len(source.calls) == calls_after_completion
        loaded = store.load_plan(plan.plan_sha256)
        assert loaded == plan
        assert store.verify(plan).completed == len(plan.tasks)

    source.paths[0].write_bytes(b"corrupt")
    with (
        FullAResearchStore(tmp_path, clock=lambda: NOW) as store,
        pytest.raises(RuntimeError, match="content hash"),
    ):
        store.verify(plan)


def test_downloader_rejects_evidence_request_identity_for_another_task(
    tmp_path: Path,
) -> None:
    plan = FullAResearchPlanner().build(
        _config(), instruments=_instruments(), sessions=(date(2006, 8, 23),)
    )

    with FullAResearchStore(tmp_path, clock=lambda: NOW) as store:
        downloader = FullAResearchDownloader(
            source=_WrongEvidenceRequestSource(tmp_path),
            store=store,
        )
        with pytest.raises(RuntimeError, match="request identity"):
            downloader.run(plan, max_tasks=1)

        assert store.status(plan.plan_sha256).failed == 1


def test_downloader_rejects_parquet_request_metadata_for_another_task(
    tmp_path: Path,
) -> None:
    plan = FullAResearchPlanner().build(
        _config(), instruments=_instruments(), sessions=(date(2006, 8, 23),)
    )

    with FullAResearchStore(tmp_path, clock=lambda: NOW) as store:
        downloader = FullAResearchDownloader(
            source=_WrongParquetRequestSource(tmp_path),
            store=store,
        )
        with pytest.raises(RuntimeError, match="request metadata"):
            downloader.run(plan, max_tasks=1)

        assert store.status(plan.plan_sha256).failed == 1


def test_claim_lease_prevents_duplicate_claim_and_reclaims_only_after_expiry(
    tmp_path: Path,
) -> None:
    current = [NOW]
    config = FullAResearchConfig(
        start=date(2006, 8, 23),
        requested_end=date(2026, 8, 23),
        benchmarks=("000016.SH",),
    )
    plan = FullAResearchPlanner().build(
        config,
        instruments=_instruments(),
        sessions=(date(2006, 8, 23),),
    )

    with (
        FullAResearchStore(tmp_path, clock=lambda: current[0]) as first_store,
        FullAResearchStore(tmp_path, clock=lambda: current[0]) as second_store,
    ):
        first_store.register(plan)
        first = first_store.claim_next(
            plan,
            lease_owner="worker-a",
            lease_duration=timedelta(minutes=1),
            phases=("benchmark",),
        )
        assert first is not None
        assert (
            second_store.claim_next(
                plan,
                lease_owner="worker-b",
                lease_duration=timedelta(minutes=1),
                phases=("benchmark",),
            )
            is None
        )

        current[0] += timedelta(minutes=1, seconds=1)
        reclaimed = second_store.claim_next(
            plan,
            lease_owner="worker-b",
            lease_duration=timedelta(minutes=1),
            phases=("benchmark",),
        )
        assert reclaimed == first
        with pytest.raises(RuntimeError, match="lease"):
            first_store.mark_failed(
                plan.plan_sha256,
                first,
                RuntimeError("late worker"),
                lease_owner="worker-a",
            )


def test_verified_empty_outcome_migrates_and_full_verify_accepts_it(tmp_path: Path) -> None:
    plan = FullAResearchPlanner().build(
        _config(), instruments=_instruments(), sessions=(date(2006, 8, 23),)
    )
    source = _FirstEmptyResearchSource(tmp_path)

    with FullAResearchStore(tmp_path, clock=lambda: NOW) as store:
        result = FullAResearchDownloader(source=source, store=store).run(plan)
        assert result.schema_id == "trademaster.full-a-download-status/v2"
        assert result.completed == len(plan.tasks)
        assert result.object_acquired == len(plan.tasks) - 1
        assert result.verified_empty == 1
        assert store.verify(plan) == result

    with duckdb.connect(str(tmp_path / "research.duckdb")) as connection:
        connection.execute(
            """
            UPDATE download_tasks SET acquisition_outcome = NULL
            WHERE plan_sha256 = ? AND row_count = 0
            """,
            [plan.plan_sha256],
        )

    with FullAResearchStore(tmp_path, clock=lambda: NOW) as migrated:
        status = migrated.status(plan.plan_sha256)
        assert status.verified_empty == 1
        assert migrated.verify(plan) == status


def test_completed_evidence_snapshot_is_typed_filtered_and_content_addressed(
    tmp_path: Path,
) -> None:
    plan = FullAResearchPlanner().build(
        _config(), instruments=_instruments(), sessions=(date(2006, 8, 23),)
    )
    source = _FakeResearchSource(tmp_path)

    with FullAResearchStore(tmp_path, clock=lambda: NOW) as store:
        FullAResearchDownloader(source=source, store=store).run(plan)
        snapshot = store.completed_evidence_snapshot(plan, endpoints=("daily",))

    assert snapshot.plan_sha256 == plan.plan_sha256
    assert len(snapshot.snapshot_sha256) == 64
    assert len(snapshot.evidence) == 1
    evidence = snapshot.evidence[0]
    assert evidence.task == plan.task("market.daily.20060823")
    assert evidence.request_sha256 == evidence.task.request_sha256
    assert evidence.acquisition_outcome == "object_acquired"
    assert evidence.row_count == 1


@pytest.mark.parametrize("outcome", ["source_history_absent", "permission_blocked"])
def test_typed_provider_blocker_is_a_machine_readable_terminal_outcome(
    tmp_path: Path,
    outcome: str,
) -> None:
    plan = FullAResearchPlanner().build(
        _config(), instruments=_instruments(), sessions=(date(2006, 8, 23),)
    )

    with FullAResearchStore(tmp_path, clock=lambda: NOW) as store:
        status = FullAResearchDownloader(
            source=_BlockedResearchSource(outcome),
            store=store,
        ).run(plan)

        assert status.blocked == len(plan.tasks)
        assert getattr(status, outcome) == len(plan.tasks)
        assert status.failed == 0
        assert store.verify(plan) == status


def test_download_store_fails_closed_when_plan_json_is_corrupt(tmp_path: Path) -> None:
    plan = FullAResearchPlanner().build(
        _config(), instruments=_instruments(), sessions=(date(2006, 8, 23),)
    )
    with FullAResearchStore(tmp_path, clock=lambda: NOW) as store:
        store.register(plan)
        path = store.plan_path(plan.plan_sha256)
    path.write_text("{}", encoding="utf-8")

    with (
        FullAResearchStore(tmp_path, clock=lambda: NOW) as store,
        pytest.raises(RuntimeError, match="hash mismatch"),
    ):
        store.load_plan(plan.plan_sha256)


def test_download_store_inherits_only_identical_completed_tasks_across_plan_versions(
    tmp_path: Path,
) -> None:
    source_plan = FullAResearchPlanner().build(
        _config(), instruments=_instruments(), sessions=(date(2006, 8, 23),)
    )
    changed_config = FullAResearchConfig(
        start=date(2006, 8, 23),
        requested_end=date(2026, 8, 23),
        benchmarks=("000001.SH", "000016.SH", "000300.SH"),
    )
    target_plan = FullAResearchPlanner().build(
        changed_config,
        instruments=_instruments(),
        sessions=(date(2006, 8, 23),),
    )
    source = _FakeResearchSource(tmp_path)

    with FullAResearchStore(tmp_path, clock=lambda: NOW) as store:
        FullAResearchDownloader(source=source, store=store).run(source_plan, max_tasks=3)
        store.register(target_plan)
        assert store.inherit_completed(source_plan, target_plan) == 3
        assert store.status(target_plan.plan_sha256).completed == 3
        assert store.status(target_plan.plan_sha256).pending == len(target_plan.tasks) - 3


def test_reconcile_registration_is_idempotent_when_market_identity_is_unchanged(
    tmp_path: Path,
) -> None:
    plan = FullAResearchPlanner().build(
        _config(), instruments=_instruments(), sessions=(date(2006, 8, 23),)
    )

    with FullAResearchStore(tmp_path, clock=lambda: NOW) as store:
        store.register(plan)

        assert _register_reconciled_plan(store, plan, plan) == 0
        assert store.load_plan(plan.plan_sha256) == plan


class _CorruptEvidenceSource(_FakeResearchSource):
    def query_with_evidence(
        self,
        endpoint: str,
        *,
        params: Mapping[str, object],
        fields: tuple[str, ...],
        page_limit: int,
    ) -> ResearchQueryResult:
        result = super().query_with_evidence(
            endpoint,
            params=params,
            fields=fields,
            page_limit=page_limit,
        )
        return ResearchQueryResult(
            table=result.table,
            evidence=ResearchObjectEvidence(
                endpoint=result.evidence.endpoint,
                request_sha256=result.evidence.request_sha256,
                content_sha256="f" * 64,
                path=result.evidence.path,
                row_count=result.evidence.row_count,
                fetched_at=result.evidence.fetched_at,
            ),
        )


def test_downloader_rejects_unverified_source_evidence(tmp_path: Path) -> None:
    plan = FullAResearchPlanner().build(
        _config(), instruments=_instruments(), sessions=(date(2006, 8, 23),)
    )
    with FullAResearchStore(tmp_path, clock=lambda: NOW) as store:
        downloader = FullAResearchDownloader(source=_CorruptEvidenceSource(tmp_path), store=store)
        with pytest.raises(RuntimeError, match="content hash"):
            downloader.run(plan, max_tasks=1)
        status = store.status(plan.plan_sha256)
        assert status.failed == 1
        assert status.completed == 0


def test_full_a_cli_reports_durable_status_without_provider_credentials(
    tmp_path: Path,
) -> None:
    plan = FullAResearchPlanner().build(
        _config(), instruments=_instruments(), sessions=(date(2006, 8, 23),)
    )
    with FullAResearchStore(tmp_path, clock=lambda: NOW) as store:
        store.register(plan)

    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "trademaster.research",
            "--data-root",
            str(tmp_path),
            "status",
            "--plan-sha256",
            plan.plan_sha256,
        ],
        capture_output=True,
        check=False,
        text=True,
    )

    assert completed.returncode == 0, completed.stderr
    payload = json.loads(completed.stdout)
    assert payload["plan_sha256"] == plan.plan_sha256
    assert payload["pending"] == len(plan.tasks)
