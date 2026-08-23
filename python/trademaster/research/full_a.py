"""Deterministic, resumable Tushare download planning for twenty-year full-A research."""

from __future__ import annotations

import hashlib
import json
import tempfile
import uuid
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path, PurePosixPath
from typing import Literal, Self, cast

import duckdb
import pyarrow as pa
import pyarrow.parquet as pq
from pydantic import BaseModel, ConfigDict, Field, model_validator

from trademaster.data.research import ResearchDataSource

_SHA256 = r"^[0-9a-f]{64}$"
_COMPLETED_OUTCOMES = frozenset({"object_acquired", "verified_empty"})
_BLOCKED_OUTCOMES = frozenset({"source_history_absent", "permission_blocked"})
_ACQUISITION_OUTCOMES = _COMPLETED_OUTCOMES | _BLOCKED_OUTCOMES
_REQUEST_METADATA_KEYS = {
    "endpoint": b"trademaster.strategy.endpoint",
    "params": b"trademaster.strategy.params",
    "fields": b"trademaster.strategy.fields",
    "request_sha256": b"trademaster.strategy.request_sha256",
}
_PHASE_ORDER = {
    "bootstrap": 0,
    "market": 1,
    "status": 2,
    "statement": 3,
    "industry": 4,
    "benchmark": 5,
}

_STOCK_BASIC_FIELDS = (
    "ts_code",
    "symbol",
    "name",
    "market",
    "exchange",
    "curr_type",
    "list_status",
    "list_date",
    "delist_date",
)
_TRADE_CAL_FIELDS = ("exchange", "cal_date", "is_open", "pretrade_date")
_DAILY_FIELDS = (
    "ts_code",
    "trade_date",
    "open",
    "high",
    "low",
    "close",
    "pre_close",
    "vol",
    "amount",
)
_ADJ_FACTOR_FIELDS = ("ts_code", "trade_date", "adj_factor")
_DAILY_BASIC_FIELDS = (
    "ts_code",
    "trade_date",
    "turnover_rate",
    "total_mv",
    "circ_mv",
    "pe_ttm",
    "pb",
    "ps_ttm",
    "dv_ttm",
)
_LIMIT_FIELDS = ("ts_code", "trade_date", "up_limit", "down_limit")
_SUSPEND_FIELDS = ("ts_code", "trade_date", "suspend_type", "suspend_timing")
_ST_FIELDS = ("ts_code", "trade_date", "type", "type_name", "name")
_FINA_FIELDS = (
    "ts_code",
    "ann_date",
    "end_date",
    "update_flag",
    "roe",
    "roa",
    "grossprofit_margin",
    "netprofit_margin",
    "assets_turn",
    "ocf_to_or",
    "q_sales_yoy",
    "q_profit_yoy",
    "q_netprofit_yoy",
    "debt_to_assets",
    "current_ratio",
    "quick_ratio",
    "cash_ratio",
    "roe_yoy",
    "or_yoy",
    "netprofit_yoy",
    "ocf_yoy",
    "profit_dedt",
)
_INCOME_FIELDS = (
    "ts_code",
    "ann_date",
    "f_ann_date",
    "end_date",
    "report_type",
    "comp_type",
    "end_type",
    "total_revenue",
    "revenue",
    "operate_profit",
    "total_profit",
    "n_income",
    "n_income_attr_p",
    "ebit",
    "ebitda",
    "update_flag",
)
_BALANCE_FIELDS = (
    "ts_code",
    "ann_date",
    "f_ann_date",
    "end_date",
    "report_type",
    "comp_type",
    "end_type",
    "money_cap",
    "total_cur_assets",
    "total_assets",
    "total_cur_liab",
    "total_ncl",
    "total_liab",
    "total_hldr_eqy_exc_min_int",
    "total_hldr_eqy_inc_min_int",
    "update_flag",
)
_CASHFLOW_FIELDS = (
    "ts_code",
    "ann_date",
    "f_ann_date",
    "end_date",
    "report_type",
    "comp_type",
    "end_type",
    "n_cashflow_act",
    "n_cashflow_inv_act",
    "n_cash_flows_fnc_act",
    "n_incr_cash_cash_equ",
    "free_cashflow",
    "update_flag",
)
_INDEX_FIELDS = (
    "ts_code",
    "trade_date",
    "open",
    "high",
    "low",
    "close",
    "pre_close",
    "vol",
    "amount",
)
_INDEX_CLASSIFY_FIELDS = (
    "index_code",
    "industry_name",
    "parent_code",
    "level",
    "industry_code",
    "is_pub",
    "src",
)
_INDEX_MEMBER_FIELDS = (
    "l1_code",
    "l1_name",
    "l2_code",
    "l2_name",
    "l3_code",
    "l3_name",
    "ts_code",
    "name",
    "in_date",
    "out_date",
    "is_new",
)


def _canonical_json(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode()


def _sha256_json(value: object) -> str:
    return hashlib.sha256(_canonical_json(value)).hexdigest()


def canonical_request_sha256(
    endpoint: str,
    *,
    params: Mapping[str, object],
    fields: tuple[str, ...],
    page_limit: int,
) -> str:
    """Hash the exact provider request independently of mutable catalog state."""

    if (
        not endpoint
        or not fields
        or fields != tuple(dict.fromkeys(fields))
        or page_limit < 1
        or any(not key for key in params)
    ):
        raise ValueError("research request identity is invalid")
    return _sha256_json(
        {
            "endpoint": endpoint,
            "params": dict(params),
            "fields": fields,
            "page_limit": page_limit,
        }
    )


CompletedAcquisitionOutcome = Literal["object_acquired", "verified_empty"]
BlockedAcquisitionOutcome = Literal["source_history_absent", "permission_blocked"]
AcquisitionOutcome = Literal[
    "object_acquired",
    "verified_empty",
    "source_history_absent",
    "permission_blocked",
]


class AcquisitionBlockedError(RuntimeError):
    """Typed terminal provider limitation, distinct from a retryable acquisition failure."""

    def __init__(self, outcome: str, message: str) -> None:
        if outcome not in _BLOCKED_OUTCOMES or not message.strip():
            raise ValueError("blocked acquisition outcome is invalid")
        super().__init__(message)
        self.outcome = cast(BlockedAcquisitionOutcome, outcome)


def _compact(value: date) -> str:
    return value.strftime("%Y%m%d")


def _parse_compact_date(value: object) -> date:
    raw = str(value)
    if len(raw) != 8 or not raw.isdigit():
        raise ValueError(f"Tushare date is not YYYYMMDD: {raw}")
    return date(int(raw[:4]), int(raw[4:6]), int(raw[6:]))


def _optional_compact_date(value: object) -> date | None:
    if value is None or str(value) in {"", "nan", "NaT", "None"}:
        return None
    return _parse_compact_date(value)


class FullAResearchConfig(BaseModel):
    """Economic scope of one full-A research dataset, excluding runtime paths/secrets."""

    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    schema_id: Literal["trademaster.full-a-research-config/v1"] = (
        "trademaster.full-a-research-config/v1"
    )
    start: date
    requested_end: date
    statement_start: date = date(2005, 1, 1)
    venues: tuple[Literal["BSE", "SSE", "SZSE"], ...] = ("BSE", "SSE", "SZSE")
    benchmarks: tuple[str, ...] = (
        "000001.SH",
        "000016.SH",
        "000300.SH",
        "000688.SH",
        "000985.CSI",
        "399001.SZ",
        "399006.SZ",
        "899050.BJ",
    )

    @model_validator(mode="after")
    def validate_scope(self) -> FullAResearchConfig:
        if self.requested_end < self.start or self.statement_start > self.start:
            raise ValueError("full-A research date scope is invalid")
        if self.venues != tuple(sorted(set(self.venues))):
            raise ValueError("full-A research venues must be unique and sorted")
        if self.benchmarks != tuple(sorted(set(self.benchmarks))) or any(
            "." not in value for value in self.benchmarks
        ):
            raise ValueError("full-A research benchmarks must be unique and sorted")
        return self


class ResearchInstrument(BaseModel):
    """One historical A-share identity from all stock_basic listing states."""

    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    instrument_id: str = Field(pattern=r"^[A-Z0-9]+\.(SH|SZ|BJ)$")
    venue: Literal["BSE", "SSE", "SZSE"]
    list_date: date
    delist_date: date | None
    listing_status: Literal["L", "D", "P", "G"]

    @model_validator(mode="after")
    def validate_identity(self) -> ResearchInstrument:
        expected = {"SH": "SSE", "SZ": "SZSE", "BJ": "BSE"}[self.instrument_id.rsplit(".", 1)[-1]]
        if self.venue != expected:
            raise ValueError("research instrument suffix and venue disagree")
        if self.delist_date is not None and self.delist_date < self.list_date:
            raise ValueError("research instrument delists before listing")
        return self

    def overlaps(self, start: date, end: date) -> bool:
        return self.list_date <= end and (self.delist_date is None or self.delist_date >= start)


class DownloadTask(BaseModel):
    """Stable provider request and its expected research role."""

    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    schema_id: Literal["trademaster.full-a-download-task/v1"]
    task_id: str = Field(pattern=_SHA256)
    task_key: str = Field(pattern=r"^[A-Za-z0-9_.-]+$")
    phase: Literal["bootstrap", "market", "status", "statement", "industry", "benchmark"]
    endpoint: str = Field(pattern=r"^[a-z][a-z0-9_]*$")
    params: tuple[tuple[str, str], ...]
    fields: tuple[str, ...]
    page_limit: int = Field(gt=0, le=10_000)
    allow_empty: bool

    @model_validator(mode="after")
    def validate_identity(self) -> DownloadTask:
        if self.params != tuple(sorted(set(self.params))) or any(not key for key, _ in self.params):
            raise ValueError("download task params must be unique and sorted")
        if not self.fields or len(self.fields) != len(set(self.fields)):
            raise ValueError("download task fields must be nonempty and unique")
        payload = self.model_dump(mode="json", exclude={"task_id"})
        if self.task_id != _sha256_json(payload):
            raise ValueError("download task identity hash mismatch")
        return self

    @classmethod
    def build(
        cls,
        *,
        task_key: str,
        phase: Literal["bootstrap", "market", "status", "statement", "industry", "benchmark"],
        endpoint: str,
        params: dict[str, str],
        fields: tuple[str, ...],
        page_limit: int,
        allow_empty: bool,
    ) -> DownloadTask:
        base: dict[str, object] = {
            "schema_id": "trademaster.full-a-download-task/v1",
            "task_key": task_key,
            "phase": phase,
            "endpoint": endpoint,
            "params": tuple(sorted(params.items())),
            "fields": fields,
            "page_limit": page_limit,
            "allow_empty": allow_empty,
        }
        base["task_id"] = _sha256_json(base)
        return cls.model_validate(base)

    @property
    def sort_key(self) -> tuple[int, str]:
        return (_PHASE_ORDER[self.phase], self.task_key)

    @property
    def request_sha256(self) -> str:
        return canonical_request_sha256(
            self.endpoint,
            params=dict(self.params),
            fields=self.fields,
            page_limit=self.page_limit,
        )


def _expected_request_metadata(task: DownloadTask) -> dict[bytes, bytes]:
    return {
        _REQUEST_METADATA_KEYS["endpoint"]: task.endpoint.encode(),
        _REQUEST_METADATA_KEYS["params"]: _canonical_json(dict(task.params)),
        _REQUEST_METADATA_KEYS["fields"]: _canonical_json(task.fields),
        _REQUEST_METADATA_KEYS["request_sha256"]: task.request_sha256.encode(),
    }


def _verify_request_metadata(task: DownloadTask, metadata: Mapping[bytes, bytes] | None) -> None:
    actual = dict(metadata or {})
    if any(actual.get(key) != value for key, value in _expected_request_metadata(task).items()):
        raise RuntimeError("research evidence request metadata mismatch")


class DownloadPlanManifest(BaseModel):
    """Complete immutable request set for one twenty-year full-A download."""

    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    schema_id: Literal["trademaster.full-a-download-plan/v1"]
    plan_sha256: str = Field(pattern=_SHA256)
    config: FullAResearchConfig
    resolved_end: date
    instruments: tuple[str, ...]
    excluded_instruments: tuple[str, ...]
    sessions: tuple[date, ...]
    tasks: tuple[DownloadTask, ...]

    @model_validator(mode="after")
    def validate_identity(self) -> DownloadPlanManifest:
        if self.resolved_end > self.config.requested_end or self.resolved_end < self.config.start:
            raise ValueError("download plan resolved end is outside config scope")
        for values, label in (
            (self.instruments, "plan instruments"),
            (self.excluded_instruments, "plan excluded instruments"),
        ):
            if values != tuple(sorted(set(values))):
                raise ValueError(f"{label} must be unique and sorted")
        if set(self.instruments) & set(self.excluded_instruments):
            raise ValueError("download plan instrument sets overlap")
        if (
            not self.sessions
            or self.sessions != tuple(sorted(set(self.sessions)))
            or self.sessions[-1] != self.resolved_end
            or self.sessions[0] < self.config.start
        ):
            raise ValueError("download plan sessions must be canonical and in scope")
        if self.tasks != tuple(sorted(self.tasks, key=lambda item: item.sort_key)):
            raise ValueError("download plan tasks must be canonical")
        if len({item.task_id for item in self.tasks}) != len(self.tasks) or len(
            {item.task_key for item in self.tasks}
        ) != len(self.tasks):
            raise ValueError("download plan task identities must be unique")
        payload = self.model_dump(mode="json", exclude={"plan_sha256"})
        if self.plan_sha256 != _sha256_json(payload):
            raise ValueError("download plan hash mismatch")
        return self

    @classmethod
    def build(
        cls,
        *,
        config: FullAResearchConfig,
        resolved_end: date,
        instruments: tuple[str, ...],
        excluded_instruments: tuple[str, ...],
        sessions: tuple[date, ...],
        tasks: tuple[DownloadTask, ...],
    ) -> DownloadPlanManifest:
        ordered_tasks = tuple(sorted(tasks, key=lambda item: item.sort_key))
        base: dict[str, object] = {
            "schema_id": "trademaster.full-a-download-plan/v1",
            "config": config,
            "resolved_end": resolved_end,
            "instruments": instruments,
            "excluded_instruments": excluded_instruments,
            "sessions": sessions,
            "tasks": ordered_tasks,
        }
        hash_payload = {
            **base,
            "config": config.model_dump(mode="json"),
            "resolved_end": resolved_end.isoformat(),
            "sessions": [item.isoformat() for item in sessions],
            "tasks": [item.model_dump(mode="json") for item in ordered_tasks],
        }
        base["plan_sha256"] = _sha256_json(hash_payload)
        return cls.model_validate(base)

    def task(self, task_key: str) -> DownloadTask:
        try:
            return next(item for item in self.tasks if item.task_key == task_key)
        except StopIteration as error:
            raise KeyError(f"unknown download task: {task_key}") from error


class FullAResearchPlanner:
    """Expand a dynamic historical universe into endpoint-owned request tasks."""

    @staticmethod
    def _task(
        task_key: str,
        phase: Literal["bootstrap", "market", "status", "statement", "industry", "benchmark"],
        endpoint: str,
        params: dict[str, str],
        fields: tuple[str, ...],
        page_limit: int,
        *,
        allow_empty: bool = False,
    ) -> DownloadTask:
        return DownloadTask.build(
            task_key=task_key,
            phase=phase,
            endpoint=endpoint,
            params=params,
            fields=fields,
            page_limit=page_limit,
            allow_empty=allow_empty,
        )

    def bootstrap_tasks(self, config: FullAResearchConfig) -> tuple[DownloadTask, ...]:
        tasks: list[DownloadTask] = []
        for listing_status in ("D", "G", "L", "P"):
            tasks.append(
                self._task(
                    f"bootstrap.stock_basic.{listing_status}",
                    "bootstrap",
                    "stock_basic",
                    {"exchange": "", "list_status": listing_status},
                    _STOCK_BASIC_FIELDS,
                    6000,
                    allow_empty=True,
                )
            )
        for venue in ("SSE", "SZSE"):
            tasks.append(
                self._task(
                    f"bootstrap.trade_cal.{venue}",
                    "bootstrap",
                    "trade_cal",
                    {
                        "exchange": venue,
                        "start_date": _compact(config.start),
                        "end_date": _compact(config.requested_end),
                    },
                    _TRADE_CAL_FIELDS,
                    6000,
                )
            )
        return tuple(sorted(tasks, key=lambda item: item.sort_key))

    def build_from_bootstrap(
        self,
        config: FullAResearchConfig,
        tables: dict[str, pa.Table],
        *,
        market_lifecycles: dict[str, tuple[date, date]] | None = None,
    ) -> DownloadPlanManifest:
        expected = {item.task_key for item in self.bootstrap_tasks(config)}
        if set(tables) != expected:
            raise ValueError("bootstrap tables do not exactly match required tasks")
        instruments_by_id: dict[str, ResearchInstrument] = {}
        for status in ("D", "G", "L", "P"):
            table = tables[f"bootstrap.stock_basic.{status}"]
            if not set(_STOCK_BASIC_FIELDS) <= set(table.column_names):
                raise ValueError("stock_basic bootstrap schema is incomplete")
            for row in table.to_pylist():
                if str(row["curr_type"]) != "CNY":
                    continue
                instrument_id = str(row["ts_code"])
                suffix = instrument_id.rsplit(".", 1)[-1]
                venue = {"SH": "SSE", "SZ": "SZSE", "BJ": "BSE"}.get(suffix)
                if venue is None or venue not in config.venues:
                    continue
                instrument = ResearchInstrument(
                    instrument_id=instrument_id,
                    venue=venue,
                    list_date=_parse_compact_date(row["list_date"]),
                    delist_date=_optional_compact_date(row["delist_date"]),
                    listing_status=status,
                )
                existing = instruments_by_id.get(instrument_id)
                if existing is not None and existing != instrument:
                    raise ValueError("stock_basic bootstrap contains conflicting identities")
                instruments_by_id[instrument_id] = instrument
        if not instruments_by_id:
            raise ValueError("stock_basic bootstrap contains no CNY A-share instruments")

        open_sessions: dict[str, set[date]] = {}
        for venue in ("SSE", "SZSE"):
            table = tables[f"bootstrap.trade_cal.{venue}"]
            if not set(_TRADE_CAL_FIELDS) <= set(table.column_names):
                raise ValueError("trade_cal bootstrap schema is incomplete")
            venue_sessions: set[date] = set()
            for row in table.to_pylist():
                if str(row["exchange"]) != venue:
                    raise ValueError("trade_cal bootstrap venue mismatch")
                if int(row["is_open"]) == 1:
                    venue_sessions.add(_parse_compact_date(row["cal_date"]))
            open_sessions[venue] = venue_sessions
        if open_sessions["SSE"] != open_sessions["SZSE"]:
            raise ValueError("SSE and SZSE open-session calendars disagree")
        resolved_end = max(open_sessions["SSE"])
        for instrument_id, (first_session, last_session) in sorted(
            (market_lifecycles or {}).items()
        ):
            if instrument_id in instruments_by_id:
                continue
            if last_session < first_session or last_session > resolved_end:
                raise ValueError("market-inferred lifecycle is invalid")
            suffix = instrument_id.rsplit(".", 1)[-1]
            venue = {"SH": "SSE", "SZ": "SZSE", "BJ": "BSE"}.get(suffix)
            if venue is None or venue not in config.venues:
                raise ValueError("market-inferred instrument venue is invalid")
            instruments_by_id[instrument_id] = ResearchInstrument(
                instrument_id=instrument_id,
                venue=venue,
                list_date=first_session,
                delist_date=(last_session if last_session < resolved_end else None),
                listing_status=("D" if last_session < resolved_end else "L"),
            )
        return self.build(
            config,
            instruments=tuple(instruments_by_id.values()),
            sessions=open_sessions["SSE"],
        )

    def build(
        self,
        config: FullAResearchConfig,
        *,
        instruments: Iterable[ResearchInstrument],
        sessions: Iterable[date],
    ) -> DownloadPlanManifest:
        all_instruments = tuple(sorted(instruments, key=lambda item: item.instrument_id))
        if len({item.instrument_id for item in all_instruments}) != len(all_instruments):
            raise ValueError("research instruments must be unique")
        ordered_sessions = tuple(
            sorted({item for item in sessions if config.start <= item <= config.requested_end})
        )
        if not ordered_sessions:
            raise ValueError("research plan has no open sessions")
        resolved_end = ordered_sessions[-1]
        included = tuple(
            item
            for item in all_instruments
            if item.venue in config.venues and item.overlaps(config.start, resolved_end)
        )
        excluded = tuple(item.instrument_id for item in all_instruments if item not in included)
        if not included:
            raise ValueError("research plan has no historical A-share instruments")
        tasks: list[DownloadTask] = []
        tasks.extend(self.bootstrap_tasks(config))
        session_definitions = (
            ("market", "daily", _DAILY_FIELDS, 6000, False),
            ("market", "adj_factor", _ADJ_FACTOR_FIELDS, 6000, False),
            ("market", "daily_basic", _DAILY_BASIC_FIELDS, 6000, False),
            ("status", "stk_limit", _LIMIT_FIELDS, 5800, True),
            ("status", "suspend_d", _SUSPEND_FIELDS, 5000, True),
            ("status", "stock_st", _ST_FIELDS, 1000, True),
        )
        for session in ordered_sessions:
            compact = _compact(session)
            for (
                phase,
                endpoint,
                session_fields,
                page_limit,
                allow_empty,
            ) in session_definitions:
                params = {"trade_date": compact}
                if endpoint == "suspend_d":
                    params["suspend_type"] = "S"
                tasks.append(
                    self._task(
                        f"{phase}.{endpoint}.{compact}",
                        phase,  # type: ignore[arg-type]
                        endpoint,
                        params,
                        session_fields,
                        page_limit,
                        allow_empty=allow_empty,
                    )
                )
        statement_definitions = (
            ("fina_indicator", _FINA_FIELDS),
            ("income", _INCOME_FIELDS),
            ("balancesheet", _BALANCE_FIELDS),
            ("cashflow", _CASHFLOW_FIELDS),
        )
        for instrument in included:
            for endpoint, statement_fields in statement_definitions:
                tasks.append(
                    self._task(
                        f"statement.{endpoint}.{instrument.instrument_id}",
                        "statement",
                        endpoint,
                        {
                            "ts_code": instrument.instrument_id,
                            "start_date": _compact(config.statement_start),
                            "end_date": _compact(resolved_end),
                        },
                        statement_fields,
                        100,
                        allow_empty=True,
                    )
                )
        for level in ("L1", "L2", "L3"):
            tasks.append(
                self._task(
                    f"industry.index_classify.{level}",
                    "industry",
                    "index_classify",
                    {"level": level, "src": "SW2021"},
                    _INDEX_CLASSIFY_FIELDS,
                    2000,
                    allow_empty=True,
                )
            )
        for is_new in ("N", "Y"):
            tasks.append(
                self._task(
                    f"industry.index_member_all.{is_new}",
                    "industry",
                    "index_member_all",
                    {"is_new": is_new},
                    _INDEX_MEMBER_FIELDS,
                    2000,
                    allow_empty=True,
                )
            )
        for index_id in config.benchmarks:
            tasks.append(
                self._task(
                    f"benchmark.index_daily.{index_id}",
                    "benchmark",
                    "index_daily",
                    {
                        "ts_code": index_id,
                        "start_date": _compact(config.start),
                        "end_date": _compact(resolved_end),
                    },
                    _INDEX_FIELDS,
                    8000,
                    allow_empty=True,
                )
            )
        return DownloadPlanManifest.build(
            config=config,
            resolved_end=resolved_end,
            instruments=tuple(item.instrument_id for item in included),
            excluded_instruments=tuple(sorted(excluded)),
            sessions=ordered_sessions,
            tasks=tuple(tasks),
        )


class DownloadRunStatus(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    schema_id: Literal["trademaster.full-a-download-status/v2"] = (
        "trademaster.full-a-download-status/v2"
    )
    plan_sha256: str = Field(pattern=_SHA256)
    total: int = Field(ge=0)
    completed: int = Field(ge=0)
    pending: int = Field(ge=0)
    running: int = Field(ge=0)
    failed: int = Field(ge=0)
    blocked: int = Field(ge=0)
    object_acquired: int = Field(ge=0)
    verified_empty: int = Field(ge=0)
    source_history_absent: int = Field(ge=0)
    permission_blocked: int = Field(ge=0)

    @model_validator(mode="after")
    def validate_counts(self) -> DownloadRunStatus:
        if self.total != (
            self.completed + self.pending + self.running + self.failed + self.blocked
        ):
            raise ValueError("download status counts do not sum to total")
        if self.completed != self.object_acquired + self.verified_empty:
            raise ValueError("completed download outcomes do not sum to completed")
        if self.blocked != self.source_history_absent + self.permission_blocked:
            raise ValueError("blocked download outcomes do not sum to blocked")
        return self


class CompletedDownloadEvidence(BaseModel):
    """One completed task bound to its exact immutable provider object."""

    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    schema_id: Literal["trademaster.completed-download-evidence/v1"] = (
        "trademaster.completed-download-evidence/v1"
    )
    task: DownloadTask
    request_sha256: str = Field(pattern=_SHA256)
    content_sha256: str = Field(pattern=_SHA256)
    row_count: int = Field(ge=0)
    evidence_uri: str
    acquisition_outcome: CompletedAcquisitionOutcome

    @model_validator(mode="after")
    def validate_evidence(self) -> CompletedDownloadEvidence:
        uri = PurePosixPath(self.evidence_uri)
        if uri.is_absolute() or ".." in uri.parts or uri.suffix != ".parquet":
            raise ValueError("completed evidence URI is invalid")
        if self.request_sha256 != self.task.request_sha256:
            raise ValueError("completed evidence request identity mismatch")
        expected_outcome = "verified_empty" if self.row_count == 0 else "object_acquired"
        if self.acquisition_outcome != expected_outcome:
            raise ValueError("completed evidence outcome disagrees with row count")
        return self


class CompletedEvidenceSnapshot(BaseModel):
    """Transaction-consistent, content-addressed completed evidence selection."""

    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    schema_id: Literal["trademaster.completed-evidence-snapshot/v1"] = (
        "trademaster.completed-evidence-snapshot/v1"
    )
    snapshot_sha256: str = Field(pattern=_SHA256)
    plan_sha256: str = Field(pattern=_SHA256)
    evidence: tuple[CompletedDownloadEvidence, ...]

    @model_validator(mode="after")
    def validate_snapshot(self) -> CompletedEvidenceSnapshot:
        if self.evidence != tuple(sorted(self.evidence, key=lambda item: item.task.sort_key)):
            raise ValueError("completed evidence snapshot must be canonical")
        if len({item.task.task_id for item in self.evidence}) != len(self.evidence):
            raise ValueError("completed evidence snapshot contains duplicate tasks")
        payload = self.model_dump(mode="json", exclude={"snapshot_sha256"})
        if self.snapshot_sha256 != _sha256_json(payload):
            raise ValueError("completed evidence snapshot hash mismatch")
        return self

    @classmethod
    def build(
        cls,
        *,
        plan_sha256: str,
        evidence: tuple[CompletedDownloadEvidence, ...],
    ) -> CompletedEvidenceSnapshot:
        ordered = tuple(sorted(evidence, key=lambda item: item.task.sort_key))
        base: dict[str, object] = {
            "schema_id": "trademaster.completed-evidence-snapshot/v1",
            "plan_sha256": plan_sha256,
            "evidence": [item.model_dump(mode="json") for item in ordered],
        }
        base["snapshot_sha256"] = _sha256_json(base)
        return cls.model_validate({**base, "evidence": ordered})


class FullAResearchStore:
    """JSON-authoritative plan store with resumable DuckDB task state."""

    def __init__(self, root: Path, *, clock: Callable[[], datetime]) -> None:
        self.root = root.resolve()
        if self.root == Path(self.root.anchor):
            raise ValueError("research root cannot be the filesystem root")
        self.clock = clock
        self.plans = self.root / "plans"
        self.temporary = self.root / ".tmp"
        self.plans.mkdir(parents=True, exist_ok=True)
        self.temporary.mkdir(parents=True, exist_ok=True)
        self._connection = duckdb.connect(str(self.root / "research.duckdb"))
        self._connection.execute(
            """
            CREATE TABLE IF NOT EXISTS download_plans (
                plan_sha256 VARCHAR PRIMARY KEY,
                manifest_uri VARCHAR NOT NULL UNIQUE,
                manifest_sha256 VARCHAR NOT NULL,
                task_count BIGINT NOT NULL,
                created_at TIMESTAMPTZ NOT NULL
            )
            """
        )
        self._connection.execute(
            """
            CREATE TABLE IF NOT EXISTS download_tasks (
                plan_sha256 VARCHAR NOT NULL,
                task_id VARCHAR NOT NULL,
                task_key VARCHAR NOT NULL,
                endpoint VARCHAR NOT NULL,
                phase VARCHAR NOT NULL,
                status VARCHAR NOT NULL,
                attempts BIGINT NOT NULL,
                request_sha256 VARCHAR,
                content_sha256 VARCHAR,
                row_count BIGINT,
                evidence_uri VARCHAR,
                error_type VARCHAR,
                error_message VARCHAR,
                updated_at TIMESTAMPTZ NOT NULL,
                acquisition_outcome VARCHAR,
                lease_owner VARCHAR,
                lease_expires_at TIMESTAMPTZ,
                PRIMARY KEY (plan_sha256, task_id),
                UNIQUE (plan_sha256, task_key)
            )
            """
        )
        for definition in (
            "acquisition_outcome VARCHAR",
            "lease_owner VARCHAR",
            "lease_expires_at TIMESTAMPTZ",
        ):
            self._connection.execute(
                f"ALTER TABLE download_tasks ADD COLUMN IF NOT EXISTS {definition}"
            )
        self._connection.execute(
            """
            UPDATE download_tasks
            SET acquisition_outcome = CASE
                WHEN row_count = 0 THEN 'verified_empty'
                ELSE 'object_acquired'
            END
            WHERE status = 'completed' AND acquisition_outcome IS NULL
            """
        )

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def close(self) -> None:
        self._connection.close()

    def _relative(self, path: Path) -> str:
        try:
            return path.resolve().relative_to(self.root).as_posix()
        except ValueError as error:
            raise RuntimeError("research artifact path escapes its root") from error

    def _write_atomic(self, path: Path, payload: bytes) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists():
            if path.read_bytes() != payload:
                raise RuntimeError("research artifact identity collision")
            return
        with tempfile.NamedTemporaryFile(
            dir=self.temporary, prefix="research-", suffix=".tmp", delete=False
        ) as handle:
            temporary = Path(handle.name)
            handle.write(payload)
        try:
            temporary.replace(path)
        finally:
            if temporary.exists():
                temporary.unlink()

    def register(self, plan: DownloadPlanManifest) -> None:
        payload = _canonical_json(plan.model_dump(mode="json"))
        path = self.plans / plan.plan_sha256 / "manifest.json"
        self._write_atomic(path, payload)
        uri = self._relative(path)
        manifest_sha = hashlib.sha256(payload).hexdigest()
        existing = self._connection.execute(
            """
            SELECT manifest_uri, manifest_sha256, task_count FROM download_plans
            WHERE plan_sha256 = ?
            """,
            [plan.plan_sha256],
        ).fetchone()
        expected = (uri, manifest_sha, len(plan.tasks))
        if existing is not None and tuple(existing) != expected:
            raise RuntimeError("download plan catalog identity conflicts")
        self._connection.execute("BEGIN TRANSACTION")
        try:
            if existing is None:
                self._connection.execute(
                    "INSERT INTO download_plans VALUES (?, ?, ?, ?, ?)",
                    [plan.plan_sha256, uri, manifest_sha, len(plan.tasks), self.clock()],
                )
            existing_task_rows = self._connection.execute(
                """
                SELECT task_id, task_key, endpoint, phase FROM download_tasks
                WHERE plan_sha256 = ?
                """,
                [plan.plan_sha256],
            ).fetchall()
            existing_tasks = {
                str(row[0]): tuple(str(value) for value in row[1:]) for row in existing_task_rows
            }
            new_rows: list[list[object]] = []
            updated_at = self.clock()
            for task in plan.tasks:
                task_identity = (task.task_key, task.endpoint, task.phase)
                existing_task = existing_tasks.get(task.task_id)
                if existing_task is not None:
                    if existing_task != task_identity:
                        raise RuntimeError("download task catalog identity conflicts")
                    continue
                new_rows.append(
                    [
                        plan.plan_sha256,
                        task.task_id,
                        task.task_key,
                        task.endpoint,
                        task.phase,
                        updated_at,
                    ]
                )
            if new_rows:
                self._connection.executemany(
                    """
                    INSERT INTO download_tasks (
                        plan_sha256, task_id, task_key, endpoint, phase, status,
                        attempts, request_sha256, content_sha256, row_count,
                        evidence_uri, error_type, error_message, updated_at,
                        acquisition_outcome, lease_owner, lease_expires_at
                    ) VALUES (
                        ?, ?, ?, ?, ?, 'pending', 0, NULL, NULL, NULL,
                        NULL, NULL, NULL, ?, NULL, NULL, NULL
                    )
                    """,
                    new_rows,
                )
            lease_cutoff = self.clock()
            self._connection.execute(
                """
                UPDATE download_tasks
                SET status = 'pending', lease_owner = NULL, lease_expires_at = NULL,
                    acquisition_outcome = NULL, updated_at = ?
                WHERE plan_sha256 = ? AND status = 'running'
                  AND (lease_expires_at IS NULL OR lease_expires_at <= ?)
                """,
                [lease_cutoff, plan.plan_sha256, lease_cutoff],
            )
            self._connection.execute("COMMIT")
        except Exception:
            self._connection.execute("ROLLBACK")
            raise

    def plan_path(self, plan_sha256: str) -> Path:
        row = self._connection.execute(
            "SELECT manifest_uri FROM download_plans WHERE plan_sha256 = ?",
            [plan_sha256],
        ).fetchone()
        if row is None:
            raise KeyError(f"unknown download plan: {plan_sha256}")
        uri = PurePosixPath(str(row[0]))
        if uri.is_absolute() or ".." in uri.parts:
            raise RuntimeError("download plan URI is invalid")
        path = (self.root / uri).resolve()
        self._relative(path)
        return path

    def load_plan(self, plan_sha256: str) -> DownloadPlanManifest:
        row = self._connection.execute(
            """
            SELECT manifest_uri, manifest_sha256, task_count FROM download_plans
            WHERE plan_sha256 = ?
            """,
            [plan_sha256],
        ).fetchone()
        if row is None:
            raise KeyError(f"unknown download plan: {plan_sha256}")
        path = self.plan_path(plan_sha256)
        try:
            payload = path.read_bytes()
        except OSError as error:
            raise RuntimeError("download plan manifest is missing") from error
        if hashlib.sha256(payload).hexdigest() != str(row[1]):
            raise RuntimeError("download plan manifest hash mismatch")
        try:
            plan = DownloadPlanManifest.model_validate_json(payload, strict=True)
        except ValueError as error:
            raise RuntimeError("download plan manifest is invalid") from error
        if plan.plan_sha256 != plan_sha256 or len(plan.tasks) != int(row[2]):
            raise RuntimeError("download plan catalog identity mismatch")
        task_rows = self._connection.execute(
            """
            SELECT task_id, task_key, endpoint, phase FROM download_tasks
            WHERE plan_sha256 = ? ORDER BY task_id
            """,
            [plan_sha256],
        ).fetchall()
        expected = tuple(
            sorted((item.task_id, item.task_key, item.endpoint, item.phase) for item in plan.tasks)
        )
        if tuple(tuple(str(value) for value in item) for item in task_rows) != expected:
            raise RuntimeError("download task catalog differs from authoritative plan")
        return plan

    def inherit_completed(
        self,
        source_plan: DownloadPlanManifest,
        target_plan: DownloadPlanManifest,
    ) -> int:
        """Copy verified completions only when the full task hash is identical."""

        if source_plan.plan_sha256 == target_plan.plan_sha256:
            raise ValueError("download plan inheritance requires different plans")
        if (
            self.load_plan(source_plan.plan_sha256) != source_plan
            or self.load_plan(target_plan.plan_sha256) != target_plan
        ):
            raise RuntimeError("download plan inheritance authority mismatch")
        target_tasks = {item.task_id: item for item in target_plan.tasks}
        rows = self._connection.execute(
            """
            SELECT task_id, attempts, request_sha256, content_sha256,
                   row_count, evidence_uri, acquisition_outcome
            FROM download_tasks
            WHERE plan_sha256 = ? AND status = 'completed'
            ORDER BY task_id
            """,
            [source_plan.plan_sha256],
        ).fetchall()
        inherited: list[list[object]] = []
        for task_id, attempts, request_sha, content_sha, row_count, raw_uri, raw_outcome in rows:
            task = target_tasks.get(str(task_id))
            if task is None:
                continue
            if request_sha is None or content_sha is None or row_count is None or raw_uri is None:
                raise RuntimeError("completed source task lacks evidence identity")
            if str(request_sha) != task.request_sha256:
                raise RuntimeError("inherited download evidence request identity mismatch")
            outcome = str(raw_outcome)
            expected_outcome = "verified_empty" if int(row_count) == 0 else "object_acquired"
            if outcome != expected_outcome:
                raise RuntimeError("inherited download evidence outcome mismatch")
            uri = PurePosixPath(str(raw_uri))
            if uri.is_absolute() or ".." in uri.parts:
                raise RuntimeError("inherited download evidence URI is invalid")
            path = (self.root / uri).resolve()
            self._relative(path)
            if not path.is_file():
                raise RuntimeError("inherited download evidence is missing")
            digest = hashlib.sha256()
            with path.open("rb") as handle:
                for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                    digest.update(chunk)
            if digest.hexdigest() != str(content_sha):
                raise RuntimeError("inherited download evidence content hash mismatch")
            try:
                metadata = pq.read_metadata(path)
            except Exception as error:
                raise RuntimeError("inherited download evidence is unreadable") from error
            actual_rows = metadata.num_rows
            if actual_rows != int(row_count):
                raise RuntimeError("inherited download evidence row count mismatch")
            _verify_request_metadata(task, metadata.schema.to_arrow_schema().metadata)
            inherited.append(
                [
                    int(attempts),
                    str(request_sha),
                    str(content_sha),
                    int(row_count),
                    uri.as_posix(),
                    outcome,
                    self.clock(),
                    target_plan.plan_sha256,
                    str(task_id),
                ]
            )
        if inherited:
            self._connection.executemany(
                """
                UPDATE download_tasks
                SET status = 'completed', attempts = ?, request_sha256 = ?,
                    content_sha256 = ?, row_count = ?, evidence_uri = ?,
                    acquisition_outcome = ?, error_type = NULL, error_message = NULL,
                    lease_owner = NULL, lease_expires_at = NULL, updated_at = ?
                WHERE plan_sha256 = ? AND task_id = ? AND status = 'pending'
                """,
                inherited,
            )
        return len(inherited)

    def pending_tasks(self, plan: DownloadPlanManifest) -> tuple[DownloadTask, ...]:
        rows = self._connection.execute(
            """
            SELECT task_id FROM download_tasks
            WHERE plan_sha256 = ? AND status IN ('pending', 'failed')
            ORDER BY task_key
            """,
            [plan.plan_sha256],
        ).fetchall()
        by_id = {item.task_id: item for item in plan.tasks}
        try:
            return tuple(by_id[str(row[0])] for row in rows)
        except KeyError as error:
            raise RuntimeError("download state references an unknown task") from error

    def claim_next(
        self,
        plan: DownloadPlanManifest,
        *,
        lease_owner: str,
        lease_duration: timedelta,
        phases: tuple[str, ...] = (),
    ) -> DownloadTask | None:
        """Atomically claim one task, preserving live leases from other workers."""

        if not lease_owner.strip() or len(lease_owner) > 200:
            raise ValueError("download lease owner is invalid")
        if lease_duration <= timedelta(0):
            raise ValueError("download lease duration must be positive")
        if phases != tuple(dict.fromkeys(phases)) or any(
            phase not in _PHASE_ORDER for phase in phases
        ):
            raise ValueError("download phases must be unique and known")
        by_id = {item.task_id: item for item in plan.tasks}
        phase_clause = ""
        phase_parameters: list[object] = []
        if phases:
            phase_clause = " AND phase IN (" + ",".join("?" for _ in phases) + ")"
            phase_parameters.extend(phases)

        for attempt in range(8):
            try:
                self._connection.execute("BEGIN TRANSACTION")
                now = self.clock()
                self._connection.execute(
                    """
                    UPDATE download_tasks
                    SET status = 'pending', lease_owner = NULL, lease_expires_at = NULL,
                        acquisition_outcome = NULL, error_type = NULL,
                        error_message = NULL, updated_at = ?
                    WHERE plan_sha256 = ? AND status = 'running'
                      AND (lease_expires_at IS NULL OR lease_expires_at <= ?)
                    """,
                    [now, plan.plan_sha256, now],
                )
                row = self._connection.execute(
                    f"""
                    SELECT task_id FROM download_tasks
                    WHERE plan_sha256 = ? AND status IN ('pending', 'failed')
                    {phase_clause}
                    ORDER BY task_key LIMIT 1
                    """,
                    [plan.plan_sha256, *phase_parameters],
                ).fetchone()
                if row is None:
                    self._connection.execute("COMMIT")
                    return None
                task_id = str(row[0])
                claimed = self._connection.execute(
                    """
                    UPDATE download_tasks
                    SET status = 'running', attempts = attempts + 1,
                        acquisition_outcome = NULL, error_type = NULL,
                        error_message = NULL, lease_owner = ?, lease_expires_at = ?,
                        updated_at = ?
                    WHERE plan_sha256 = ? AND task_id = ?
                      AND status IN ('pending', 'failed')
                    RETURNING task_id
                    """,
                    [
                        lease_owner,
                        now + lease_duration,
                        now,
                        plan.plan_sha256,
                        task_id,
                    ],
                ).fetchone()
                if claimed is None:
                    self._connection.execute("ROLLBACK")
                    continue
                self._connection.execute("COMMIT")
                try:
                    return by_id[str(claimed[0])]
                except KeyError as error:
                    raise RuntimeError("download state references an unknown task") from error
            except duckdb.TransactionException as error:
                try:
                    self._connection.execute("ROLLBACK")
                except duckdb.TransactionException:
                    pass
                if attempt == 7:
                    raise RuntimeError("download task claim repeatedly conflicted") from error
            except Exception:
                try:
                    self._connection.execute("ROLLBACK")
                except duckdb.TransactionException:
                    pass
                raise
        raise AssertionError("download task claim retry loop must return or raise")

    def mark_running(self, plan_sha256: str, task: DownloadTask) -> None:
        claimed = self._connection.execute(
            """
            UPDATE download_tasks
            SET status = 'running', attempts = attempts + 1,
                acquisition_outcome = NULL, error_type = NULL, error_message = NULL,
                lease_owner = NULL, lease_expires_at = NULL, updated_at = ?
            WHERE plan_sha256 = ? AND task_id = ? AND status IN ('pending', 'failed')
            RETURNING task_id
            """,
            [self.clock(), plan_sha256, task.task_id],
        ).fetchone()
        if claimed is None:
            raise RuntimeError("download task could not enter legacy running state")

    def mark_completed(
        self,
        plan_sha256: str,
        task: DownloadTask,
        *,
        request_sha256: str,
        content_sha256: str,
        row_count: int,
        evidence_path: Path,
        lease_owner: str | None = None,
    ) -> None:
        if row_count < 0:
            raise ValueError("download evidence row count cannot be negative")
        if request_sha256 != task.request_sha256:
            raise RuntimeError("download evidence request identity mismatch")
        evidence_uri = self._relative(evidence_path)
        if not evidence_path.is_file():
            raise RuntimeError("download evidence Parquet is missing")
        digest = hashlib.sha256()
        with evidence_path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
        if digest.hexdigest() != content_sha256:
            raise RuntimeError("download evidence content hash mismatch")
        try:
            metadata = pq.read_metadata(evidence_path)
        except Exception as error:
            raise RuntimeError("download evidence Parquet is unreadable") from error
        if metadata.num_rows != row_count:
            raise RuntimeError("download evidence row count mismatch")
        _verify_request_metadata(task, metadata.schema.to_arrow_schema().metadata)
        now = self.clock()
        outcome: CompletedAcquisitionOutcome = (
            "verified_empty" if row_count == 0 else "object_acquired"
        )
        lease_clause = " AND lease_owner IS NULL"
        parameters: list[object] = [
            request_sha256,
            content_sha256,
            row_count,
            evidence_uri,
            outcome,
            now,
            plan_sha256,
            task.task_id,
        ]
        if lease_owner is not None:
            lease_clause = " AND lease_owner = ? AND lease_expires_at > ?"
            parameters.extend([lease_owner, now])
        completed = self._connection.execute(
            f"""
            UPDATE download_tasks
            SET status = 'completed', request_sha256 = ?, content_sha256 = ?,
                row_count = ?, evidence_uri = ?, acquisition_outcome = ?,
                error_type = NULL, error_message = NULL, lease_owner = NULL,
                lease_expires_at = NULL, updated_at = ?
            WHERE plan_sha256 = ? AND task_id = ? AND status = 'running'
                  {lease_clause}
            RETURNING task_id
            """,
            parameters,
        ).fetchone()
        if completed is None:
            raise RuntimeError("download task lease was lost before completion")

    def mark_failed(
        self,
        plan_sha256: str,
        task: DownloadTask,
        error: Exception,
        *,
        lease_owner: str | None = None,
    ) -> None:
        message = str(error).replace("\n", " ")[:1000]
        now = self.clock()
        lease_clause = " AND lease_owner IS NULL"
        parameters: list[object] = [
            type(error).__name__,
            message,
            now,
            plan_sha256,
            task.task_id,
        ]
        if lease_owner is not None:
            lease_clause = " AND lease_owner = ? AND lease_expires_at > ?"
            parameters.extend([lease_owner, now])
        failed = self._connection.execute(
            f"""
            UPDATE download_tasks
            SET status = 'failed', acquisition_outcome = NULL, error_type = ?,
                error_message = ?, lease_owner = NULL, lease_expires_at = NULL,
                updated_at = ?
            WHERE plan_sha256 = ? AND task_id = ? AND status = 'running'
                  {lease_clause}
            RETURNING task_id
            """,
            parameters,
        ).fetchone()
        if failed is None:
            raise RuntimeError("download task lease was lost before failure recording")

    def mark_blocked(
        self,
        plan_sha256: str,
        task: DownloadTask,
        error: AcquisitionBlockedError,
        *,
        lease_owner: str,
    ) -> None:
        message = str(error).replace("\n", " ")[:1000]
        now = self.clock()
        blocked = self._connection.execute(
            """
            UPDATE download_tasks
            SET status = 'blocked', acquisition_outcome = ?, error_type = ?,
                error_message = ?, lease_owner = NULL, lease_expires_at = NULL,
                updated_at = ?
            WHERE plan_sha256 = ? AND task_id = ? AND status = 'running'
              AND lease_owner = ? AND lease_expires_at > ?
            RETURNING task_id
            """,
            [
                error.outcome,
                type(error).__name__,
                message,
                now,
                plan_sha256,
                task.task_id,
                lease_owner,
                now,
            ],
        ).fetchone()
        if blocked is None:
            raise RuntimeError("download task lease was lost before blocker recording")

    def status(self, plan_sha256: str) -> DownloadRunStatus:
        rows = self._connection.execute(
            """
            SELECT status, acquisition_outcome, count(*) FROM download_tasks
            WHERE plan_sha256 = ? GROUP BY status, acquisition_outcome
            """,
            [plan_sha256],
        ).fetchall()
        counts: dict[str, int] = {}
        outcome_counts: dict[str, int] = {}
        for raw_status, raw_outcome, raw_count in rows:
            task_status = str(raw_status)
            count = int(raw_count)
            counts[task_status] = counts.get(task_status, 0) + count
            outcome = None if raw_outcome is None else str(raw_outcome)
            if task_status == "completed" and outcome not in _COMPLETED_OUTCOMES:
                raise RuntimeError("completed download task has no valid acquisition outcome")
            if task_status == "blocked" and outcome not in _BLOCKED_OUTCOMES:
                raise RuntimeError("blocked download task has no valid acquisition outcome")
            if task_status not in {"completed", "blocked"} and outcome is not None:
                raise RuntimeError("nonterminal download task has an acquisition outcome")
            if outcome is not None:
                outcome_counts[outcome] = outcome_counts.get(outcome, 0) + count
        total = sum(counts.values())
        return DownloadRunStatus(
            plan_sha256=plan_sha256,
            total=total,
            completed=counts.get("completed", 0),
            pending=counts.get("pending", 0),
            running=counts.get("running", 0),
            failed=counts.get("failed", 0),
            blocked=counts.get("blocked", 0),
            object_acquired=outcome_counts.get("object_acquired", 0),
            verified_empty=outcome_counts.get("verified_empty", 0),
            source_history_absent=outcome_counts.get("source_history_absent", 0),
            permission_blocked=outcome_counts.get("permission_blocked", 0),
        )

    def completed_evidence_snapshot(
        self,
        plan: DownloadPlanManifest,
        *,
        endpoints: tuple[str, ...] = (),
    ) -> CompletedEvidenceSnapshot:
        if endpoints != tuple(sorted(set(endpoints))) or any(not value for value in endpoints):
            raise ValueError("completed evidence endpoint filter must be canonical")
        endpoint_clause = ""
        endpoint_parameters: list[object] = []
        if endpoints:
            endpoint_clause = " AND endpoint IN (" + ",".join("?" for _ in endpoints) + ")"
            endpoint_parameters.extend(endpoints)
        self._connection.execute("BEGIN TRANSACTION")
        try:
            loaded = self.load_plan(plan.plan_sha256)
            if loaded != plan:
                raise RuntimeError("completed evidence plan differs from persisted authority")
            rows = self._connection.execute(
                f"""
                SELECT task_id, request_sha256, content_sha256, row_count,
                       evidence_uri, acquisition_outcome
                FROM download_tasks
                WHERE plan_sha256 = ? AND status = 'completed' {endpoint_clause}
                ORDER BY task_key
                """,
                [plan.plan_sha256, *endpoint_parameters],
            ).fetchall()
            by_id = {item.task_id: item for item in plan.tasks}
            evidence: list[CompletedDownloadEvidence] = []
            for task_id, request_sha, content_sha, row_count, raw_uri, raw_outcome in rows:
                try:
                    task = by_id[str(task_id)]
                except KeyError as error:
                    raise RuntimeError("completed evidence references an unknown task") from error
                if any(value is None for value in (request_sha, content_sha, row_count, raw_uri)):
                    raise RuntimeError("completed download task lacks evidence identity")
                uri = PurePosixPath(str(raw_uri))
                path = (self.root / uri).resolve()
                self._relative(path)
                if not path.is_file():
                    raise RuntimeError("download evidence Parquet is missing")
                digest = hashlib.sha256()
                with path.open("rb") as handle:
                    for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                        digest.update(chunk)
                if digest.hexdigest() != str(content_sha):
                    raise RuntimeError("download evidence content hash mismatch")
                try:
                    metadata = pq.read_metadata(path)
                except Exception as error:
                    raise RuntimeError("download evidence Parquet is unreadable") from error
                if metadata.num_rows != int(row_count):
                    raise RuntimeError("download evidence row count mismatch")
                _verify_request_metadata(task, metadata.schema.to_arrow_schema().metadata)
                evidence.append(
                    CompletedDownloadEvidence(
                        task=task,
                        request_sha256=str(request_sha),
                        content_sha256=str(content_sha),
                        row_count=int(row_count),
                        evidence_uri=uri.as_posix(),
                        acquisition_outcome=cast(CompletedAcquisitionOutcome, str(raw_outcome)),
                    )
                )
            snapshot = CompletedEvidenceSnapshot.build(
                plan_sha256=plan.plan_sha256,
                evidence=tuple(evidence),
            )
            self._connection.execute("COMMIT")
            return snapshot
        except Exception:
            self._connection.execute("ROLLBACK")
            raise

    def verify(self, plan: DownloadPlanManifest) -> DownloadRunStatus:
        loaded = self.load_plan(plan.plan_sha256)
        if loaded != plan:
            raise RuntimeError("download plan differs from persisted authority")
        status = self.status(plan.plan_sha256)
        if status.pending or status.running or status.failed:
            raise RuntimeError("download plan is incomplete")
        snapshot = self.completed_evidence_snapshot(plan)
        if len(snapshot.evidence) != status.completed:
            raise RuntimeError("completed download evidence set is incomplete")
        if status.completed + status.blocked != status.total:
            raise RuntimeError("download terminal outcomes do not cover the plan")
        return status


@dataclass(frozen=True, slots=True)
class FullAResearchDownloader:
    source: ResearchDataSource
    store: FullAResearchStore

    @staticmethod
    def _verify_result(task: DownloadTask, result: object) -> None:
        from trademaster.data.research import ResearchQueryResult

        if not isinstance(result, ResearchQueryResult):
            raise TypeError("research source returned an invalid result")
        evidence = result.evidence
        if evidence.endpoint != task.endpoint:
            raise RuntimeError("research evidence endpoint mismatch")
        if evidence.request_sha256 != task.request_sha256:
            raise RuntimeError("research evidence request identity mismatch")
        if not evidence.path.is_file():
            raise RuntimeError("research evidence Parquet is missing")
        digest = hashlib.sha256()
        with evidence.path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
        if digest.hexdigest() != evidence.content_sha256:
            raise RuntimeError("research evidence content hash mismatch")
        try:
            persisted = pq.read_metadata(evidence.path)
        except Exception as error:
            raise RuntimeError("research evidence Parquet is unreadable") from error
        _verify_request_metadata(task, persisted.schema.to_arrow_schema().metadata)
        if (
            persisted.num_rows != evidence.row_count
            or result.table.num_rows != evidence.row_count
            or not set(task.fields) <= set(persisted.schema.to_arrow_schema().names)
            or not set(task.fields) <= set(result.table.column_names)
        ):
            raise RuntimeError("research evidence rows or fields mismatch")

    def run(
        self,
        plan: DownloadPlanManifest,
        *,
        max_tasks: int | None = None,
        phases: tuple[str, ...] = (),
        progress: Callable[[DownloadRunStatus, DownloadTask], None] | None = None,
        progress_every: int = 100,
        lease_duration: timedelta = timedelta(hours=1),
    ) -> DownloadRunStatus:
        if max_tasks is not None and max_tasks < 1:
            raise ValueError("max_tasks must be positive")
        if phases != tuple(dict.fromkeys(phases)) or any(
            value not in _PHASE_ORDER for value in phases
        ):
            raise ValueError("download phases must be unique and known")
        if progress_every < 1:
            raise ValueError("progress_every must be positive")
        if lease_duration <= timedelta(0):
            raise ValueError("lease_duration must be positive")
        self.store.register(plan)
        lease_owner = uuid.uuid4().hex
        processed = 0
        while max_tasks is None or processed < max_tasks:
            task = self.store.claim_next(
                plan,
                lease_owner=lease_owner,
                lease_duration=lease_duration,
                phases=phases,
            )
            if task is None:
                break
            processed += 1
            try:
                result = self.source.query_with_evidence(
                    task.endpoint,
                    params=dict(task.params),
                    fields=task.fields,
                    page_limit=task.page_limit,
                )
                self._verify_result(task, result)
                if not task.allow_empty and result.table.num_rows == 0:
                    raise RuntimeError(
                        f"required Tushare endpoint returned no rows: {task.endpoint}"
                    )
                self.store.mark_completed(
                    plan.plan_sha256,
                    task,
                    request_sha256=result.evidence.request_sha256,
                    content_sha256=result.evidence.content_sha256,
                    row_count=result.evidence.row_count,
                    evidence_path=result.evidence.path,
                    lease_owner=lease_owner,
                )
                if progress is not None and (processed == 1 or processed % progress_every == 0):
                    progress(self.store.status(plan.plan_sha256), task)
            except AcquisitionBlockedError as error:
                self.store.mark_blocked(
                    plan.plan_sha256,
                    task,
                    error,
                    lease_owner=lease_owner,
                )
                if progress is not None and (processed == 1 or processed % progress_every == 0):
                    progress(self.store.status(plan.plan_sha256), task)
            except Exception as error:
                self.store.mark_failed(
                    plan.plan_sha256,
                    task,
                    error,
                    lease_owner=lease_owner,
                )
                raise
        return self.store.status(plan.plan_sha256)


__all__ = [
    "AcquisitionBlockedError",
    "AcquisitionOutcome",
    "BlockedAcquisitionOutcome",
    "CompletedAcquisitionOutcome",
    "CompletedDownloadEvidence",
    "CompletedEvidenceSnapshot",
    "DownloadPlanManifest",
    "DownloadRunStatus",
    "DownloadTask",
    "FullAResearchConfig",
    "FullAResearchDownloader",
    "FullAResearchPlanner",
    "FullAResearchStore",
    "ResearchInstrument",
    "canonical_request_sha256",
]
