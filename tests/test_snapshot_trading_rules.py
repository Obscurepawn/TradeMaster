"""Per-rule settings preserve evidence checks and saved daily replay."""

import json
from dataclasses import replace
from datetime import date, datetime, time
from pathlib import Path
from typing import Any, Literal
from zoneinfo import ZoneInfo

import pytest
from trademaster.backtest import (
    BacktestDay,
    DailyPrice,
    InitialAccount,
    InitialLot,
    Instrument,
    Policy,
    Snapshot,
    backtest,
)
from trademaster.replay import replay

DAYS = (date(2024, 1, 5), date(2024, 1, 8), date(2024, 1, 9))
ZERO_COST = Policy(0, 0, 0, 0, 0, 0, 1)


@pytest.mark.parametrize("side", ["buy", "sell"])
def test_price_limit_rule_can_be_disabled_and_replayed(tmp_path: Path, side: str) -> None:
    blocked = DailyPrice(
        DAYS[1],
        "A",
        10,
        10,
        buy_at_open="no" if side == "buy" else "yes",
        sell_at_open="no" if side == "sell" else "yes",
        buy_reason="upper_limit" if side == "buy" else None,
        sell_reason="lower_limit" if side == "sell" else None,
        evidence="opening auction",
        buy_blocked_by_price_limit=side == "buy",
        sell_blocked_by_price_limit=side == "sell",
    )
    snapshot = Snapshot.create(
        tmp_path / "snapshot",
        sessions=DAYS[:2],
        next_session=DAYS[2],
        instruments=[Instrument("A")],
        daily=[DailyPrice(DAYS[0], "A", 10, 10, evidence="fixture"), blocked],
    )
    account = (
        InitialAccount(1000)
        if side == "buy"
        else InitialAccount(0, (InitialLot("A", 100, DAYS[0]),))
    )
    targets = {DAYS[0]: {"A": 1} if side == "buy" else {}}
    default = backtest(
        snapshot, targets=targets, initial_account=account, policy=ZERO_COST, detail=True
    )
    assert default.fills == []
    assert default.orders[0]["reason"] == ("upper_limit" if side == "buy" else "lower_limit")
    assert "enforce_price_limits" not in default.request["run"]["policy"]

    relaxed = backtest(
        snapshot,
        targets=targets,
        initial_account=account,
        policy=replace(ZERO_COST, enforce_price_limits=False),
        detail=True,
    )
    assert [(f["side"], f["quantity"]) for f in relaxed.fills] == [(side, 100)]
    assert relaxed.equity[-1]["equity"] == 1000
    assert relaxed.request["run"]["policy"]["enforce_price_limits"] is False
    request = relaxed.save(tmp_path / "run")
    assert json.loads(replay(request)) == relaxed.raw


@pytest.mark.parametrize(
    ("permission", "reason", "suspended", "marker", "opening", "expected_status", "expected"),
    [
        ("no", "upper_limit", False, False, 10, "completed", "upper_limit"),
        ("no", "other_restriction", False, False, 10, "completed", "other_restriction"),
        ("no", "upper_limit", True, True, 10, "completed", "suspended"),
        ("unknown", "unknown_auction", False, False, 10, "insufficient_data", "unknown_auction"),
        ("no", "upper_limit", False, True, None, "insufficient_data", "missing_open"),
    ],
)
def test_disabling_limits_preserves_other_opening_checks(
    tmp_path: Path,
    permission: Literal["yes", "no", "unknown"],
    reason: str,
    suspended: bool,
    marker: bool,
    opening: int | None,
    expected_status: str,
    expected: str,
) -> None:
    snapshot = Snapshot.create(
        tmp_path,
        sessions=DAYS[:2],
        next_session=DAYS[2],
        instruments=[Instrument("A")],
        daily=[
            DailyPrice(DAYS[0], "A", 10, 10, evidence="fixture"),
            DailyPrice(
                DAYS[1],
                "A",
                opening,
                10,
                buy_at_open=permission,
                buy_reason=reason,
                evidence="fixture",
                suspended=suspended,
                buy_blocked_by_price_limit=marker,
            ),
        ],
    )
    result = backtest(
        snapshot,
        targets={DAYS[0]: {"A": 1}},
        initial_cash=1000,
        policy=replace(ZERO_COST, enforce_price_limits=False),
        detail=True,
    )
    assert not result.fills
    assert result.raw["status"] == expected_status
    assert result.orders[0]["reason"] == expected


@pytest.mark.parametrize("side", ["buy", "sell"])
@pytest.mark.parametrize("permission", ["yes", "unknown"])
def test_price_limit_marker_requires_a_known_block(
    tmp_path: Path, side: str, permission: Literal["yes", "unknown"]
) -> None:
    row = DailyPrice(
        DAYS[0],
        "A",
        10,
        10,
        buy_at_open=permission if side == "buy" else "yes",
        sell_at_open=permission if side == "sell" else "yes",
        evidence="fixture",
        buy_blocked_by_price_limit=side == "buy",
        sell_blocked_by_price_limit=side == "sell",
    )
    with pytest.raises(ValueError, match="price.limit"):
        Snapshot.create(
            tmp_path,
            sessions=DAYS[:1],
            next_session=DAYS[1],
            instruments=[Instrument("A")],
            daily=[row],
        )


@pytest.mark.parametrize("value", [0, 1, "false", None])
def test_price_limit_settings_require_booleans(tmp_path: Path, value: Any) -> None:
    snapshot = Snapshot.create(
        tmp_path / "valid",
        sessions=DAYS[:1],
        next_session=DAYS[1],
        instruments=[Instrument("A")],
        daily=[DailyPrice(DAYS[0], "A", 10, 10, evidence="fixture")],
    )
    with pytest.raises(ValueError, match="enforce_price_limits.*bool"):
        backtest(
            snapshot,
            targets={},
            initial_cash=1000,
            policy=replace(ZERO_COST, enforce_price_limits=value),
        )
    with pytest.raises(ValueError, match="price.limit.*bool"):
        Snapshot.create(
            tmp_path / "invalid",
            sessions=DAYS[:1],
            next_session=DAYS[1],
            instruments=[Instrument("A")],
            daily=[
                DailyPrice(
                    DAYS[0], "A", 10, 10, evidence="fixture", buy_blocked_by_price_limit=value
                )
            ],
        )


def test_unmarked_daily_rows_keep_their_existing_shape(tmp_path: Path) -> None:
    snapshot = Snapshot.create(
        tmp_path,
        sessions=DAYS[:1],
        next_session=DAYS[1],
        instruments=[Instrument("A")],
        daily=[DailyPrice(DAYS[0], "A", 10, 10, evidence="fixture")],
    )
    row = json.loads(snapshot.market_path.read_bytes())["rows"][0]
    assert "buy_blocked_by_price_limit" not in row
    assert "sell_blocked_by_price_limit" not in row


def test_price_limit_markers_do_not_replace_opening_evidence(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="require evidence"):
        Snapshot.create(
            tmp_path,
            sessions=DAYS[:1],
            next_session=DAYS[1],
            instruments=[Instrument("A")],
            daily=[
                DailyPrice(DAYS[0], "A", 10, 10, buy_at_open="no", buy_blocked_by_price_limit=True)
            ],
        )


@pytest.mark.parametrize("action_gap", [False, True])
def test_disabling_limits_preserves_buy_cap_and_action_coverage(
    tmp_path: Path, action_gap: bool
) -> None:
    snapshot = Snapshot.create(
        tmp_path,
        sessions=DAYS[:2],
        next_session=DAYS[2],
        instruments=[Instrument("A")],
        daily=[
            DailyPrice(DAYS[0], "A", 10, 10, evidence="fixture"),
            DailyPrice(
                DAYS[1],
                "A",
                11,
                11,
                buy_at_open="no",
                buy_reason="upper_limit",
                evidence="fixture",
                corporate_action_gap=action_gap,
                buy_blocked_by_price_limit=True,
            ),
        ],
    )
    result = backtest(
        snapshot,
        targets={DAYS[0]: {"A": 1}},
        initial_cash=1000,
        policy=replace(ZERO_COST, enforce_price_limits=False),
        detail=True,
    )
    assert not result.fills
    assert result.orders[0]["reason"] == ("corporate_action_gap" if action_gap else "above_buy_cap")
    assert result.raw["status"] == ("insufficient_data" if action_gap else "completed")


@pytest.mark.parametrize(("settlement", "sellable"), [("t0", 7), ("t1", 0)])
def test_whole_share_usd_account_uses_explicit_rules_and_local_close(
    tmp_path: Path, settlement: Literal["t0", "t1"], sellable: int
) -> None:
    new_york = ZoneInfo("America/New_York")
    closes = {day: datetime.combine(day, time(16), new_york) for day in DAYS}
    snapshot = Snapshot.create(
        tmp_path,
        sessions=DAYS,
        next_session=date(2024, 1, 10),
        instruments=[
            Instrument(
                "EXAMPLE", venue="nyse", currency="USD", buy_lot_size=1, settlement=settlement
            )
        ],
        daily=[DailyPrice(day, "EXAMPLE", 10, 10, evidence="fixture") for day in DAYS],
        close_times=closes,
    )

    class RoundTrip:
        def on_close(self, day: BacktestDay) -> dict[str, int]:
            assert day.decision_at == closes[day.session]
            if day.session == DAYS[0]:
                return {"EXAMPLE": 1}
            if day.session == DAYS[1]:
                assert day.account.quantity("EXAMPLE") == 7
                assert day.account.sellable_quantity("EXAMPLE") == sellable
            return {}

    result = backtest(
        snapshot,
        RoundTrip(),
        initial_cash=75,
        policy=replace(ZERO_COST, enforce_price_limits=False),
        detail=True,
    )
    assert [(f["side"], f["quantity"]) for f in result.fills] == [("buy", 7), ("sell", 7)]
    assert result.equity[-1]["cash"] == 75
    assert result.summary["total_costs"] == 0
