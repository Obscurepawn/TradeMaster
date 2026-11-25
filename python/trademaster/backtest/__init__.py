"""Daily stock backtests on a verified data snapshot. Account changes are calculated in Rust."""

from __future__ import annotations

import hashlib
import os
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal, Protocol, runtime_checkable

import pyarrow as pa

if TYPE_CHECKING:
    from trademaster._native import NativeAccount

from .snapshot import (
    Amount,
    CashDividend,
    DailyPrice,
    DecisionDay,
    Feature,
    Instrument,
    Snapshot,
    SnapshotWriter,
    _publish,
    canonical,
    scaled,
)

DecisionSlice = DecisionDay
type Weights = Mapping[str, Amount]
type Schedule = Literal["daily_close", "week_end"]
_UNIT = Decimal(100_000_000)


def _currency(units: int) -> Decimal:
    return Decimal(f"{units}e-8")


@dataclass(frozen=True)
class Account:
    """Cash, marked equity and share balances at one close."""

    _ledger: NativeAccount

    @property
    def cash(self) -> Decimal:
        return _currency(self._ledger.cash)

    @property
    def dividend_receivable(self) -> Decimal:
        """Cash dividends earned on shares and awaiting payment."""
        return _currency(self._ledger.dividend_receivable)

    @property
    def equity(self) -> Decimal:
        return _currency(self._ledger.equity)

    def quantity(self, symbol: str) -> int:
        return self._ledger.quantity(symbol)

    def sellable_quantity(self, symbol: str) -> int:
        return self._ledger.sellable_quantity(symbol)


@dataclass(frozen=True)
class Fill:
    """One execution. Fees include the commission, tax, transfer fee and slippage charge."""

    session: date
    instrument_id: str
    side: Literal["buy", "sell"]
    quantity: int
    price: Decimal
    fees: Decimal
    batch_id: str

    @classmethod
    def _from_trade(cls, trade: dict[str, Any]) -> Fill:
        charges = sum(trade[key] for key in ("commission", "tax", "transfer_fee", "slippage"))
        return cls(
            date.fromisoformat(trade["session"]),
            trade["instrument_id"],
            trade["side"],
            trade["quantity"],
            _currency(trade["price"]),
            _currency(charges),
            trade["batch_id"],
        )


@dataclass(frozen=True)
class BacktestDay(DecisionDay):
    """Market data, the closing account and fills since the previous decision."""

    account: Account
    fills: tuple[Fill, ...]


@runtime_checkable
class Strategy(Protocol):
    def on_close(self, day: BacktestDay) -> Weights | None: ...


CloseStrategy = Strategy


@dataclass(frozen=True)
class TargetBatch:
    """Complete target decided at a session close. An empty mapping means all cash."""

    decision_session: date
    weights: Weights
    eligible_session: date | None = None
    batch_id: str | None = None
    snapshot_id: str | None = None


@dataclass(frozen=True)
class InitialLot:
    instrument_id: str
    quantity: int
    sellable_session: date


@dataclass(frozen=True)
class InitialAccount:
    cash: Amount
    lots: Sequence[InitialLot] = ()


@dataclass(frozen=True)
class Policy:
    """Run costs and opening checks, with A-share defaults."""

    commission_ppm: int = 300
    minimum_commission: Amount = Decimal(5)
    sell_stamp_duty_ppm: int = 500
    sse_transfer_fee_ppm: int = 10
    slippage_ppm: int = 0
    buy_cap_ppm: int = 20_000
    max_attempts: int = 3
    enforce_price_limits: bool = True

    def __post_init__(self) -> None:
        if type(self.enforce_price_limits) is not bool:
            raise ValueError("enforce_price_limits must be a boolean")


_DEFAULT_POLICY = Policy()


@dataclass(frozen=True)
class BacktestResult:
    """Raw Rust fields use fixed-8 integers; equity and summary use the account currency."""

    snapshot_id: str
    request: dict[str, Any]
    raw: dict[str, Any]
    targets: list[dict[str, Any]]
    decision_metadata: list[dict[str, str]]
    summary: dict[str, Any]
    equity: list[dict[str, Any]]

    def save(self, directory: str | Path) -> Path:
        """Write result.json and a request replayable from its file path with tm-replay."""
        root = Path(directory).resolve()
        root.mkdir(parents=True, exist_ok=True)
        request = {
            **self.request,
            "snapshot_path": os.path.relpath(self.request["snapshot_path"], root),
        }
        _publish(root / "result.json", canonical(self.raw))
        request_file = root / "request.json"
        _publish(request_file, canonical(request))
        return request_file

    @property
    def attempts(self) -> list[dict[str, Any]]:
        return self.raw["attempts"]  # type: ignore[no-any-return]

    @property
    def orders(self) -> list[dict[str, Any]]:
        return self.raw["orders"]  # type: ignore[no-any-return]

    @property
    def fills(self) -> list[dict[str, Any]]:
        return self.raw["fills"]  # type: ignore[no-any-return]

    @property
    def dividends(self) -> list[dict[str, Any]]:
        """Dividend entitlements in fixed-8 units, with their payment status."""
        return self.raw.get("dividends", [])  # type: ignore[no-any-return]

    @property
    def positions(self) -> list[dict[str, Any]]:
        return self.raw["positions"]  # type: ignore[no-any-return]


def _date(value: date | str) -> date:
    return date.fromisoformat(value) if isinstance(value, str) else value


def _batches_from_table(table: pa.Table) -> list[TargetBatch]:
    """One row per decision. An empty 'weights' Arrow map represents an all-cash target."""
    if not {"decision_session", "weights"} <= set(table.column_names):
        raise ValueError("target table needs decision_session and weights columns")
    batches = []
    for row in table.to_pylist():
        if row["weights"] is None:
            continue
        weights = dict(row["weights"])
        if len(weights) != len(row["weights"]):
            raise ValueError("duplicate instrument in target weights")
        eligible = row.get("eligible_session")
        batches.append(
            TargetBatch(
                _date(row["decision_session"]),
                weights,
                _date(eligible) if eligible is not None else None,
                row.get("batch_id"),
                row.get("snapshot_id"),
            )
        )
    return batches


def _decision_indices(snapshot: Snapshot, schedule: Schedule) -> list[int]:
    if schedule == "daily_close":
        return list(range(len(snapshot.sessions)))
    if schedule != "week_end":
        raise ValueError("schedule must be daily_close or week_end")
    following = (*snapshot.sessions[1:], snapshot.next_session)
    return [
        i
        for i, (session, after) in enumerate(zip(snapshot.sessions, following, strict=True))
        if session.isocalendar()[:2] != after.isocalendar()[:2]
    ]


def _encode_target(
    snapshot: Snapshot, batch: TargetBatch, day: DecisionDay
) -> tuple[dict[str, Any], dict[str, str]]:
    decision = day.session
    if batch.snapshot_id is not None and batch.snapshot_id != snapshot.snapshot_id:
        raise ValueError("target snapshot identity mismatch")
    i = snapshot._indices[decision]
    eligible = batch.eligible_session or (
        snapshot.sessions[i + 1] if i + 1 < len(snapshot.sessions) else snapshot.next_session
    )
    if eligible <= decision or (
        eligible != snapshot.next_session and eligible not in snapshot._indices
    ):
        raise ValueError("target eligibility must be a known session after its decision")
    weights = {}
    for symbol, value in sorted(batch.weights.items()):
        if symbol not in day._universe:
            raise ValueError(f"instrument outside the decision universe: {symbol}")
        weights[symbol] = scaled(value, "weight")
    if sum(weights.values()) > int(_UNIT):
        raise ValueError("target weights must sum to at most one")
    row: dict[str, Any] = {
        "decision_session": decision.isoformat(),
        "eligible_session": eligible.isoformat(),
        "weights": weights,
    }
    batch_id = batch.batch_id
    if batch_id is None:
        batch_id = hashlib.sha256(canonical([snapshot.snapshot_id, row])).hexdigest()
    if not batch_id:
        raise ValueError("target batch IDs must be nonempty and unique")
    row["batch_id"] = batch_id
    metadata = {
        "batch_id": batch_id,
        "decision_at": day.decision_at.isoformat(),
        "snapshot_id": snapshot.snapshot_id,
    }
    return row, metadata


def _encode_targets(
    snapshot: Snapshot, batches: Sequence[TargetBatch]
) -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
    rows = []
    metadata = []
    ids: set[str] = set()
    seen_sessions: set[date] = set()
    for batch in sorted(batches, key=lambda b: b.decision_session):
        decision = batch.decision_session
        if decision not in snapshot._indices or decision in seen_sessions:
            raise ValueError(f"unknown or duplicate decision session: {decision}")
        seen_sessions.add(decision)
        row, info = _encode_target(snapshot, batch, snapshot.day(decision))
        if info["batch_id"] in ids:
            raise ValueError("target batch IDs must be nonempty and unique")
        ids.add(info["batch_id"])
        rows.append(row)
        metadata.append(info)
    return rows, metadata


def _summarize(
    raw: dict[str, Any], daily: list[dict[str, Any]], session_count: int
) -> dict[str, Any]:
    nav = [row["equity"] for row in daily]
    completed = raw["status"] == "completed"
    rates = [b["status"] for b in raw["batches"]]
    reached, expired = rates.count("reached"), rates.count("expired")
    average = sum(nav) / len(nav) if nav else None
    peak = None
    drawdowns = []
    for value in nav:
        peak = value if peak is None else max(peak, value)
        if peak:
            drawdowns.append(value / peak - 1)
    return {
        "status": raw["status"],
        "first_issue": raw["first_issue"],
        "net_return": (nav[-1] / nav[0] - 1 if completed and nav and nav[0] else None),
        "maximum_drawdown": (min(drawdowns) if completed and drawdowns else None),
        "turnover": Decimal(raw["turnover_notional"]) / _UNIT / average
        if completed and average
        else None,
        "total_costs": Decimal(raw["total_costs"]) / _UNIT,
        "fill_count": raw["fill_count"],
        "order_count": raw["order_count"],
        "rejected_order_count": raw["rejected_order_count"],
        "expired_target_rate": expired / (reached + expired) if reached + expired else None,
        "data_coverage": len(nav) / session_count,
        "valued_sessions": len(nav),
        "requested_sessions": session_count,
        "batch_counts": {
            s: rates.count(s)
            for s in ("reached", "expired", "replaced", "end_of_run", "insufficient_data")
        },
    }


def backtest(
    snapshot: Snapshot,
    strategy: Strategy | None = None,
    *,
    targets: Mapping[date, Weights | None] | Sequence[TargetBatch] | pa.Table | None = None,
    schedule: Schedule = "daily_close",
    initial_cash: Amount | None = None,
    initial_account: InitialAccount | None = None,
    policy: Policy = _DEFAULT_POLICY,
    detail: bool = False,
) -> BacktestResult:
    """Call a strategy at the selected closes or replay dated complete targets.

    A strategy's on_close(day) receives the closing account and recent fills.
    Dates in a mapping are decision sessions. None keeps the active target, {} exits
    all holdings. A last-session decision is recorded but has no in-sample execution.
    """
    if (strategy is None) == (targets is None):
        raise ValueError("supply exactly one strategy or target table")
    if (initial_cash is None) == (initial_account is None):
        raise ValueError("supply exactly one initial_cash or initial_account")
    batches: list[TargetBatch] = []
    if strategy is None:
        if schedule != "daily_close":
            raise ValueError("target batches supply their own schedule")
        if isinstance(targets, pa.Table):
            batches = _batches_from_table(targets)
        elif isinstance(targets, Mapping):
            batches = [TargetBatch(d, w) for d, w in targets.items() if w is not None]
        else:
            assert targets is not None
            batches = list(targets)
    rows, metadata = _encode_targets(snapshot, batches)
    account = (
        initial_account
        if initial_account is not None
        else InitialAccount(initial_cash if initial_cash is not None else 0)
    )
    lots = []
    for lot in account.lots:
        if (
            lot.instrument_id not in snapshot.instruments
            or type(lot.quantity) is not int
            or lot.quantity <= 0
        ):
            raise ValueError("initial lot needs a known instrument and positive integer quantity")
        lots.append(
            {
                "instrument_id": lot.instrument_id,
                "quantity": lot.quantity,
                "sellable_session": lot.sellable_session.isoformat(),
            }
        )
    policy_fields = asdict(policy)
    if policy.enforce_price_limits:
        del policy_fields["enforce_price_limits"]
    policy_fields["minimum_commission"] = scaled(policy.minimum_commission, "minimum commission")
    run = {
        "initial_cash": scaled(account.cash, "initial cash"),
        "initial_lots": lots,
        "targets": rows,
        "policy": policy_fields,
        "detail": detail,
    }
    request = {
        "schema_id": "trademaster.snapshot-run/v1",
        "snapshot_path": str(snapshot.market_path),
        "snapshot_sha256": snapshot.market_sha256,
        "snapshot_id": snapshot.snapshot_id,
        "run": run,
    }
    if strategy is None:
        raw = snapshot._market.run(run, [])
    else:
        on_close = strategy.on_close

        def decide(
            index: int, ledger: NativeAccount, trades: list[dict[str, Any]]
        ) -> dict[str, Any] | None:
            market = snapshot.day(snapshot.sessions[index])
            day = BacktestDay(
                market.session,
                market.decision_at,
                market.instruments,
                market._history,
                market._scores,
                Account(ledger),
                tuple(Fill._from_trade(t) for t in trades),
            )
            weights = on_close(day)
            if weights is None:
                return None
            row, info = _encode_target(snapshot, TargetBatch(day.session, weights), day)
            rows.append(row)
            metadata.append(info)
            return row

        indices = _decision_indices(snapshot, schedule)
        raw = snapshot._market.run(run, indices, decide)
    daily = [
        {
            "session": row["session"],
            "cash": Decimal(row["cash"]) / _UNIT,
            "market_value": Decimal(row["market_value"]) / _UNIT,
            **(
                {"dividend_receivable": _currency(row["dividend_receivable"])}
                if "dividend_receivable" in row
                else {}
            ),
            "equity": Decimal(row["equity"]) / _UNIT,
        }
        for row in raw["daily"]
    ]
    return BacktestResult(
        snapshot.snapshot_id,
        request,
        raw,
        rows,
        metadata,
        _summarize(raw, daily, len(snapshot.sessions)),
        daily,
    )


__all__ = [
    "Account",
    "BacktestDay",
    "BacktestResult",
    "CashDividend",
    "CloseStrategy",
    "DailyPrice",
    "DecisionDay",
    "DecisionSlice",
    "Feature",
    "Fill",
    "InitialAccount",
    "InitialLot",
    "Instrument",
    "Policy",
    "Snapshot",
    "SnapshotWriter",
    "Strategy",
    "TargetBatch",
    "backtest",
]
