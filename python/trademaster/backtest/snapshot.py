"""Read and publish the daily market and point-in-time research inputs for a backtest."""

from __future__ import annotations

import hashlib
import itertools
import json
import math
import os
from bisect import bisect_left, bisect_right
from collections import defaultdict
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, time, timedelta, timezone
from decimal import Decimal
from functools import cached_property
from pathlib import Path
from tempfile import NamedTemporaryFile
from types import MappingProxyType
from typing import TYPE_CHECKING, Any, Literal, cast

import numpy as np
import numpy.typing as npt
import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.parquet as pq

if TYPE_CHECKING:
    from trademaster._native import Market
    from trademaster.e2e.demo_data import DemoMarket

type Amount = Decimal | int | float
type Permission = Literal["yes", "no", "unknown"]
_SCALE = 100_000_000
_ADJUSTED = "__adjusted_close__"
_FEATURE_FIELDS: list[tuple[str, pa.DataType]] = [
    ("instrument_id", pa.string()),
    ("name", pa.string()),
    ("version", pa.string()),
    ("available_at", pa.timestamp("us", tz="UTC")),
    ("value", pa.float64()),
]
_FEATURES = pa.schema(_FEATURE_FIELDS)


def scaled(value: Amount, label: str) -> int:
    number = Decimal(str(value))
    if not number.is_finite() or number < 0:
        raise ValueError(f"{label} must be finite and nonnegative")
    if number > Decimal("92233720368.54775807"):
        raise ValueError(f"{label} exceeds the maximum fixed-8 amount")
    numerator, denominator = number.as_integer_ratio()
    units, remainder = divmod(numerator * _SCALE, denominator)
    if remainder:
        raise ValueError(f"{label} exceeds eight decimal places of precision")
    return units


def canonical(value: object) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode("utf-8")


def _publish(path: Path, payload: bytes) -> None:
    # Publish a complete file without overwriting an existing snapshot or racing another writer.
    with NamedTemporaryFile(dir=path.parent, delete=False) as stream:
        temporary = Path(stream.name)
        try:
            stream.write(payload)
            stream.flush()
            try:
                os.link(temporary, path)
            except FileExistsError:
                if path.read_bytes() != payload:
                    raise ValueError(f"snapshot already exists with different contents: {path}")
        finally:
            temporary.unlink(missing_ok=True)


def _object(root: Path, label: str, extension: str, payload: bytes) -> dict[str, str]:
    digest = hashlib.sha256(payload).hexdigest()
    name = f"{label}-{digest}.{extension}"
    _publish(root / name, payload)
    return {"file": name, "sha256": digest}


def _verified(root: Path, descriptor: dict[str, str]) -> tuple[Path, bytes]:
    path = (root / descriptor["file"]).resolve()
    if path.parent != root:
        raise ValueError("snapshot object must be inside the snapshot directory")
    payload = path.read_bytes()
    if hashlib.sha256(payload).hexdigest() != descriptor["sha256"]:
        raise ValueError(f"snapshot object hash mismatch: {path.name}")
    return path, payload


def _adjusted_grid(
    table: pa.Table,
    days: tuple[date, ...],
    symbols: tuple[str, ...],
    close_times: Mapping[date, datetime],
    lo: npt.NDArray[np.int64],
    hi: npt.NDArray[np.int64],
) -> npt.NDArray[np.float64]:
    """Scatter adjusted-close features into an instrument-major grid (NaN = missing)."""
    grid = np.full((len(symbols), len(days)), np.nan)
    if not table.num_rows:
        return grid
    # Rows are visited in publication order so the first invalid row matches a sequential scan.
    table = table.sort_by([("available_at", "ascending")])
    stamps = pa.array(
        [close_times[d] for d in days], pa.timestamp("us", tz="UTC")
    )  # positions follow `days`
    day_index = pc.index_in(table["available_at"], value_set=stamps)
    if day_index.null_count:
        first = pc.index(pc.is_null(day_index), True).as_py()
        raise KeyError(table["available_at"][first].as_py())
    symbol_index = pc.index_in(table["instrument_id"], value_set=pa.array(symbols, pa.string()))
    if symbol_index.null_count:
        first = pc.index(pc.is_null(symbol_index), True).as_py()
        raise KeyError(table["instrument_id"][first].as_py())
    rows = symbol_index.to_numpy()
    cols = day_index.to_numpy()
    outside = (cols < lo[rows]) | (cols >= hi[rows])
    if outside.any():
        first = int(np.argmax(outside))
        location = (days[cols[first]], symbols[rows[first]])
        raise ValueError(f"adjusted close outside listing interval: {location}")
    grid[rows, cols] = table["value"].to_numpy(zero_copy_only=False)
    return grid


def _history_tuple(values: npt.NDArray[np.float64]) -> tuple[float | None, ...]:
    return tuple(None if math.isnan(v) else v for v in values.tolist())


def _listed_range(
    days: tuple[date, ...], interval: tuple[date | None, date | None]
) -> tuple[int, int]:
    """Session index range [lo, hi) where `_listed` holds for the interval."""
    start, end = interval
    lo = 0 if start is None else bisect_left(days, start)
    hi = len(days) if end is None else bisect_left(days, end)
    return lo, hi


def _listed(session: date, interval: tuple[date | None, date | None]) -> bool:
    start, end = interval
    return (start is None or start <= session) and (end is None or session < end)


def _validate_clock(
    days: Sequence[date], closes: Mapping[date, datetime], cutoffs: Mapping[date, datetime]
) -> None:
    if set(cutoffs) != set(days) or set(closes) != set(days):
        raise ValueError("supply one close and one decision cutoff for every session")
    for day in days:
        close, cutoff = closes[day], cutoffs[day]
        if close.utcoffset() is None or close.date() != day:
            raise ValueError("the close must be timezone-aware and on its local session date")
        if (
            cutoff.utcoffset() is None
            or cutoff < close
            or cutoff.astimezone(close.tzinfo).date() != day
        ):
            raise ValueError(
                "the decision cutoff must be at or after the close on its local session date"
            )
    if any(cutoffs[a] >= closes[b] for a, b in itertools.pairwise(days)):
        raise ValueError("session closes and decision cutoffs must be in calendar order")


@dataclass(frozen=True)
class Instrument:
    instrument_id: str
    venue: str = "other"
    buy_lot_size: int = 100
    tick_size: Amount = Decimal("0.01")
    settlement: Literal["t0", "t1"] = "t1"
    asset_class: str = "stock"
    currency: str = "CNY"
    listed_on: date | None = None
    delisted_on: date | None = None  # First date outside the strategy universe.


@dataclass(frozen=True)
class DailyPrice:
    """Opening permissions; price-limit markers identify limits as the sole blocking cause."""

    session: date
    instrument_id: str
    open: Amount | None
    close: Amount | None
    buy_at_open: Permission = "yes"
    sell_at_open: Permission = "yes"
    buy_reason: str | None = None
    sell_reason: str | None = None
    evidence: str = ""
    suspended: bool = False
    corporate_action_gap: bool = False
    adjusted_close: float | None = None
    buy_blocked_by_price_limit: bool = False
    sell_blocked_by_price_limit: bool = False


@dataclass(frozen=True)
class CashDividend:
    """Cash paid per share on the payment date, in the instrument's currency."""

    instrument_id: str
    record_session: date
    ex_session: date
    pay_session: date
    amount_per_share: Amount


@dataclass(frozen=True)
class Feature:
    instrument_id: str
    name: str
    value: float | None
    available_at: datetime
    version: str = "1"


@dataclass(frozen=True)
class DecisionDay:
    """Only published observations through this session's close are accessible."""

    session: date
    decision_at: datetime
    instruments: tuple[str, ...]
    _history: Callable[[str, bool, int | None], tuple[float | None, ...]] = field(repr=False)
    _scores: Callable[[str], Mapping[str, float | None]] = field(repr=False)

    @cached_property
    def _universe(self) -> frozenset[str]:
        return frozenset(self.instruments)

    def closes(
        self, symbol: str, *, adjusted: bool = False, limit: int | None = None
    ) -> tuple[float | None, ...]:
        """Return closing prices through this decision, with missing sessions kept in the window."""
        if symbol not in self._universe:
            raise ValueError(f"instrument is outside the decision universe: {symbol}")
        if limit is not None and (type(limit) is not int or limit < 1):
            raise ValueError("history limit must be a positive integer")
        return self._history(symbol, adjusted, limit)

    def scores(self, name: str) -> Mapping[str, float | None]:
        return self._scores(name)


@dataclass(frozen=True)
class Snapshot:
    directory: Path
    snapshot_id: str
    market_path: Path
    market_sha256: str
    sessions: tuple[date, ...]
    next_session: date
    instruments: tuple[str, ...]
    _cutoffs: Mapping[date, datetime] = field(repr=False)
    _indices: Mapping[date, int] = field(repr=False)
    _listing: Mapping[str, tuple[date | None, date | None]] = field(repr=False)
    # Per-instrument rows of instrument-major float64 grids; NaN marks a missing close.
    _closes: Mapping[str, npt.NDArray[np.float64]] = field(repr=False)
    _adjusted: Mapping[str, npt.NDArray[np.float64]] = field(repr=False)
    _features: Mapping[str, dict[str, tuple[list[datetime], list[float | None]]]] = field(
        repr=False
    )

    _market: Market = field(repr=False, compare=False)
    # Full-history tuples built on first read of each (instrument, adjusted) series, so repeated
    # window reads are tuple slices; shared by every run on this open snapshot.
    _cache_history: bool = field(default=True, repr=False, compare=False)
    _history_cache: dict[tuple[str, bool], tuple[float | None, ...]] = field(
        default_factory=dict, repr=False, compare=False
    )

    def day(self, session: date) -> DecisionDay:
        if session not in self._indices:
            raise ValueError(f"not a decision session: {session}")
        active = tuple(s for s, interval in self._listing.items() if _listed(session, interval))
        cutoff = self._cutoffs[session]
        stop = self._indices[session] + 1

        def history(symbol: str, adjusted: bool, limit: int | None) -> tuple[float | None, ...]:
            start = 0 if limit is None else max(0, stop - limit)
            grid = self._adjusted if adjusted else self._closes
            if not self._cache_history:
                return _history_tuple(grid[symbol][start:stop])
            series = self._history_cache.get((symbol, adjusted))
            if series is None:
                series = self._history_cache[symbol, adjusted] = _history_tuple(grid[symbol])
            return series[start:stop]

        def scores(name: str) -> Mapping[str, float | None]:
            observations = self._features.get(name, {})
            result: dict[str, float | None] = {}
            for symbol in active:
                dates, values = observations.get(symbol, ([], []))
                index = bisect_right(dates, cutoff) - 1
                result[symbol] = values[index] if index >= 0 else None
            return MappingProxyType(result)

        return DecisionDay(session, cutoff, active, history, scores)

    @classmethod
    def create(
        cls,
        directory: str | Path,
        *,
        sessions: Sequence[date],
        next_session: date,
        instruments: Sequence[Instrument],
        daily: Sequence[DailyPrice],
        features: Sequence[Feature] = (),
        cash_dividends: Sequence[CashDividend] = (),
        close_times: Mapping[date, datetime] | None = None,
        cutoffs: Mapping[date, datetime] | None = None,
        source_evidence: Mapping[str, str] | None = None,
    ) -> Snapshot:
        """Publish a reusable daily snapshot. Pass each close in its market's local time.

        The A-share default is 15:00 UTC+8. Strategies run at the supplied close unless
        a later cutoff is provided for that local session. Features use their own
        publication timestamps.
        """
        days = list(sessions)
        if not days or days != sorted(set(days)) or next_session <= days[-1]:
            raise ValueError("provide unique, ordered trading sessions and the following session")
        closes = (
            dict(close_times)
            if close_times is not None
            else {d: datetime.combine(d, time(15), timezone(timedelta(hours=8))) for d in days}
        )
        times = dict(cutoffs) if cutoffs is not None else closes
        _validate_clock(days, closes, times)
        instruments = sorted(instruments, key=lambda item: item.instrument_id)
        names = {s.instrument_id for s in instruments}
        if len(names) != len(instruments) or not names or "" in names:
            raise ValueError("provide unique, nonempty instrument IDs")
        specs = []
        listings = {}
        for item in instruments:
            if item.listed_on and item.delisted_on and item.listed_on >= item.delisted_on:
                raise ValueError("listing interval is empty")
            if item.buy_lot_size < 1 or not scaled(item.tick_size, "tick size"):
                raise ValueError("positive tick and lot sizes are required")
            specs.append(
                {
                    "instrument_id": item.instrument_id,
                    "asset_class": item.asset_class,
                    "venue": item.venue,
                    "currency": item.currency,
                    "buy_lot_size": item.buy_lot_size,
                    "tick_size": scaled(item.tick_size, "tick size"),
                    "settlement": item.settlement,
                }
            )
            listings[item.instrument_id] = (item.listed_on, item.delisted_on)
        rows = []
        research = []
        versions: dict[str, str] = {}
        seen: set[tuple[str, str, datetime]] = set()
        for obs in sorted(
            features, key=lambda f: (f.name, f.instrument_id, f.available_at.isoformat())
        ):
            if (
                obs.instrument_id not in names
                or not obs.name
                or not obs.version
                or obs.name == _ADJUSTED
                or obs.available_at.utcoffset() is None
            ):
                raise ValueError(
                    "feature requires a known instrument, name, version and publication time"
                )
            key = (obs.name, obs.instrument_id, obs.available_at)
            if key in seen or versions.setdefault(obs.name, obs.version) != obs.version:
                raise ValueError("duplicate feature observation or mixed versions")
            seen.add(key)
            research.append(
                {
                    "instrument_id": obs.instrument_id,
                    "name": obs.name,
                    "version": obs.version,
                    "available_at": obs.available_at,
                    "value": obs.value,
                }
            )
        row_keys: set[tuple[date, str]] = set()
        for row in sorted(daily, key=lambda r: (r.session, r.instrument_id)):
            key2 = (row.session, row.instrument_id)
            if row.instrument_id not in names or row.session not in times or key2 in row_keys:
                raise ValueError(f"unknown or duplicate daily row: {key2}")
            row_keys.add(key2)
            if not _listed(row.session, listings[row.instrument_id]) and (
                any(p is not None for p in (row.open, row.close, row.adjusted_close))
                or "yes" in (row.buy_at_open, row.sell_at_open)
            ):
                raise ValueError(f"price or opening permission outside listing interval: {key2}")
            if row.buy_at_open not in ("yes", "no", "unknown") or row.sell_at_open not in (
                "yes",
                "no",
                "unknown",
            ):
                raise ValueError("invalid opening permission")
            if not row.evidence and (row.buy_at_open != "unknown" or row.sell_at_open != "unknown"):
                raise ValueError("known opening permissions require evidence")
            markers = (
                (row.buy_blocked_by_price_limit, row.buy_at_open),
                (row.sell_blocked_by_price_limit, row.sell_at_open),
            )
            if any(type(marker) is not bool for marker, _ in markers):
                raise ValueError("price-limit markers must be booleans")
            if any(marker and permission != "no" for marker, permission in markers):
                raise ValueError("price-limit markers require a known blocked opening permission")
            prices = [scaled(x, "price") if x is not None else None for x in (row.open, row.close)]
            if 0 in prices:
                raise ValueError("raw prices must be positive")
            rows.append(
                {
                    "session": row.session.isoformat(),
                    "instrument_id": row.instrument_id,
                    "open": prices[0],
                    "close": prices[1],
                    "buy_at_open": row.buy_at_open,
                    "sell_at_open": row.sell_at_open,
                    "buy_reason": row.buy_reason,
                    "sell_reason": row.sell_reason,
                    "evidence": row.evidence,
                    "suspended": row.suspended,
                    "corporate_action_gap": row.corporate_action_gap,
                    **(
                        {"buy_blocked_by_price_limit": True}
                        if row.buy_blocked_by_price_limit
                        else {}
                    ),
                    **(
                        {"sell_blocked_by_price_limit": True}
                        if row.sell_blocked_by_price_limit
                        else {}
                    ),
                }
            )
            if row.adjusted_close is not None:
                if not math.isfinite(row.adjusted_close) or row.adjusted_close <= 0:
                    raise ValueError("adjusted close must be positive and finite")
                research.append(
                    {
                        "instrument_id": row.instrument_id,
                        "name": _ADJUSTED,
                        "version": "1",
                        "available_at": closes[row.session],
                        "value": row.adjusted_close,
                    }
                )
        market = {
            "sessions": [d.isoformat() for d in days],
            "next_session": next_session.isoformat(),
            "instruments": specs,
            "rows": rows,
        }
        if cash_dividends:
            actions = []
            action_keys: set[tuple[date, str]] = set()
            for div in sorted(cash_dividends, key=lambda d: (d.ex_session, d.instrument_id)):
                action_key = (div.ex_session, div.instrument_id)
                amount = scaled(div.amount_per_share, "cash dividend per share")
                if not div.record_session < div.ex_session <= div.pay_session:
                    raise ValueError(
                        "cash dividend needs record_session < ex_session <= pay_session"
                    )
                if (
                    not amount
                    or action_key in action_keys
                    or div.instrument_id not in names
                    or div.record_session not in times
                    or any(
                        d < next_session and d not in times
                        for d in (div.ex_session, div.pay_session)
                    )
                ):
                    raise ValueError("invalid or duplicate cash dividend")
                action_keys.add(action_key)
                actions.append(
                    {
                        "instrument_id": div.instrument_id,
                        "record_session": div.record_session.isoformat(),
                        "ex_session": div.ex_session.isoformat(),
                        "pay_session": div.pay_session.isoformat(),
                        "amount_per_share": amount,
                    }
                )
            market["cash_dividends"] = actions
        root = Path(directory).resolve()
        root.mkdir(parents=True, exist_ok=True)
        output = pa.BufferOutputStream()
        pq.write_table(pa.Table.from_pylist(research, schema=_FEATURES), output)
        manifest: dict[str, Any] = {
            "schema_id": "trademaster.daily-snapshot/v1",
            "market": _object(root, "market", "json", canonical(market)),
            "features": _object(root, "features", "parquet", output.getvalue().to_pybytes()),
            "close_times": {d.isoformat(): closes[d].isoformat() for d in days},
            "cutoffs": {d.isoformat(): times[d].astimezone(UTC).isoformat() for d in days},
            "listing": {
                s: [d.isoformat() if d else None for d in interval]
                for s, interval in listings.items()
            },
            "source_evidence": dict(source_evidence or {}),
        }
        manifest["snapshot_id"] = hashlib.sha256(canonical(manifest)).hexdigest()
        _publish(root / "manifest.json", canonical(manifest))
        return cls.open(root)

    @classmethod
    def open(cls, directory: str | Path, *, history_cache: bool = True) -> Snapshot:
        """Verify and load the market and features once for use across backtests.

        With ``history_cache`` (the default), each instrument's close history becomes a cached
        tuple on its first read; pass ``False`` to trade repeated-read speed for lower memory.
        """
        from trademaster._native import Market

        root = Path(directory).resolve()
        manifest = json.loads((root / "manifest.json").read_bytes())
        digest = manifest.pop("snapshot_id")
        if (
            manifest["schema_id"] != "trademaster.daily-snapshot/v1"
            or digest != hashlib.sha256(canonical(manifest)).hexdigest()
        ):
            raise ValueError("snapshot manifest hash or schema mismatch")
        path, content = _verified(root, manifest["market"])
        _, features_bytes = _verified(root, manifest["features"])
        # Rust parses the market once; Python reads its close grid instead of re-parsing JSON.
        native = Market(content)
        del content
        session_text, next_text, symbol_list, close_bytes, observed_bytes = native.price_grid()
        days = tuple(date.fromisoformat(d) for d in session_text)
        indices = {d: i for i, d in enumerate(days)}
        symbols = tuple(symbol_list)
        listing = {
            s: (date.fromisoformat(a) if a else None, date.fromisoformat(b) if b else None)
            for s, (a, b) in manifest["listing"].items()
        }
        if set(listing) != set(symbols):
            raise ValueError("snapshot listing intervals must cover all instruments")
        cutoffs = {
            date.fromisoformat(d): datetime.fromisoformat(t) for d, t in manifest["cutoffs"].items()
        }
        closes = {
            date.fromisoformat(d): datetime.fromisoformat(t)
            for d, t in manifest["close_times"].items()
        }
        _validate_clock(days, closes, cutoffs)
        n_days, n_symbols = len(days), len(symbols)
        # Listed sessions of instrument k occupy the half-open index range [lo[k], hi[k]).
        bounds = [_listed_range(days, listing[s]) for s in symbols]
        lo = np.array([b[0] for b in bounds], np.int64)
        hi = np.array([b[1] for b in bounds], np.int64)
        sessions_axis = np.arange(n_days)
        outside = (sessions_axis < lo[:, None]) | (sessions_axis >= hi[:, None])
        observed = np.frombuffer(observed_bytes, np.uint8).reshape(n_symbols, n_days)
        violations = np.argwhere((observed.astype(bool) & outside).T)  # (session, instrument)
        if len(violations):
            day_index, k = violations[0]
            raise ValueError(
                "price or opening permission outside listing interval: "
                f"{(days[day_index], symbols[k])}"
            )
        del outside, observed, observed_bytes
        raw = np.frombuffer(close_bytes, "<i8").reshape(n_symbols, n_days)
        prices = np.where(raw == 0, np.nan, raw / _SCALE)
        del raw, close_bytes
        next_session = date.fromisoformat(next_text)

        feature_table = pq.read_table(pa.BufferReader(features_bytes))
        del features_bytes
        if feature_table.schema != _FEATURES:
            raise ValueError("snapshot feature schema mismatch")
        is_adjusted = pc.equal(feature_table["name"], pa.scalar(_ADJUSTED))
        adjusted = _adjusted_grid(feature_table.filter(is_adjusted), days, symbols, closes, lo, hi)
        observations: dict[str, dict[str, tuple[list[datetime], list[float | None]]]] = defaultdict(
            dict
        )
        research = feature_table.filter(pc.invert(is_adjusted))
        del feature_table
        research = research.sort_by([("available_at", "ascending")])
        # The verified schema makes these columns non-null except `value`.
        for s, name, t, value in zip(
            cast(list[str], research["instrument_id"].to_pylist()),
            cast(list[str], research["name"].to_pylist()),
            cast(list[datetime], research["available_at"].to_pylist()),
            cast(list[float | None], research["value"].to_pylist()),
            strict=True,
        ):
            timestamps, values = observations[name].setdefault(s, ([], []))
            timestamps.append(t)
            values.append(value)
        return cls(
            root,
            digest,
            path,
            manifest["market"]["sha256"],
            days,
            next_session,
            symbols,
            cutoffs,
            indices,
            listing,
            {s: prices[k] for k, s in enumerate(symbols)},
            {s: adjusted[k] for k, s in enumerate(symbols)},
            observations,
            native,
            history_cache,
        )

    @classmethod
    def from_demo_market(cls, market: DemoMarket, directory: str | Path) -> Snapshot:
        """Convert the verified A-share sample once, keeping raw and signal prices separate."""
        status = {(r["trade_date"], r["instrument_id"]): r for r in market.status.to_pylist()}
        if len(status) != market.status.num_rows:
            raise ValueError("duplicate market status for the same instrument and session")
        rows = []
        for r in market.bars.to_pylist():
            key = (r["trade_date"], r["instrument_id"])
            st = status.pop(key, None)
            if st is None:
                raise ValueError(f"missing market status or duplicate bar: {key}")
            suspended = bool(st["suspended"])
            buy_block = suspended or r["open"] >= st["up_limit"]
            sell_block = suspended or r["open"] <= st["down_limit"]
            rows.append(
                DailyPrice(
                    r["trade_date"],
                    r["instrument_id"],
                    r["open"],
                    r["close"],
                    "no" if buy_block else "yes",
                    "no" if sell_block else "yes",
                    "suspended" if suspended else "upper_limit" if buy_block else None,
                    "suspended" if suspended else "lower_limit" if sell_block else None,
                    st["source_revision"],
                    suspended,
                    False,
                    float(r["close"]) * float(r["adj_factor"]),
                    buy_blocked_by_price_limit=buy_block and not suspended,
                    sell_blocked_by_price_limit=sell_block and not suspended,
                )
            )
        if status:
            raise ValueError("market has status without a daily bar")
        specs = [
            Instrument(s, venue="sse" if s.endswith(".SH") else "szse")
            for s in sorted({r.instrument_id for r in rows})
        ]
        if len(rows) != len(specs) * len(market.days):
            raise ValueError("demo market bars must cover each instrument on every session")
        return cls.create(
            directory,
            sessions=market.days,
            next_session=market.next_session,
            instruments=specs,
            daily=rows,
            source_evidence=market.source_evidence,
        )

    load = open
