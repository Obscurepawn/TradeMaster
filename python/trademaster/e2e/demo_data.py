"""Read a small daily A-share market from a completed local TuShare research plan."""

from __future__ import annotations

import hashlib
import math
import re
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any

import duckdb
import pyarrow as pa
import pyarrow.parquet as pq

from trademaster.research.full_a import (
    CompletedDownloadEvidence,
    DownloadPlanManifest,
    _verify_request_metadata,
)

_ENDPOINTS = {
    "daily": "market",
    "adj_factor": "market",
    "stk_limit": "status",
    "suspend_d": "status",
}
_MAIN_BOARD = re.compile(r"(?:(?:000|001|002|003)[0-9]{3}\.SZ|(?:600|601|603|605)[0-9]{3}\.SH)")
_KEY: list[tuple[str, pa.DataType]] = [("instrument_id", pa.string()), ("trade_date", pa.date32())]
_BARS = pa.schema(
    _KEY
    + [(name, pa.float64()) for name in ("open", "high", "low", "close", "pre_close")]
    + [("volume", pa.int64()), ("adj_factor", pa.float64())]
)
_STATUS = pa.schema(
    _KEY
    + [
        ("up_limit", pa.float64()),
        ("down_limit", pa.float64()),
        ("suspended", pa.bool_()),
        ("source_revision", pa.string()),
    ]
)


@dataclass(frozen=True)
class DemoMarket:
    days: tuple[date, ...]
    next_session: date
    bars: pa.Table
    status: pa.Table
    source_evidence: dict[str, str]


def _verified_bytes(root: Path, path: Path, digest: str) -> bytes:
    resolved = (root / path).resolve()
    if not resolved.is_relative_to(root):
        raise ValueError(f"market evidence is outside the data root: {path}")
    payload = resolved.read_bytes()
    if hashlib.sha256(payload).hexdigest() != digest:
        raise ValueError(f"market evidence content hash mismatch: {path}")
    return payload


def _load_plan(
    connection: duckdb.DuckDBPyConnection, root: Path, digest: str
) -> DownloadPlanManifest:
    record = connection.execute(
        "SELECT manifest_uri, manifest_sha256, task_count FROM download_plans WHERE plan_sha256 = ?",
        [digest],
    ).fetchone()
    path = Path("plans") / digest / "manifest.json"
    if record is None or record[0] != path.as_posix():
        raise ValueError(f"unknown market download plan: {digest}")
    payload = _verified_bytes(root, path, record[1])
    plan = DownloadPlanManifest.model_validate_json(payload)
    if plan.plan_sha256 != digest or len(plan.tasks) != record[2]:
        raise ValueError("download plan catalog identity mismatch")
    return plan


def _completed_evidence(
    connection: duckdb.DuckDBPyConnection,
    plan: DownloadPlanManifest,
    keys: set[str],
) -> dict[str, CompletedDownloadEvidence]:
    planned = {task.task_key: task for task in plan.tasks if task.task_key in keys}
    records = connection.execute(
        """SELECT task_key, task_id, request_sha256, content_sha256, row_count,
                  evidence_uri, acquisition_outcome
           FROM download_tasks WHERE plan_sha256 = ? AND status = 'completed'
           AND task_key = ANY(?)""",
        [plan.plan_sha256, sorted(keys)],
    ).fetchall()
    evidence = {}
    for key, task_id, request, content, count, uri, outcome in records:
        task = planned.get(key)
        if task is None or task.task_id != task_id or key in evidence:
            raise ValueError(f"market task identity mismatch: {key}")
        evidence[key] = CompletedDownloadEvidence(
            task=task,
            request_sha256=request,
            content_sha256=content,
            row_count=count,
            evidence_uri=uri,
            acquisition_outcome=outcome,
        )
    missing = sorted(keys - evidence.keys())
    if missing:
        raise ValueError(f"completed market evidence missing: {missing[0]}")
    return evidence


def _selected_rows(
    root: Path,
    evidence: CompletedDownloadEvidence,
    endpoint: str,
    day: str,
    instruments: tuple[str, ...],
) -> dict[str, dict[str, Any]]:
    task = evidence.task
    params = {"trade_date": day}
    if endpoint == "suspend_d":
        params["suspend_type"] = "S"
    if task.endpoint != endpoint or dict(task.params) != params:
        raise ValueError(f"expected full-market {endpoint} request for {day}")
    path = Path("raw") / endpoint / f"{evidence.content_sha256}.parquet"
    if Path(evidence.evidence_uri) != path:
        raise ValueError(f"unexpected raw object path for {task.task_key}")
    payload = _verified_bytes(root, path, evidence.content_sha256)
    parquet = pq.ParquetFile(pa.BufferReader(payload))
    _verify_request_metadata(task, parquet.schema_arrow.metadata)
    if parquet.metadata.num_rows != evidence.row_count or not set(task.fields).issubset(
        parquet.schema_arrow.names
    ):
        raise ValueError(f"market object schema or row count mismatch: {task.task_key}")
    if endpoint == "suspend_d":
        # An empty or disjoint list proves absence only when it covers this market session.
        fields = ["ts_code", "trade_date", "suspend_type"]
        if not set(fields).issubset(task.fields):
            raise ValueError(f"incomplete suspension query fields: {task.task_key}")
        for row in parquet.read(columns=fields).to_pylist():
            if not row["ts_code"] or row["trade_date"] != day or row["suspend_type"] != "S":
                raise ValueError(f"suspension query contains unexpected data: {task.task_key}")
            if row["ts_code"] in instruments:
                raise ValueError(f"demo window includes a suspension: {day} {row['ts_code']}")
        return {}
    rows = {}
    if evidence.row_count:
        selected = pq.read_table(
            pa.BufferReader(payload),
            columns=list(task.fields),
            filters=[("ts_code", "in", instruments)],
        )
        for row in selected.to_pylist():
            symbol = row["ts_code"]
            if row["trade_date"] != day:
                raise ValueError(f"market observation date mismatch: {task.task_key} {symbol}")
            if symbol in rows:
                raise ValueError(f"duplicate {endpoint} observation: {day} {symbol}")
            rows[symbol] = row
    if missing := set(instruments) - rows.keys():
        raise ValueError(f"missing {endpoint} observation: {day} {min(missing)}")
    return rows


def _positive(row: dict[str, Any], field: str) -> float:
    value = row.get(field)
    if not isinstance(value, (int, float)) or not math.isfinite(value) or value <= 0:
        raise ValueError(f"invalid {field}: {row['trade_date']} {row['ts_code']}")
    return float(value)


def load_demo_market(
    data_root: Path,
    plan_sha256: str,
    start: date,
    end: date,
    instruments: tuple[str, ...],
) -> DemoMarket:
    """Load raw OHLC, share volume and direction limits for ordinary main-board A shares.

    Daily volume is an integer number of shares. TuShare reports lots of 100 shares.

    The demo supports sessions with continuous quotes and unchanged adjustment factors.
    A verified market-wide suspension request is required for every included session.
    """
    if not re.fullmatch(r"[0-9a-f]{64}", plan_sha256):
        raise ValueError("invalid market plan hash")
    if (
        not instruments
        or len(set(instruments)) != len(instruments)
        or not all(_MAIN_BOARD.fullmatch(symbol) for symbol in instruments)
    ):
        raise ValueError("select unique Shanghai or Shenzhen main-board A shares")
    root = data_root.resolve()
    with duckdb.connect(str(root / "research.duckdb"), read_only=True) as connection:
        connection.execute("BEGIN TRANSACTION")
        plan = _load_plan(connection, root, plan_sha256)
        if start < plan.config.start or end < start or end > plan.resolved_end:
            raise ValueError("demo date range is outside the plan")
        if set(instruments) - set(plan.instruments):
            raise ValueError("selected instruments are outside the plan")
        days = tuple(day for day in plan.sessions if start <= day <= end)
        next_session = next((day for day in plan.sessions if day > end), None)
        if not days or next_session is None:
            raise ValueError("demo requires trading days and a next session within the plan")
        keys = {
            f"{phase}.{endpoint}.{day:%Y%m%d}"
            for day in days
            for endpoint, phase in _ENDPOINTS.items()
        }
        evidence = _completed_evidence(connection, plan, keys)
    bars: list[dict[str, object]] = []
    status: list[dict[str, object]] = []
    last_close: dict[str, float] = {}
    factors: dict[str, float] = {}
    for day in days:
        key = day.strftime("%Y%m%d")
        daily = {
            endpoint: evidence[f"{phase}.{endpoint}.{key}"]
            for endpoint, phase in _ENDPOINTS.items()
        }
        observations = {
            endpoint: _selected_rows(root, item, endpoint, key, instruments)
            for endpoint, item in daily.items()
        }
        revision = f"{daily['stk_limit'].content_sha256}:{daily['suspend_d'].content_sha256}"
        for symbol in instruments:
            row = observations["daily"][symbol]
            prices = {
                name: _positive(row, name) for name in ("open", "high", "low", "close", "pre_close")
            }
            shares = Decimal(str(_positive(row, "vol"))) * 100
            if shares != shares.to_integral_value() or shares > 2**63 - 1:
                raise ValueError(f"volume must convert to whole shares in int64: {day} {symbol}")
            limits = observations["stk_limit"][symbol]
            upper, lower = _positive(limits, "up_limit"), _positive(limits, "down_limit")
            if not (
                lower
                <= prices["low"]
                <= min(prices["open"], prices["close"])
                <= max(prices["open"], prices["close"])
                <= prices["high"]
                <= upper
            ):
                raise ValueError(f"OHLC or daily limits disagree: {day} {symbol}")
            factor = _positive(observations["adj_factor"][symbol], "adj_factor")
            if symbol in factors and (
                factor != factors[symbol] or last_close[symbol] != prices["pre_close"]
            ):
                raise ValueError(f"adjustment factor or previous close changed: {day} {symbol}")
            factors[symbol], last_close[symbol] = factor, prices["close"]
            identity = {"instrument_id": symbol, "trade_date": day}
            bars.append({**identity, **prices, "volume": int(shares), "adj_factor": factor})
            status.append(
                {
                    **identity,
                    "up_limit": upper,
                    "down_limit": lower,
                    "suspended": False,
                    "source_revision": revision,
                }
            )
    return DemoMarket(
        days,
        next_session,
        pa.Table.from_pylist(bars, schema=_BARS),
        pa.Table.from_pylist(status, schema=_STATUS),
        {key: item.content_sha256 for key, item in sorted(evidence.items())},
    )
