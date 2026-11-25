import hashlib
import json
from datetime import UTC, date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

import pyarrow as pa
import pytest
from trademaster.backtest import (
    BacktestDay,
    DailyPrice,
    DecisionDay,
    Feature,
    InitialAccount,
    InitialLot,
    Instrument,
    Policy,
    Snapshot,
    TargetBatch,
    backtest,
)
from trademaster.e2e.demo_data import DemoMarket
from trademaster.replay import replay

DAYS = tuple(date(2024, 1, d) for d in (5, 8, 9, 10))
NEXT = date(2024, 1, 11)


def weekly_snapshot(root: Path) -> Snapshot:
    rows = []
    for i, day in enumerate(DAYS):
        rows.append(
            DailyPrice(
                day,
                "A",
                10 if i == 0 else 9,
                10 if i == 0 else 9,
                sell_at_open="no" if i == 1 else "yes",
                sell_reason="lower_limit" if i == 1 else None,
                evidence="fixture",
            )
        )
        rows.append(DailyPrice(day, "B", 20, 20, evidence="fixture"))
    return Snapshot.create(
        root,
        sessions=DAYS,
        next_session=NEXT,
        instruments=[Instrument("A"), Instrument("B")],
        daily=rows,
        source_evidence={"fixture": "四日样本"},
        features=[
            Feature("B", "score", 2.0, datetime(2024, 1, 5, 7, tzinfo=UTC)),
            Feature("A", "score", 8.0, datetime(2024, 1, 5, 8, tzinfo=UTC)),
        ],
    )


def test_verified_snapshot_limits_prices_and_factors_to_close(tmp_path: Path) -> None:
    stored = weekly_snapshot(tmp_path)
    snap = Snapshot.open(tmp_path)
    friday = snap.day(DAYS[0])
    assert friday.session == DAYS[0]
    assert friday.closes("A") == (10.0,)
    assert friday.scores("score") == {"A": None, "B": 2.0}
    assert snap.day(DAYS[1]).scores("score")["A"] == 8.0
    assert snap.day(DAYS[2]).closes("A") == (10.0, 9.0, 9.0)
    assert snap.snapshot_id == stored.snapshot_id
    snap.market_path.write_bytes(b"{}")
    with pytest.raises(ValueError, match="hash"):
        Snapshot.open(tmp_path)


def test_a_close_decision_cannot_run_before_the_session_close(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="close"):
        Snapshot.create(
            tmp_path,
            sessions=[DAYS[0]],
            next_session=DAYS[1],
            instruments=[Instrument("A")],
            daily=[DailyPrice(DAYS[0], "A", 10, 11, evidence="fixture")],
            cutoffs={DAYS[0]: datetime(2024, 1, 5, 1, 30, tzinfo=UTC)},
        )


def test_bounded_closes_keep_calendar_gaps_and_stop_at_the_decision(tmp_path: Path) -> None:
    snap = Snapshot.create(
        tmp_path,
        sessions=DAYS,
        next_session=NEXT,
        instruments=[Instrument("A")],
        daily=[
            DailyPrice(d, "A", price, price, evidence="fixture", adjusted_close=price * 2)
            for d, price in zip((DAYS[0], DAYS[2], DAYS[3]), (10, 12, 14), strict=True)
        ],
    )
    day = snap.day(DAYS[2])
    assert day.closes("A", limit=1) == (12.0,)
    assert day.closes("A", adjusted=True, limit=2) == (None, 24.0)
    assert day.closes("A", limit=21) == day.closes("A") == (10.0, None, 12.0)


@pytest.mark.parametrize("limit", [0, -1, True, 1.5])
def test_history_limit_requires_a_positive_integer(tmp_path: Path, limit: int) -> None:
    with pytest.raises(ValueError, match="positive integer"):
        weekly_snapshot(tmp_path).day(DAYS[0]).closes("A", limit=limit)


def test_open_rejects_a_hash_valid_manifest_with_a_pre_close_cutoff(tmp_path: Path) -> None:
    weekly_snapshot(tmp_path)
    path = tmp_path / "manifest.json"
    manifest = json.loads(path.read_bytes())
    del manifest["snapshot_id"]
    manifest["cutoffs"][DAYS[0].isoformat()] = "2024-01-05T06:00:00+00:00"
    content = json.dumps(manifest, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    manifest["snapshot_id"] = hashlib.sha256(content.encode()).hexdigest()
    path.write_text(json.dumps(manifest))
    with pytest.raises(ValueError, match="close"):
        Snapshot.open(tmp_path)


def test_calendar_supplies_close_and_later_decision_cutoff_in_market_time(tmp_path: Path) -> None:
    west = timezone(timedelta(hours=-10))
    day = DAYS[0]
    close = datetime(2024, 1, 5, 16, tzinfo=west)  # Jan 6 in UTC.
    snap = Snapshot.create(
        tmp_path,
        sessions=[day],
        next_session=DAYS[1],
        instruments=[Instrument("A")],
        daily=[DailyPrice(day, "A", 9, 10, evidence="fixture", adjusted_close=20)],
        features=[
            Feature("A", "news", 1.0, close + timedelta(minutes=10)),
            Feature("A", "news", 2.0, close + timedelta(hours=1)),
        ],
        close_times={day: close},
        cutoffs={day: close + timedelta(minutes=30)},
    )
    ctx = snap.day(day)
    assert ctx.closes("A", adjusted=True) == (20.0,)
    assert ctx.scores("news")["A"] == 1.0
    assert ctx.decision_at == close + timedelta(minutes=30)


def test_demo_market_alignment_adjusted_history_and_repeat_publication(tmp_path: Path) -> None:
    bars = pa.Table.from_pylist(
        [
            {"trade_date": d, "instrument_id": "A.SH", "open": 9, "close": 10, "adj_factor": f}
            for d, f in zip(DAYS[:2], [1.0, 2.0], strict=True)
        ]
    )
    status = pa.Table.from_pylist(
        [
            {
                "trade_date": d,
                "instrument_id": "A.SH",
                "suspended": False,
                "up_limit": 11,
                "down_limit": 8,
                "source_revision": "fixture",
            }
            for d in DAYS[:2]
        ]
    )
    market = DemoMarket(DAYS[:2], DAYS[2], bars, status, {"fixture": "v1"})
    snap = Snapshot.from_demo_market(market, tmp_path)
    assert snap.day(DAYS[0]).closes("A.SH", adjusted=True) == (10.0,)
    assert snap.day(DAYS[1]).closes("A.SH", adjusted=True) == (10.0, 20.0)
    assert snap.day(DAYS[1]).closes("A.SH") == (10.0, 10.0)
    assert Snapshot.from_demo_market(market, tmp_path).snapshot_id == snap.snapshot_id
    missing_status = DemoMarket(market.days, market.next_session, bars, status.slice(1), {})
    with pytest.raises(ValueError, match="missing"):
        Snapshot.from_demo_market(missing_status, tmp_path)


def test_callback_and_dated_targets_match_the_four_session_account(tmp_path: Path) -> None:
    snap = weekly_snapshot(tmp_path)

    class Rotation:
        def on_close(self, day: DecisionDay) -> dict[str, Decimal]:
            assert day.scores("score")["A"] is None
            return {"B": Decimal("0.80")}

    account = InitialAccount(20_000, (InitialLot("A", 8000, DAYS[0]),))
    policy = Policy(
        commission_ppm=0,
        minimum_commission=5,
        sse_transfer_fee_ppm=0,
        sell_stamp_duty_ppm=0,
        slippage_ppm=0,
        buy_cap_ppm=20000,
        max_attempts=3,
    )
    detailed = backtest(
        snap,
        Rotation(),
        schedule="week_end",
        initial_account=account,
        policy=policy,
        detail=True,
    )
    screen = backtest(
        snap,
        targets={DAYS[0]: {"B": Decimal("0.80")}},
        initial_account=account,
        policy=policy,
    )
    assert detailed.targets == screen.targets
    assert detailed.summary == screen.summary
    assert detailed.equity == screen.equity
    assert [d["equity"] for d in detailed.equity] == [100000, 92000, 91995, 91990]
    assert detailed.equity[-1]["cash"] == 19990
    assert len(detailed.fills) == 2 and not screen.fills
    assert detailed.raw["fill_count"] == 2
    assert detailed.request["snapshot_sha256"] == snap.market_sha256


def test_none_cash_and_last_session_are_distinct(tmp_path: Path) -> None:
    snap = weekly_snapshot(tmp_path)
    result = backtest(
        snap,
        targets={DAYS[0]: {}, DAYS[1]: None, DAYS[-1]: {"A": Decimal(1)}},
        initial_cash=10000,
    )
    assert [b["weights"] for b in result.targets] == [{}, {"A": 100000000}]
    assert result.targets[-1]["eligible_session"] == NEXT.isoformat()
    assert result.raw["batches"][-1]["status"] == "end_of_run"


def test_arrow_targets_preserve_batch_id_cash_and_snapshot_identity(tmp_path: Path) -> None:
    snap = weekly_snapshot(tmp_path)
    table = pa.table(
        {
            "decision_session": [DAYS[0], DAYS[1], DAYS[-1]],
            "weights": pa.array([[("B", 0.8)], None, []], type=pa.map_(pa.string(), pa.float64())),
            "batch_id": ["enter", "hold", "cash"],
            "snapshot_id": [snap.snapshot_id] * 3,
        }
    )
    result = backtest(snap, targets=table, initial_cash=10000)
    assert [r["batch_id"] for r in result.targets] == ["enter", "cash"]
    assert result.targets[-1]["weights"] == {}
    assert result.decision_metadata[0] == {
        "batch_id": "enter",
        "decision_at": snap.day(DAYS[0]).decision_at.isoformat(),
        "snapshot_id": snap.snapshot_id,
    }
    with pytest.raises(ValueError, match="identity"):
        backtest(
            snap,
            targets=[TargetBatch(DAYS[0], {}, snapshot_id="another")],
            initial_cash=10000,
        )


def test_missing_close_reports_coverage_and_no_full_period_return(tmp_path: Path) -> None:
    snap = Snapshot.create(
        tmp_path,
        sessions=DAYS[:2],
        next_session=DAYS[2],
        instruments=[Instrument("A")],
        daily=[DailyPrice(DAYS[0], "A", 10, 10, evidence="fixture")],
    )
    result = backtest(
        snap,
        targets={},
        initial_account=InitialAccount(0, (InitialLot("A", 100, DAYS[0]),)),
    )
    assert result.summary["status"] == "insufficient_data"
    assert result.summary["first_issue"] == {
        "session": DAYS[1].isoformat(),
        "instrument_id": "A",
        "reason": "missing_close",
        "phase": "close",
    }
    assert result.summary["data_coverage"] == 0.5
    assert result.summary["net_return"] is None
    assert result.summary["maximum_drawdown"] is None


def test_delisted_instrument_cannot_fill_a_pending_target(tmp_path: Path) -> None:
    instrument = Instrument("A", delisted_on=DAYS[1])
    friday = DailyPrice(DAYS[0], "A", 10, 10, evidence="fixture")

    def publish(rows: list[DailyPrice]) -> Snapshot:
        return Snapshot.create(
            tmp_path, sessions=DAYS[:2], next_session=DAYS[2], instruments=[instrument], daily=rows
        )

    with pytest.raises(ValueError, match="listing interval"):
        publish([friday, DailyPrice(DAYS[1], "A", 10, 10, evidence="fixture")])
    snapshot = publish([friday])
    assert snapshot.day(DAYS[1]).instruments == ()
    result = backtest(snapshot, targets={DAYS[0]: {"A": 0.8}}, initial_cash=10000, detail=True)
    assert result.summary["status"] == "insufficient_data"
    assert result.fills == []


def test_saved_request_moves_with_its_snapshot_and_preserves_the_raw_result(tmp_path: Path) -> None:
    bundle = tmp_path / "研究"
    snap = weekly_snapshot(bundle / "snapshot")
    result = backtest(snap, targets={DAYS[0]: {"B": 0.8}}, initial_cash=10000)
    request_file = result.save(bundle / "export")
    assert request_file == result.save(bundle / "export")
    assert request_file.name == "request.json"
    assert result.request["snapshot_path"] == str(snap.market_path)
    moved = bundle.rename(tmp_path / "moved")
    export = moved / "export"
    saved = json.loads((export / "request.json").read_bytes())
    assert not Path(saved["snapshot_path"]).is_absolute()
    assert (export / saved["snapshot_path"]).resolve().is_file()
    assert json.loads((export / "result.json").read_bytes()) == result.raw
    assert json.loads(replay(export / "request.json")) == result.raw


@pytest.mark.parametrize(
    "weights",
    [
        {"Z": Decimal(1)},
        {"A": Decimal("-0.1")},
        {"A": Decimal("1.1")},
        {"A": Decimal("0.000000001")},
        {"A": Decimal("0.8000000000000000000000000000001")},
    ],
)
def test_invalid_weights_are_rejected(tmp_path: Path, weights: dict[str, Decimal]) -> None:
    with pytest.raises(ValueError, match="instrument|weight|precision"):
        backtest(
            weekly_snapshot(tmp_path),
            targets={DAYS[0]: weights},
            initial_cash=10000,
        )


def test_empty_arrow_table_keeps_shares_but_dated_empty_weights_liquidate(tmp_path: Path) -> None:
    snap = weekly_snapshot(tmp_path)
    initial = InitialAccount(1000, (InitialLot("A", 100, DAYS[0]),))
    table = pa.table(
        {
            "decision_session": [DAYS[1]],
            "weights": pa.array([[]], type=pa.map_(pa.string(), pa.float64())),
        }
    )
    hold = backtest(snap, targets=table.slice(0, 0), initial_account=initial, detail=True)
    exit_position = backtest(snap, targets=table, initial_account=initial, detail=True)
    assert hold.targets == [] and hold.fills == []
    assert hold.positions[-1]["quantity"] == 100
    assert exit_position.targets[0]["weights"] == {}
    assert [(f["side"], f["quantity"]) for f in exit_position.fills] == [("sell", 100)]
    assert exit_position.equity[-1]["market_value"] == 0


@pytest.mark.parametrize("older_eligible", [DAYS[2], DAYS[3], NEXT])
def test_later_decision_wins_when_an_older_target_is_delayed(
    tmp_path: Path, older_eligible: date
) -> None:
    snap = weekly_snapshot(tmp_path)
    initial = InitialAccount(1000, (InitialLot("A", 100, DAYS[0]),))
    table = pa.table(
        {
            "decision_session": [DAYS[1], DAYS[0]],
            "eligible_session": [DAYS[2], older_eligible],
            "weights": pa.array([[], [("B", 0.8)]], type=pa.map_(pa.string(), pa.float64())),
            "batch_id": ["exit", "older"],
        }
    )
    result = backtest(snap, targets=table, initial_account=initial, detail=True)
    assert result.raw["batches"] == [
        {"batch_id": "older", "status": "replaced", "attempts": 0},
        {"batch_id": "exit", "status": "reached", "attempts": 1},
    ]

    class ExitAtClose:
        def on_close(self, day: DecisionDay) -> dict[str, Decimal] | None:
            return {} if day.session == DAYS[1] else None

    callback = backtest(snap, ExitAtClose(), initial_account=initial, detail=True)
    assert result.equity == callback.equity
    assert [(f["session"], f["side"], f["instrument_id"], f["quantity"]) for f in result.fills] == [
        (DAYS[2].isoformat(), "sell", "A", 100)
    ]
    assert json.loads(replay(result.save(tmp_path / "export"))) == result.raw


@pytest.mark.parametrize(
    ("batches", "message"),
    [
        ([TargetBatch(DAYS[1], {}, DAYS[1])], "eligib"),
        ([TargetBatch(DAYS[0], {}, date(2024, 1, 6))], "eligib"),
        (
            [TargetBatch(DAYS[0], {}, DAYS[1]), TargetBatch(DAYS[0], {}, DAYS[2])],
            "duplicate decision",
        ),
        (
            [TargetBatch(DAYS[0], {}, DAYS[2], "dup"), TargetBatch(DAYS[1], {}, DAYS[2], "dup")],
            "batch IDs",
        ),
    ],
)
def test_target_dates_and_ids_still_reject_invalid_batches(
    tmp_path: Path, batches: list[TargetBatch], message: str
) -> None:
    with pytest.raises(ValueError, match=message):
        backtest(weekly_snapshot(tmp_path), targets=batches, initial_cash=1000)


def test_strategy_waits_for_sale_and_receives_fills_in_summary_mode(tmp_path: Path) -> None:
    snap = weekly_snapshot(tmp_path)
    days: list[BacktestDay] = []

    class WaitForSale:
        def on_close(self, day: BacktestDay) -> dict[str, Decimal] | None:
            days.append(day)
            if len(days) == 1:
                return {}
            if day.account.quantity("A") == 0 and day.account.quantity("B") == 0:
                return {"B": Decimal("0.80")}
            return None

    account = InitialAccount(20000, (InitialLot("A", 8000, DAYS[0]),))
    policy = Policy(commission_ppm=0, sse_transfer_fee_ppm=0, sell_stamp_duty_ppm=0)
    run = backtest(snap, WaitForSale(), initial_account=account, policy=policy)
    assert [day.session for day in days] == list(DAYS)
    assert [day.account.cash for day in days] == [20000, 20000, 91995, 19990]
    assert [day.account.equity for day in days] == [100000, 92000, 91995, 91990]
    assert [day.account.quantity("A") for day in days] == [8000, 8000, 0, 0]
    assert [day.account.sellable_quantity("B") for day in days] == [0, 0, 0, 0]
    assert days[-1].account.quantity("B") == 3600
    assert [len(day.fills) for day in days] == [0, 0, 1, 1]
    sold, bought = days[2].fills[0], days[3].fills[0]
    assert (sold.session, sold.instrument_id, sold.side, sold.quantity) == (
        DAYS[2],
        "A",
        "sell",
        8000,
    )
    assert (bought.session, bought.instrument_id, bought.side, bought.quantity) == (
        DAYS[3],
        "B",
        "buy",
        3600,
    )
    assert sold.price == 9 and bought.price == 20 and sold.fees == bought.fees == 5
    assert [t["batch_id"] for t in run.targets] == [sold.batch_id, bought.batch_id]
    assert [t["decision_session"] for t in run.targets] == [
        DAYS[0].isoformat(),
        DAYS[2].isoformat(),
    ]
    assert run.fills == [] and run.summary["fill_count"] == 2
    batch = backtest(
        snap,
        targets={DAYS[0]: {}, DAYS[2]: {"B": Decimal("0.80")}},
        initial_account=account,
        policy=policy,
    )
    assert run.raw == batch.raw


def test_weekly_on_close_reads_intervening_trades_at_the_next_decision(tmp_path: Path) -> None:
    sessions = (*DAYS, date(2024, 1, 12))
    snap = Snapshot.create(
        tmp_path,
        sessions=sessions,
        next_session=date(2024, 1, 15),
        instruments=[Instrument("A")],
        daily=[DailyPrice(d, "A", 10, 10, evidence="fixture") for d in sessions],
    )
    observed: list[BacktestDay] = []

    class BuyAtFirstClose:
        def on_close(self, day: BacktestDay) -> dict[str, Decimal] | None:
            observed.append(day)
            return {"A": Decimal(1)} if len(observed) == 1 else None

    backtest(snap, BuyAtFirstClose(), schedule="week_end", initial_cash=10000)
    first, second = observed
    assert (first.session, second.session) == (sessions[0], sessions[-1])
    assert first.account.quantity("A") == 0 and first.fills == ()
    assert len(second.fills) == 1 and second.fills[0].session == DAYS[1]
    assert second.account.sellable_quantity("A") == second.fills[0].quantity


def test_on_close_propagates_its_error(tmp_path: Path) -> None:
    snap = weekly_snapshot(tmp_path)
    sessions = []
    error = LookupError("invalid factor")

    class FailAtSecondClose:
        def on_close(self, day: BacktestDay) -> dict[str, Decimal]:
            sessions.append(day.session)
            if len(sessions) == 2:
                assert day.account.quantity("B") > 0
                raise error
            return {"B": Decimal("0.80")}

    with pytest.raises(LookupError) as raised:
        backtest(snap, FailAtSecondClose(), initial_cash=20000)
    assert raised.value is error
    assert sessions == list(DAYS[:2])
    rerun = backtest(snap, targets={}, initial_cash=20000)
    assert all(row["cash"] == row["equity"] == Decimal(20000) for row in rerun.equity)


def test_incomplete_close_receives_no_decision(tmp_path: Path) -> None:
    snap = Snapshot.create(
        tmp_path,
        sessions=DAYS,
        next_session=NEXT,
        instruments=[Instrument("A")],
        daily=[DailyPrice(DAYS[0], "A", 10, 10, evidence="fixture")],
    )
    observed = []

    class Observe:
        def on_close(self, day: BacktestDay) -> None:
            observed.append(day.session)

    run = backtest(
        snap, Observe(), initial_account=InitialAccount(0, (InitialLot("A", 100, DAYS[0]),))
    )
    assert run.summary["status"] == "insufficient_data"
    assert observed == [DAYS[0]]


def test_open_snapshot_keeps_verified_market_for_every_run(tmp_path: Path) -> None:
    snap = weekly_snapshot(tmp_path)
    snap.market_path.write_bytes(b"{}")
    for cash in (20000, 40000):
        run = backtest(
            snap, targets={DAYS[0]: {"B": Decimal("0.80")}}, initial_cash=cash, detail=True
        )
        assert run.fills[0]["price"] == 20 * 100_000_000
        assert run.equity[0]["cash"] == cash
    with pytest.raises(ValueError, match="hash"):
        Snapshot.open(tmp_path)


def test_dividend_follows_the_entitled_holder_and_payment_date(tmp_path: Path) -> None:
    from trademaster.backtest import CashDividend

    snap = Snapshot.create(
        tmp_path,
        sessions=DAYS,
        next_session=NEXT,
        instruments=[Instrument("A"), Instrument("B")],
        daily=[
            DailyPrice(day, symbol, price, price, evidence="fixture")
            for day in DAYS
            for symbol, price in (("A", 10 if day == DAYS[0] else 9), ("B", 1))
        ],
        cash_dividends=[CashDividend("A", DAYS[0], DAYS[1], DAYS[2], 1)],
    )
    balances = []

    class Rotate:
        def on_close(self, day: BacktestDay) -> dict[str, int]:
            balances.append((day.account.cash, day.account.dividend_receivable, day.account.equity))
            return {} if day.session == DAYS[0] else {"B": 1}

    run = backtest(
        snap,
        Rotate(),
        initial_account=InitialAccount(100, [InitialLot("A", 100, DAYS[0])]),
        policy=Policy(0, 0, 0, 0, 0, 0),
        detail=True,
    )
    assert balances == [
        (Decimal(cash), Decimal(receivable), Decimal(equity))
        for cash, receivable, equity in [
            (100, 0, 1100),
            (1000, 100, 1100),
            (100, 0, 1100),
            (0, 0, 1100),
        ]
    ]
    assert [(f["side"], f["quantity"]) for f in run.fills] == [
        ("sell", 100),
        ("buy", 1000),
        ("buy", 100),
    ]
    assert run.dividends == [
        {
            "instrument_id": "A",
            "record_session": DAYS[0].isoformat(),
            "ex_session": DAYS[1].isoformat(),
            "pay_session": DAYS[2].isoformat(),
            "amount_per_share": 100_000_000,
            "quantity": 100,
            "amount": 10_000_000_000,
            "paid": True,
        }
    ]
    assert json.loads(replay(run.save(tmp_path / "run"))) == run.raw


def test_record_close_is_retained_after_sale_and_after_end_of_sample(tmp_path: Path) -> None:
    from trademaster.backtest import CashDividend

    snap = Snapshot.create(
        tmp_path,
        sessions=DAYS,
        next_session=NEXT,
        instruments=[Instrument("A")],
        daily=[DailyPrice(d, "A", 10, 10, evidence="fixture") for d in DAYS],
        cash_dividends=[CashDividend("A", DAYS[0], DAYS[2], NEXT, Decimal("0.005"))],
    )
    run = backtest(
        snap,
        targets={DAYS[0]: {}},
        initial_account=InitialAccount(0, [InitialLot("A", 3, DAYS[0])]),
        policy=Policy(0, 0, 0, 0, 0, 0),
    )
    assert run.raw["fill_count"] == 1
    assert run.dividends[0]["quantity"] == 3
    assert run.dividends[0]["amount"] == 2_000_000  # 0.015 CNY rounds once to 0.02.
    assert run.dividends[0]["paid"] is False
    assert run.equity[-1] == {
        "session": DAYS[-1].isoformat(),
        "cash": 30,
        "market_value": 0,
        "dividend_receivable": Decimal("0.02"),
        "equity": Decimal("30.02"),
    }


def test_ex_date_buyer_and_target_use_the_ex_dividend_price(tmp_path: Path) -> None:
    from trademaster.backtest import CashDividend

    snap = Snapshot.create(
        tmp_path,
        sessions=DAYS,
        next_session=NEXT,
        instruments=[Instrument("A")],
        daily=[
            DailyPrice(d, "A", p, p, evidence="fixture")
            for d, p in zip(DAYS, (10, 9, 9, 9), strict=True)
        ],
        cash_dividends=[CashDividend("A", DAYS[0], DAYS[1], DAYS[1], 1)],
    )
    policy = Policy(0, 0, 0, 0, 0, 0)
    buyer = backtest(
        snap, targets={DAYS[0]: {"A": 1}}, initial_cash=900, policy=policy, detail=True
    )
    assert buyer.dividends == []
    assert [(f["session"], f["quantity"]) for f in buyer.fills] == [(DAYS[1].isoformat(), 100)]
    assert buyer.equity[-1]["equity"] == 900

    holder = backtest(
        snap,
        targets={DAYS[0]: {"A": Decimal("0.90")}},
        initial_account=InitialAccount(0, [InitialLot("A", 1000, DAYS[0])]),
        policy=policy,
        detail=True,
    )
    assert holder.attempts[0]["reserve"] == 1000 * 100_000_000
    assert not holder.fills  # 1,000 shares at 9 already meet the 90% target.
    assert all(d["equity"] == 10_000 for d in holder.equity)


@pytest.mark.parametrize(
    ("symbol", "record", "ex", "pay", "rate"),
    [
        ("B", DAYS[0], DAYS[1], DAYS[1], 1),
        ("A", DAYS[1], DAYS[0], DAYS[2], 1),
        ("A", DAYS[1], DAYS[1], DAYS[2], 1),
        ("A", DAYS[0], DAYS[2], DAYS[1], 1),
        ("A", DAYS[0], DAYS[0] + timedelta(days=1), DAYS[2], 1),
        ("A", DAYS[0], DAYS[1], DAYS[2], 0),
    ],
)
def test_snapshot_rejects_invalid_dividend(
    tmp_path: Path, symbol: str, record: date, ex: date, pay: date, rate: int
) -> None:
    from trademaster.backtest import CashDividend

    with pytest.raises(ValueError, match="cash dividend"):
        Snapshot.create(
            tmp_path,
            sessions=DAYS,
            next_session=NEXT,
            instruments=[Instrument("A")],
            daily=[],
            cash_dividends=[CashDividend(symbol, record, ex, pay, rate)],
        )
    assert not (tmp_path / "manifest.json").exists()


def test_history_cache_is_lazy_and_matches_uncached_reads(tmp_path: Path) -> None:
    weekly_snapshot(tmp_path)
    cached = Snapshot.open(tmp_path)
    uncached = Snapshot.open(tmp_path, history_cache=False)
    assert cached._history_cache == {}

    day = cached.day(DAYS[2])
    assert day.closes("A", limit=2) == (9.0, 9.0)
    assert set(cached._history_cache) == {("A", False)}  # only the symbol and series read

    for session in DAYS:
        for symbol in ("A", "B"):
            for adjusted in (False, True):
                for limit in (None, 1, 3, 10):
                    assert cached.day(session).closes(
                        symbol, adjusted=adjusted, limit=limit
                    ) == uncached.day(session).closes(symbol, adjusted=adjusted, limit=limit)
    assert uncached._history_cache == {}


def test_history_cache_keeps_missing_closes_as_none(tmp_path: Path) -> None:
    rows = [DailyPrice(d, "A", 10, 10, evidence="fixture") for d in DAYS]
    rows[1] = DailyPrice(DAYS[1], "A", None, None, "no", "no", evidence="halt", suspended=True)
    Snapshot.create(
        tmp_path, sessions=DAYS, next_session=NEXT, instruments=[Instrument("A")], daily=rows
    )
    snap = Snapshot.open(tmp_path)
    assert snap.day(DAYS[3]).closes("A") == (10.0, None, 10.0, 10.0)
    assert snap.day(DAYS[1]).closes("A", limit=1) == (None,)
