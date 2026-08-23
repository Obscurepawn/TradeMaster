"""Credential-isolated Tushare adapter with raw and staging evidence."""

from __future__ import annotations

import hashlib
import importlib
import json
import os
import tempfile
from collections.abc import Callable, Mapping
from contextvars import ContextVar
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any, Protocol, Self, cast, runtime_checkable

import pyarrow as pa
import pyarrow.parquet as pq

from trademaster.contracts import DatasetRequest, _require_utc

from .config import DataConfig, DataPaths
from .coverage import ProviderPage
from .registry import DatasetRegistry


class TushareCredentialError(RuntimeError):
    pass


class TushareDataError(RuntimeError):
    pass


_RAW_EVIDENCE_SHA256S: ContextVar[list[str] | None] = ContextVar(
    "trademaster_raw_evidence_sha256s", default=None
)


@runtime_checkable
class TushareClient(Protocol):
    def query(self, api_name: str, *, fields: str, **params: str) -> Any: ...


def _default_client_factory(token: str) -> TushareClient:
    tushare: Any = importlib.import_module("tushare")
    return cast(TushareClient, tushare.pro_api(token))


def _records(value: Any) -> list[dict[str, object]]:
    if isinstance(value, list) and all(isinstance(row, dict) for row in value):
        return cast(list[dict[str, object]], value)
    to_dict = getattr(value, "to_dict", None)
    if callable(to_dict):
        result = to_dict(orient="records")
        if isinstance(result, list) and all(isinstance(row, dict) for row in result):
            return cast(list[dict[str, object]], result)
    raise TushareDataError("Tushare response is not a tabular record set")


def _raw_revision(row: dict[str, object]) -> str:
    encoded = json.dumps(
        row, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str
    ).encode()
    return hashlib.sha256(encoded).hexdigest()


def _atomic_evidence_write(table: pa.Table, directory: Path) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix="evidence-", suffix=".parquet.tmp", dir=directory
    )
    os.close(descriptor)
    temporary = Path(temporary_name)
    try:
        pq.write_table(table, temporary, compression="zstd", version="2.6")
        payload = temporary.read_bytes()
        digest = hashlib.sha256(payload).hexdigest()
        destination = directory / f"{digest}.parquet"
        with temporary.open("rb") as stream:
            os.fsync(stream.fileno())
        try:
            os.link(temporary, destination)
        except FileExistsError:
            if hashlib.sha256(destination.read_bytes()).hexdigest() != digest:
                raise TushareDataError("evidence content-address collision")
        directory_descriptor = os.open(directory, os.O_RDONLY)
        try:
            os.fsync(directory_descriptor)
        finally:
            os.close(directory_descriptor)
        return destination
    finally:
        temporary.unlink(missing_ok=True)


def _date_from_key(value: str) -> tuple[str, date]:
    try:
        venue, raw_date = value.split(":", 1)
        parsed = date.fromisoformat(raw_date)
    except ValueError as error:
        raise TushareDataError("session coverage key must be VENUE:YYYY-MM-DD") from error
    return venue, parsed


def _compact_date(value: object) -> date:
    raw = str(value)
    if len(raw) != 8 or not raw.isdigit():
        raise TushareDataError("Tushare date must use YYYYMMDD")
    return date(int(raw[:4]), int(raw[4:6]), int(raw[6:8]))


def _venue_for_instrument(instrument_id: str) -> str:
    suffix = instrument_id.rsplit(".", 1)[-1]
    venues = {"SH": "SSE", "SZ": "SZSE", "BJ": "BSE"}
    try:
        return venues[suffix]
    except KeyError as error:
        raise TushareDataError(f"unsupported Tushare instrument suffix: {suffix}") from error


class TushareProvider:
    def __init__(
        self,
        *,
        client: TushareClient,
        paths: DataPaths,
        registry: DatasetRegistry,
        clock: Callable[[], datetime],
    ) -> None:
        self.client = client
        self.paths = paths
        self.registry = registry
        self.clock = clock
        self.etf_instruments = frozenset(paths.etf_instruments)

    def _canonical_table(
        self, dataset: str, rows: list[dict[str, object]]
    ) -> pa.Table:
        return pa.Table.from_pylist(rows, schema=self.registry[dataset].arrow_schema)

    @classmethod
    def from_environment(
        cls,
        *,
        config: DataConfig,
        registry: DatasetRegistry,
        clock: Callable[[], datetime],
        environment: Mapping[str, str] | None = None,
        client_factory: Callable[[str], TushareClient] = _default_client_factory,
    ) -> Self:
        env = os.environ if environment is None else environment
        token = env.get(config.tushare_token_env)
        if not token:
            raise TushareCredentialError(
                f"required Tushare credential is absent: {config.tushare_token_env}"
            )
        return cls(
            client=client_factory(token),
            paths=config.paths,
            registry=registry,
            clock=clock,
        )

    def fetch(
        self,
        request: DatasetRequest,
        *,
        coverage_keys: tuple[str, ...],
        page_token: str | None,
    ) -> ProviderPage:
        self.registry[request.dataset]
        if any(
            instrument_id in self.etf_instruments
            for instrument_id in request.instruments
        ) and request.dataset not in {"instrument_master", "daily_bars"}:
            raise TushareDataError(
                f"ETF provider contract is not implemented for {request.dataset}"
            )
        try:
            index = 0 if page_token is None else int(page_token)
            coverage_key = coverage_keys[index]
        except (ValueError, IndexError) as error:
            raise TushareDataError("invalid Tushare page token") from error
        ingested_at = self.clock()
        _require_utc(ingested_at)
        raw_evidence_sha256s: list[str] = []
        evidence_token = _RAW_EVIDENCE_SHA256S.set(raw_evidence_sha256s)
        try:
            if request.dataset == "trade_calendar":
                table, partition = self._fetch_trade_calendar(coverage_key, ingested_at)
            elif request.dataset == "daily_bars":
                table, partition = self._fetch_daily_bars(
                    request, coverage_key, ingested_at
                )
            elif request.dataset in {"adj_factors", "daily_basic", "index_bars"}:
                table, partition = self._fetch_simple_session_dataset(
                    request, coverage_key, ingested_at
                )
            elif request.dataset == "daily_limits_status":
                table, partition = self._fetch_daily_limits_status(
                    request, coverage_key, ingested_at
                )
            elif request.dataset == "instrument_master":
                table, partition = self._fetch_instrument_master(
                    coverage_key, ingested_at
                )
            elif request.dataset == "index_membership":
                table, partition = self._fetch_index_membership(
                    coverage_key, ingested_at
                )
            elif request.dataset == "industry_membership":
                table, partition = self._fetch_industry_membership(
                    request, coverage_key, ingested_at
                )
            elif request.dataset == "financial_indicators":
                table, partition = self._fetch_financial_indicators(
                    coverage_key, ingested_at
                )
            else:
                raise TushareDataError(
                    f"Tushare normalizer is not implemented for {request.dataset}"
                )
            staging_path = self._persist_staging(
                request,
                coverage_key,
                partition,
                table,
                ingested_at,
                raw_object_sha256s=tuple(sorted(raw_evidence_sha256s)),
            )
        finally:
            _RAW_EVIDENCE_SHA256S.reset(evidence_token)
        next_token = str(index + 1) if index + 1 < len(coverage_keys) else None
        return ProviderPage(
            table=table,
            partition_key=partition,
            coverage_keys=(coverage_key,),
            next_page_token=next_token,
            upstream_object_sha256s=tuple(
                sorted({*raw_evidence_sha256s, staging_path.stem})
            ),
        )

    def _query_raw(
        self,
        *,
        endpoint: str,
        fields: str,
        params: dict[str, str],
        ingested_at: datetime,
        allow_empty: bool = False,
        page_limit: int | None = None,
    ) -> list[dict[str, object]]:
        request_payload = {"endpoint": endpoint, "fields": fields, "params": params}
        request_sha256 = hashlib.sha256(
            json.dumps(
                request_payload,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode()
        ).hexdigest()
        rows: list[dict[str, object]] = []
        seen_pages: set[str] = set()
        offset = 0
        page_index = 0
        mutable_cached_observations: dict[int, tuple[str, datetime]] = {}
        cached_pages: dict[
            int, tuple[int, list[dict[str, object]], str, str, datetime]
        ] = {}
        for path in sorted((self.paths.raw / endpoint).glob("ingest_date=*/*.parquet")):
            if hashlib.sha256(path.read_bytes()).hexdigest() != path.stem:
                raise TushareDataError("cached raw evidence content hash mismatch")
            cached_table = pq.read_table(path)
            raw_metadata = (cached_table.schema.metadata or {}).get(
                b"trademaster.provider_request/v1"
            )
            if raw_metadata is None:
                continue
            try:
                cached_meta = json.loads(raw_metadata)
            except (TypeError, ValueError) as error:
                raise TushareDataError("cached raw evidence metadata is invalid") from error
            if cached_meta.get("provider_request_sha256") != request_sha256:
                continue
            try:
                cached_ingested_at = datetime.fromisoformat(
                    str(cached_meta["ingested_at"])
                )
                _require_utc(cached_ingested_at)
            except (KeyError, TypeError, ValueError) as error:
                raise TushareDataError("cached raw ingestion time is invalid") from error
            if cached_ingested_at > ingested_at:
                continue
            if (
                cached_meta.get("endpoint") != endpoint
                or cached_meta.get("fields") != fields
                or cached_meta.get("params") != params
            ):
                raise TushareDataError("cached raw evidence request binding mismatch")
            try:
                cached_index = int(cached_meta["page_index"])
                cached_offset = int(cached_meta["page_cursor"])
                returned_rows = int(cached_meta["returned_rows"])
            except (KeyError, TypeError, ValueError) as error:
                raise TushareDataError("cached raw page cursor is invalid") from error
            cached_rows = (
                []
                if cached_table.column_names == ["_empty"]
                else cast(list[dict[str, object]], cached_table.to_pylist())
            )
            if returned_rows != len(cached_rows) or cached_meta.get(
                "row_source_revisions"
            ) != sorted(_raw_revision(row) for row in cached_rows):
                raise TushareDataError("cached raw page content proof is invalid")
            fingerprint = hashlib.sha256(
                json.dumps(
                    cached_rows,
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                    default=str,
                ).encode()
            ).hexdigest()
            existing = cached_pages.get(cached_index)
            candidate = (
                cached_offset,
                cached_rows,
                fingerprint,
                path.stem,
                cached_ingested_at,
            )
            if existing is not None:
                if existing[:3] != candidate[:3] and not allow_empty:
                    raise TushareDataError("conflicting cached raw provider pages")
                if existing[4] == candidate[4] and existing[:3] != candidate[:3]:
                    raise TushareDataError(
                        "cached mutable raw pages have an ambiguous revision"
                    )
                if existing[4] > candidate[4]:
                    continue
            cached_pages[cached_index] = candidate
        if allow_empty:
            mutable_cached_observations = {
                index: (candidate[2], candidate[4])
                for index, candidate in cached_pages.items()
            }
        while not allow_empty and page_index in cached_pages:
            cached_offset, cached_rows, fingerprint, raw_sha256, _ = cached_pages[
                page_index
            ]
            if cached_offset != offset or fingerprint in seen_pages:
                raise TushareDataError("cached raw page sequence is invalid")
            seen_pages.add(fingerprint)
            rows.extend(cached_rows)
            evidence = _RAW_EVIDENCE_SHA256S.get()
            if evidence is not None:
                evidence.append(raw_sha256)
            if page_limit is None or len(cached_rows) < page_limit:
                if not rows and not allow_empty:
                    raise TushareDataError(f"Tushare {endpoint} returned an empty response")
                return rows
            offset += page_limit
            page_index += 1
        while True:
            call_params = dict(params)
            if page_limit is not None:
                call_params.update(limit=str(page_limit), offset=str(offset))
            try:
                page = _records(self.client.query(endpoint, fields=fields, **call_params))
            except TushareDataError:
                raise
            except Exception as error:
                raise TushareDataError(f"Tushare {endpoint} request failed") from error
            if not page and page_index == 0 and not allow_empty:
                raise TushareDataError(f"Tushare {endpoint} returned an empty response")
            page_fingerprint = hashlib.sha256(
                json.dumps(
                    page,
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                    default=str,
                ).encode()
            ).hexdigest()
            mutable_cached = mutable_cached_observations.get(page_index)
            if (
                mutable_cached is not None
                and mutable_cached[1] == ingested_at
                and mutable_cached[0] != page_fingerprint
            ):
                raise TushareDataError(
                    "mutable provider page has an ambiguous revision timestamp"
                )
            if page and page_fingerprint in seen_pages:
                raise TushareDataError(f"Tushare {endpoint} repeated a provider page")
            seen_pages.add(page_fingerprint)
            raw = (
                pa.Table.from_pylist(page)
                if page
                else pa.table({"_empty": pa.array([], pa.bool_())})
            )
            metadata = dict(raw.schema.metadata or {})
            metadata[b"trademaster.provider_request/v1"] = json.dumps(
                {
                    "endpoint": endpoint,
                    "fields": fields,
                    "params": params,
                    "ingested_at": ingested_at.isoformat(),
                    "provider_request_sha256": request_sha256,
                    "page_cursor": str(offset),
                    "page_index": page_index,
                    "returned_rows": len(page),
                    "row_source_revisions": sorted(_raw_revision(row) for row in page),
                },
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode()
            raw_path = _atomic_evidence_write(
                raw.replace_schema_metadata(metadata),
                self.paths.raw
                / endpoint
                / f"ingest_date={ingested_at.date().isoformat()}",
            )
            evidence = _RAW_EVIDENCE_SHA256S.get()
            if evidence is not None:
                evidence.append(raw_path.stem)
            rows.extend(page)
            if page_limit is None or len(page) < page_limit:
                break
            offset += page_limit
            page_index += 1
        return rows

    def _fetch_trade_calendar(
        self, coverage_key: str, ingested_at: datetime
    ) -> tuple[pa.Table, str]:
        venue, session_date = _date_from_key(coverage_key)
        raw_date = session_date.strftime("%Y%m%d")
        rows = self._query_raw(
            endpoint="trade_cal",
            fields="exchange,cal_date,is_open,pretrade_date",
            params={"exchange": venue, "start_date": raw_date, "end_date": raw_date},
            ingested_at=ingested_at,
            page_limit=6000,
        )
        matches = [
            row
            for row in rows
            if str(row.get("exchange")) == venue
            and str(row.get("cal_date")) == raw_date
        ]
        if len(matches) != 1:
            raise TushareDataError("trade_cal response does not uniquely match coverage key")
        row = matches[0]
        normalized = [
            {
                "venue": venue,
                "session_date": session_date,
                "is_open": bool(int(cast(Any, row["is_open"]))),
                "open_at": datetime(
                    session_date.year,
                    session_date.month,
                    session_date.day,
                    1,
                    30,
                    tzinfo=UTC,
                ),
                "close_at": datetime(
                    session_date.year,
                    session_date.month,
                    session_date.day,
                    7,
                    tzinfo=UTC,
                ),
                "event_time": datetime(
                    session_date.year,
                    session_date.month,
                    session_date.day,
                    7,
                    tzinfo=UTC,
                ),
                "known_at": ingested_at,
                "source_revision": _raw_revision(row),
            }
        ]
        return (
            self._canonical_table("trade_calendar", normalized),
            f"venue={venue}/session_year={session_date.year}",
        )

    def _fetch_daily_bars(
        self,
        request: DatasetRequest,
        coverage_key: str,
        ingested_at: datetime,
    ) -> tuple[pa.Table, str]:
        venue, trade_date = _date_from_key(coverage_key)
        raw_date = trade_date.strftime("%Y%m%d")
        fields = "ts_code,trade_date,open,high,low,close,vol,amount,pre_close"
        requested_etfs = tuple(
            instrument_id
            for instrument_id in request.instruments
            if instrument_id in self.etf_instruments
        )
        requested_stocks = tuple(
            instrument_id
            for instrument_id in request.instruments
            if instrument_id not in self.etf_instruments
        )
        rows: list[dict[str, object]] = []
        if requested_stocks or not request.instruments:
            rows.extend(
                self._query_raw(
                    endpoint="daily",
                    fields=fields,
                    params={"trade_date": raw_date},
                    ingested_at=ingested_at,
                    allow_empty=bool(request.instruments),
                    page_limit=6000,
                )
            )
        for instrument_id in requested_etfs:
            rows.extend(
                self._query_raw(
                    endpoint="fund_daily",
                    fields=fields,
                    params={"ts_code": instrument_id, "trade_date": raw_date},
                    ingested_at=ingested_at,
                    allow_empty=True,
                    page_limit=800,
                )
            )
        normalized: list[dict[str, object]] = []
        for row in rows:
            instrument_id = str(row.get("ts_code"))
            if str(row.get("trade_date")) != raw_date:
                continue
            if _venue_for_instrument(instrument_id) != venue:
                continue
            if request.instruments and instrument_id not in request.instruments:
                continue
            normalized.append(
                {
                    "instrument_id": instrument_id,
                    "trade_date": trade_date,
                    "open": float(cast(Any, row["open"])),
                    "high": float(cast(Any, row["high"])),
                    "low": float(cast(Any, row["low"])),
                    "close": float(cast(Any, row["close"])),
                    "volume": float(cast(Any, row["vol"])),
                    "amount": float(cast(Any, row["amount"])),
                    "pre_close": float(cast(Any, row["pre_close"])),
                    "venue": venue,
                    "event_time": datetime(
                        trade_date.year,
                        trade_date.month,
                        trade_date.day,
                        7,
                        tzinfo=UTC,
                    ),
                    "known_at": datetime(
                        trade_date.year,
                        trade_date.month,
                        trade_date.day,
                        8,
                        tzinfo=UTC,
                    ),
                    "source_revision": _raw_revision(row),
                }
            )
        if not normalized and not request.instruments:
            raise TushareDataError("daily response does not cover requested venue/universe")
        return (
            self._canonical_table("daily_bars", normalized),
            f"trade_year={trade_date.year}/trade_month={trade_date.month:02d}",
        )

    def _fetch_simple_session_dataset(
        self,
        request: DatasetRequest,
        coverage_key: str,
        ingested_at: datetime,
    ) -> tuple[pa.Table, str]:
        venue, trade_date = _date_from_key(coverage_key)
        raw_date = trade_date.strftime("%Y%m%d")
        definitions = {
            "adj_factors": (
                "adj_factor",
                "ts_code,trade_date,adj_factor",
            ),
            "daily_basic": (
                "daily_basic",
                "ts_code,trade_date,turnover_rate,total_mv,circ_mv",
            ),
            "index_bars": (
                "index_daily",
                "ts_code,trade_date,open,high,low,close,vol,amount",
            ),
        }
        endpoint, fields = definitions[request.dataset]
        rows: list[dict[str, object]] = []
        if request.dataset == "index_bars":
            if not request.instruments:
                raise TushareDataError("index_daily requires a resolved index universe")
            for instrument_id in request.instruments:
                rows.extend(
                    self._query_raw(
                        endpoint=endpoint,
                        fields=fields,
                        params={"ts_code": instrument_id, "trade_date": raw_date},
                        ingested_at=ingested_at,
                        page_limit=8000,
                    )
                )
        else:
            rows = self._query_raw(
                endpoint=endpoint,
                fields=fields,
                params={"trade_date": raw_date},
                ingested_at=ingested_at,
                page_limit=6000,
            )
        normalized: list[dict[str, object]] = []
        for row in rows:
            instrument_id = str(row.get("ts_code"))
            if str(row.get("trade_date")) != raw_date:
                continue
            if _venue_for_instrument(instrument_id) != venue:
                continue
            if request.instruments and instrument_id not in request.instruments:
                continue
            event_hour = 1 if request.dataset == "adj_factors" else 7
            event_minute = 20 if request.dataset == "adj_factors" else 0
            known_hour = {
                "adj_factors": 1,
                "daily_basic": 12,
                "index_bars": 8,
            }[request.dataset]
            known_minute = 20 if request.dataset == "adj_factors" else 0
            common: dict[str, object] = {
                "instrument_id": instrument_id,
                "trade_date": trade_date,
                "venue": venue,
                "event_time": datetime(
                    trade_date.year,
                    trade_date.month,
                    trade_date.day,
                    event_hour,
                    event_minute,
                    tzinfo=UTC,
                ),
                "known_at": datetime(
                    trade_date.year,
                    trade_date.month,
                    trade_date.day,
                    known_hour,
                    known_minute,
                    tzinfo=UTC,
                ),
                "source_revision": _raw_revision(row),
            }
            if request.dataset == "adj_factors":
                common["adj_factor"] = float(cast(Any, row["adj_factor"]))
            elif request.dataset == "daily_basic":
                common.update(
                    float_market_value=float(cast(Any, row["circ_mv"])),
                    total_market_value=float(cast(Any, row["total_mv"])),
                    turnover_rate=float(cast(Any, row["turnover_rate"])),
                )
            else:
                common.update(
                    open=float(cast(Any, row["open"])),
                    high=float(cast(Any, row["high"])),
                    low=float(cast(Any, row["low"])),
                    close=float(cast(Any, row["close"])),
                    volume=float(cast(Any, row["vol"])),
                    amount=float(cast(Any, row["amount"])),
                )
            normalized.append(common)
        if not normalized:
            raise TushareDataError(f"{endpoint} does not cover requested venue/universe")
        return (
            self._canonical_table(request.dataset, normalized),
            f"trade_year={trade_date.year}/trade_month={trade_date.month:02d}",
        )

    def _fetch_daily_limits_status(
        self,
        request: DatasetRequest,
        coverage_key: str,
        ingested_at: datetime,
    ) -> tuple[pa.Table, str]:
        venue, trade_date = _date_from_key(coverage_key)
        raw_date = trade_date.strftime("%Y%m%d")
        limits = self._query_raw(
            endpoint="stk_limit",
            fields="ts_code,trade_date,up_limit,down_limit",
            params={"trade_date": raw_date},
            ingested_at=ingested_at,
            page_limit=5800,
        )
        suspensions = self._query_raw(
            endpoint="suspend_d",
            fields="ts_code,trade_date,suspend_type",
            params={"trade_date": raw_date, "suspend_type": "S"},
            ingested_at=ingested_at,
            allow_empty=True,
            page_limit=5000,
        )
        st_rows = self._query_raw(
            endpoint="stock_st",
            fields="ts_code,trade_date,type",
            params={"trade_date": raw_date},
            ingested_at=ingested_at,
            allow_empty=True,
            page_limit=1000,
        )
        suspended = {
            str(row.get("ts_code"))
            for row in suspensions
            if str(row.get("suspend_type")) == "S"
            and str(row.get("trade_date")) == raw_date
        }
        st_instruments = {
            str(row.get("ts_code"))
            for row in st_rows
            if str(row.get("trade_date")) == raw_date
        }
        normalized: list[dict[str, object]] = []
        for row in limits:
            instrument_id = str(row.get("ts_code"))
            if str(row.get("trade_date")) != raw_date:
                continue
            if _venue_for_instrument(instrument_id) != venue:
                continue
            if request.instruments and instrument_id not in request.instruments:
                continue
            status_payload = dict(row)
            status_payload.update(
                suspended=instrument_id in suspended,
                is_st=instrument_id in st_instruments,
            )
            normalized.append(
                {
                    "instrument_id": instrument_id,
                    "trade_date": trade_date,
                    "up_limit": float(cast(Any, row["up_limit"])),
                    "down_limit": float(cast(Any, row["down_limit"])),
                    "suspended": instrument_id in suspended,
                    "is_st": instrument_id in st_instruments,
                    "venue": venue,
                    "event_time": datetime(
                        trade_date.year,
                        trade_date.month,
                        trade_date.day,
                        1,
                        20,
                        tzinfo=UTC,
                    ),
                    "known_at": datetime(
                        trade_date.year,
                        trade_date.month,
                        trade_date.day,
                        1,
                        20,
                        tzinfo=UTC,
                    ),
                    "source_revision": _raw_revision(status_payload),
                }
            )
        if not normalized:
            raise TushareDataError("daily limit/status inputs do not cover requested universe")
        return (
            self._canonical_table("daily_limits_status", normalized),
            f"trade_year={trade_date.year}/trade_month={trade_date.month:02d}",
        )

    def _fetch_instrument_master(
        self, coverage_key: str, ingested_at: datetime
    ) -> tuple[pa.Table, str]:
        try:
            instrument_id, raw_effective = coverage_key.rsplit(":", 1)
            effective_from = date.fromisoformat(raw_effective)
        except ValueError as error:
            raise TushareDataError("invalid instrument-master coverage key") from error
        if instrument_id in self.etf_instruments:
            etf_rows = self._query_raw(
                endpoint="etf_basic",
                fields="ts_code,exchange,list_date,list_status",
                params={"ts_code": instrument_id},
                ingested_at=ingested_at,
                page_limit=2000,
            )
            matches = [
                row
                for row in etf_rows
                if str(row.get("ts_code")) == instrument_id
                and str(row.get("list_date")) == effective_from.strftime("%Y%m%d")
            ]
            if len(matches) != 1:
                raise TushareDataError("etf_basic does not uniquely match listing event")
            row = matches[0]
            raw_venue = str(row.get("exchange"))
            venue_by_provider_code = {"SH": "SSE", "SZ": "SZSE"}
            venue = venue_by_provider_code.get(raw_venue)
            if venue is None or venue != _venue_for_instrument(instrument_id):
                raise TushareDataError(
                    "ETF exchange is unknown or conflicts with the instrument suffix"
                )
            if str(row.get("list_status")) != "L":
                raise TushareDataError(
                    "ETF lifecycle cannot be reconstructed from a non-listed etf_basic row"
                )
            event_time = datetime(
                effective_from.year,
                effective_from.month,
                effective_from.day,
                tzinfo=UTC,
            )
            table = self._canonical_table(
                "instrument_master",
                [
                    {
                        "instrument_id": instrument_id,
                        "effective_from": effective_from,
                        "venue": venue,
                        "asset_class": "etf",
                        "list_date": effective_from,
                        "delist_date": date(9999, 12, 31),
                        "event_time": event_time,
                        "known_at": event_time,
                        "source_revision": _raw_revision(row),
                    }
                ],
            )
            return table, "dataset_scope=all"
        rows: list[dict[str, object]] = []
        for list_status in ("L", "D", "P", "G"):
            rows.extend(
                self._query_raw(
                    endpoint="stock_basic",
                    fields="ts_code,exchange,market,curr_type,list_date,delist_date",
                    params={"ts_code": instrument_id, "list_status": list_status},
                    ingested_at=ingested_at,
                    allow_empty=True,
                    page_limit=6000,
                )
            )
        rows = list(
            {
                json.dumps(row, sort_keys=True, default=str): row for row in rows
            }.values()
        )
        matches = [
            row
            for row in rows
            if str(row.get("ts_code")) == instrument_id
            and (
                str(row.get("list_date")) == effective_from.strftime("%Y%m%d")
                or str(row.get("delist_date")) == effective_from.strftime("%Y%m%d")
            )
        ]
        if len(matches) != 1:
            raise TushareDataError("stock_basic does not uniquely match coverage key")
        row = matches[0]
        venue = str(row.get("exchange")) or _venue_for_instrument(instrument_id)
        delist_raw = row.get("delist_date")
        announced_delist = (
            _compact_date(delist_raw) if delist_raw not in (None, "") else None
        )
        list_date = _compact_date(row["list_date"])
        if effective_from == list_date:
            delist_date = date(9999, 12, 31)
            revision_payload = {
                key: value for key, value in row.items() if key != "delist_date"
            }
        elif announced_delist is not None and effective_from == announced_delist:
            delist_date = announced_delist
            revision_payload = row
        else:
            raise TushareDataError("coverage key is not a known instrument effective event")
        event_time = datetime(
            effective_from.year, effective_from.month, effective_from.day, tzinfo=UTC
        )
        normalized = self._canonical_table(
            "instrument_master",
            [
                {
                    "instrument_id": instrument_id,
                    "effective_from": effective_from,
                    "venue": venue,
                    "asset_class": "stock",
                    "list_date": list_date,
                    "delist_date": delist_date,
                    "event_time": event_time,
                    "known_at": event_time,
                    "source_revision": _raw_revision(revision_payload),
                }
            ],
        )
        return normalized, "dataset_scope=all"

    def _fetch_index_membership(
        self, coverage_key: str, ingested_at: datetime
    ) -> tuple[pa.Table, str]:
        try:
            index_id, raw_effective = coverage_key.rsplit(":", 1)
            effective_from = date.fromisoformat(raw_effective)
        except ValueError as error:
            raise TushareDataError("invalid index-membership coverage key") from error
        raw_date = effective_from.strftime("%Y%m%d")
        rows = self._query_raw(
            endpoint="index_weight",
            fields="index_code,con_code,trade_date,weight",
            params={"index_code": index_id, "trade_date": raw_date},
            ingested_at=ingested_at,
            page_limit=1000,
        )
        normalized = [
            {
                "index_id": index_id,
                "instrument_id": str(row.get("con_code")),
                "effective_from": effective_from,
                # index_weight is a dated composition snapshot. V1 does not infer
                # an unannounced removal date from a later observation.
                "effective_to": effective_from,
                "weight": float(cast(Any, row["weight"])),
                "event_time": datetime(
                    effective_from.year,
                    effective_from.month,
                    effective_from.day,
                    tzinfo=UTC,
                ),
                "known_at": ingested_at,
                "source_revision": _raw_revision(row),
            }
            for row in rows
            if str(row.get("index_code")) == index_id
            and str(row.get("trade_date")) == raw_date
        ]
        if not normalized:
            raise TushareDataError("index_weight does not cover requested index/date")
        return self._canonical_table("index_membership", normalized), (
            f"index_id={index_id}/effective_year={effective_from.year}"
        )

    def _fetch_industry_membership(
        self,
        request: DatasetRequest,
        coverage_key: str,
        ingested_at: datetime,
    ) -> tuple[pa.Table, str]:
        try:
            taxonomy, raw_effective = coverage_key.rsplit(":", 1)
            effective_from = date.fromisoformat(raw_effective)
        except ValueError as error:
            raise TushareDataError("invalid industry-membership coverage key") from error
        if taxonomy != "SW2021":
            raise TushareDataError("unsupported industry taxonomy")
        if not request.instruments:
            raise TushareDataError("industry membership requires a resolved universe")
        rows: list[dict[str, object]] = []
        for instrument_id in request.instruments:
            rows.extend(
                self._query_raw(
                    endpoint="index_member_all",
                    fields="l1_code,l2_code,l3_code,ts_code,in_date,out_date",
                    params={"ts_code": instrument_id},
                    ingested_at=ingested_at,
                    page_limit=2000,
                )
            )
        raw_effective_date = effective_from.strftime("%Y%m%d")
        normalized = [
            {
                "taxonomy": taxonomy,
                "instrument_id": str(row.get("ts_code")),
                "effective_from": effective_from,
                "industry_id": str(row.get("l3_code")),
                "effective_to": (
                    _compact_date(row["out_date"])
                    if row.get("out_date") not in (None, "")
                    else date(9999, 12, 31)
                ),
                "event_time": datetime(
                    effective_from.year,
                    effective_from.month,
                    effective_from.day,
                    tzinfo=UTC,
                ),
                "known_at": ingested_at,
                "source_revision": _raw_revision(row),
            }
            for row in rows
            if str(row.get("ts_code")) in request.instruments
            and str(row.get("in_date")) == raw_effective_date
            and row.get("l3_code") not in (None, "")
        ]
        if not normalized:
            raise TushareDataError(
                "index_member_all does not cover the requested effective date"
            )
        return self._canonical_table("industry_membership", normalized), (
            f"taxonomy={taxonomy}/effective_year={effective_from.year}"
        )

    def _fetch_financial_indicators(
        self, coverage_key: str, ingested_at: datetime
    ) -> tuple[pa.Table, str]:
        try:
            instrument_id, raw_period, raw_announcement = coverage_key.split(":")
            report_period = date.fromisoformat(raw_period)
            announcement_date = date.fromisoformat(raw_announcement)
        except ValueError as error:
            raise TushareDataError("invalid financial-indicator coverage key") from error
        rows = self._query_raw(
            endpoint="fina_indicator",
            fields="",
            params={
                "ts_code": instrument_id,
                "period": report_period.strftime("%Y%m%d"),
            },
            ingested_at=ingested_at,
            page_limit=100,
        )
        raw_period_compact = report_period.strftime("%Y%m%d")
        raw_announcement_compact = announcement_date.strftime("%Y%m%d")
        matches = [
            row
            for row in rows
            if str(row.get("ts_code")) == instrument_id
            and str(row.get("end_date")) == raw_period_compact
            and str(row.get("ann_date")) == raw_announcement_compact
        ]
        if not matches:
            raise TushareDataError("fina_indicator does not cover requested report revision")
        event_time = datetime(
            report_period.year,
            report_period.month,
            report_period.day,
            23,
            59,
            tzinfo=UTC,
        )
        known_at = datetime(
            announcement_date.year,
            announcement_date.month,
            announcement_date.day,
            16,
            tzinfo=UTC,
        )
        normalized = []
        for row in matches:
            values = {
                key: value
                for key, value in row.items()
                if key not in {"ts_code", "end_date", "ann_date", "update_flag"}
            }
            revision = str(row.get("update_flag") or _raw_revision(row))
            normalized.append(
                {
                    "instrument_id": instrument_id,
                    "report_period": report_period,
                    "announcement_id": announcement_date.isoformat(),
                    "revision": revision,
                    "report_values_json": json.dumps(
                        values,
                        ensure_ascii=False,
                        sort_keys=True,
                        separators=(",", ":"),
                        default=str,
                    ),
                    "event_time": event_time,
                    "known_at": known_at,
                    "source_revision": _raw_revision(row),
                }
            )
        return (
            self._canonical_table("financial_indicators", normalized),
            "dataset_scope=all",
        )

    def _persist_staging(
        self,
        request: DatasetRequest,
        coverage_key: str,
        partition: str,
        table: pa.Table,
        ingested_at: datetime,
        *,
        raw_object_sha256s: tuple[str, ...],
    ) -> Path:
        partial_request = request.model_copy(
            update={"coverage_keys": (coverage_key,)}
        )
        request_sha256 = hashlib.sha256(
            json.dumps(
                partial_request.model_dump(mode="json"),
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode()
        ).hexdigest()
        source_revisions = sorted(
            cast(list[str], table["source_revision"].to_pylist())
        )
        metadata = dict(table.schema.metadata or {})
        metadata[b"trademaster.staging/v1"] = json.dumps(
            {
                "dataset": request.dataset,
                "normalized_at": ingested_at.isoformat(),
                "request_sha256": request_sha256,
                "coverage_key": coverage_key,
                "raw_object_sha256s": raw_object_sha256s,
                "source_revision_sha256s": source_revisions,
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
        return _atomic_evidence_write(
            table.replace_schema_metadata(metadata),
            self.paths.staging / request.dataset / partition,
        )
