from datetime import UTC, date, datetime
from hashlib import sha256
from pathlib import Path
from typing import Literal

import pyarrow as pa
import pyarrow.parquet as pq
import pytest
from trademaster.e2e.demo_data import load_demo_market
from trademaster.research.full_a import (
    DownloadPlanManifest,
    DownloadTask,
    FullAResearchConfig,
    FullAResearchStore,
    _expected_request_metadata,
)

DAYS = (date(2024, 1, 5), date(2024, 1, 8), date(2024, 1, 9))
SYMBOLS = ("000001.SZ", "600001.SH")
VALUES: dict[str, dict[str, object]] = {
    "daily": {
        "open": 10.0,
        "high": 10.5,
        "low": 9.8,
        "close": 10.0,
        "pre_close": 10.0,
        "vol": 20.37,
    },
    "adj_factor": {"adj_factor": 1.0},
    "stk_limit": {"up_limit": 11.0, "down_limit": 9.0},
    "suspend_d": {"suspend_type": "S", "suspend_timing": "全天"},
}


def _make_cache(root: Path, fault: str = "") -> str:
    tasks = []
    for session in DAYS[:2]:
        key = session.strftime("%Y%m%d")
        for endpoint, values in VALUES.items():
            phase: Literal["status", "market"] = (
                "status" if endpoint in {"stk_limit", "suspend_d"} else "market"
            )
            params = {"trade_date": key}
            if endpoint == "suspend_d":
                params["suspend_type"] = "S"
            tasks.append(
                DownloadTask.build(
                    task_key=f"{phase}.{endpoint}.{key}",
                    phase=phase,
                    endpoint=endpoint,
                    params=params,
                    fields=("ts_code", "trade_date", *values),
                    page_limit=6000,
                    allow_empty=endpoint == "suspend_d",
                )
            )
    plan = DownloadPlanManifest.build(
        config=FullAResearchConfig(start=DAYS[0], requested_end=DAYS[-1]),
        resolved_end=DAYS[-1],
        instruments=SYMBOLS,
        excluded_instruments=(),
        sessions=DAYS,
        tasks=tuple(tasks),
    )
    store = FullAResearchStore(root, clock=lambda: datetime(2024, 2, 1, tzinfo=UTC))
    try:
        store.register(plan)
        for task in tasks:
            endpoint = task.endpoint
            day = dict(task.params)["trade_date"]
            symbols = list(SYMBOLS) if endpoint != "suspend_d" else []
            if day == "20240108":
                if endpoint == "suspend_d" and fault == "incomplete":
                    continue
                if endpoint == "daily" and fault == "missing":
                    symbols.pop()
                if endpoint == "stk_limit" and fault == "duplicate":
                    symbols.append(SYMBOLS[0])
                if endpoint == "suspend_d" and fault == "wrong_suspend_day":
                    symbols.append("600002.SH")
                if endpoint == "suspend_d" and fault == "suspended":
                    symbols.append(SYMBOLS[0])
            rows = [
                {"ts_code": symbol, "trade_date": day, **VALUES[endpoint]} for symbol in symbols
            ]
            if day == "20240108" and endpoint == "daily":
                for row in rows:
                    row["vol"] = 20.151 if fault == "fractional_share" else 20.15
            if fault == "adjustment" and day == "20240108" and endpoint == "adj_factor":
                rows[0]["adj_factor"] = 1.25
            if fault == "wrong_suspend_day" and day == "20240108" and endpoint == "suspend_d":
                rows[0]["trade_date"] = "20240105"
            schema = pa.schema(
                [
                    (
                        key,
                        pa.string()
                        if key in {"ts_code", "trade_date", "suspend_type", "suspend_timing"}
                        else pa.float64(),
                    )
                    for key in task.fields
                ],
                metadata={key: value for key, value in _expected_request_metadata(task).items()},
            )
            table = pa.Table.from_pylist(rows, schema=schema)
            encoded = pa.BufferOutputStream()
            pq.write_table(table, encoded)
            payload = encoded.getvalue().to_pybytes()
            digest = sha256(payload).hexdigest()
            path = root / "raw" / endpoint / f"{digest}.parquet"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(payload)
            store.mark_running(plan.plan_sha256, task)
            store.mark_completed(
                plan.plan_sha256,
                task,
                request_sha256=task.request_sha256,
                content_sha256=digest,
                row_count=len(rows),
                evidence_path=path,
            )
            if fault == "tampered" and day == "20240108" and endpoint == "daily":
                path.write_bytes(payload + b"tampered")
    finally:
        store.close()
    return plan.plan_sha256


def test_reads_requested_market_and_calendar_from_verified_local_objects(tmp_path: Path) -> None:
    plan = _make_cache(tmp_path)
    market = load_demo_market(tmp_path, plan, DAYS[0], DAYS[1], (SYMBOLS[1],))
    assert market.days == DAYS[:2]
    assert market.next_session == DAYS[2]
    assert market.bars["instrument_id"].to_pylist() == [SYMBOLS[1]] * 2
    assert market.bars["trade_date"].to_pylist() == list(DAYS[:2])
    assert market.bars["volume"].to_pylist() == [2037, 2015]
    assert market.bars.schema.field("volume").type == pa.int64()
    assert market.status["suspended"].to_pylist() == [False, False]
    assert len(market.source_evidence) == 8
    assert set(market.source_evidence.values()) == {
        path.stem for path in (tmp_path / "raw").glob("*/*.parquet")
    }
    with pytest.raises(ValueError, match="next session"):
        load_demo_market(tmp_path, plan, DAYS[0], DAYS[2], SYMBOLS)


@pytest.mark.parametrize(
    ("fault", "reason"),
    [
        ("incomplete", "completed"),
        ("tampered", "hash"),
        ("missing", "missing"),
        ("duplicate", "duplicate"),
        ("suspended", "suspension"),
        ("adjustment", "adjustment"),
        ("wrong_suspend_day", "suspension query"),
        ("fractional_share", "whole shares"),
    ],
)
def test_unusable_or_unverified_evidence_stops_the_demo(
    tmp_path: Path,
    fault: str,
    reason: str,
) -> None:
    plan = _make_cache(tmp_path, fault)
    with pytest.raises((RuntimeError, ValueError), match=reason):
        load_demo_market(tmp_path, plan, DAYS[0], DAYS[1], SYMBOLS)
