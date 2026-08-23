use time::{Duration, OffsetDateTime};
use tm_core::{
    AcceptedOrder, AssetClass, Fixed8, InstrumentId, InstrumentSpec, MarketBar, MarketEvent,
    MarketEventKind, MarketSnapshot, OrderIntent, OrderTerminal, Quantity, SettlementPolicy, Side,
    SubmittedOrder, TradingStatus, Venue,
};
use tm_engine::{CnFeeSchedule, DailyBarExecutionModel, ExecutionModel};

const UNIT: i128 = 100_000_000;

fn instrument() -> InstrumentSpec {
    InstrumentSpec::try_new(
        "600000.SH",
        AssetClass::Stock,
        Venue::Sse,
        "CNY",
        100,
        1_000_000,
        SettlementPolicy::T1,
    )
    .unwrap()
}

fn accepted(now: OffsetDateTime) -> AcceptedOrder {
    let intent = OrderIntent::try_new(
        "order-1",
        "signal-1",
        "600000.SH",
        Side::Buy,
        100,
        now - Duration::DAY,
        now,
    )
    .unwrap();
    AcceptedOrder::try_new(SubmittedOrder::try_new(intent, now).unwrap(), now).unwrap()
}

fn event(now: OffsetDateTime, include_bar: bool) -> MarketEvent {
    let bars = if include_bar {
        vec![MarketBar {
            instrument_id: InstrumentId("600000.SH".to_owned()),
            event_time: now,
            open: Fixed8::from_scaled(10 * UNIT).unwrap(),
            high: Fixed8::from_scaled(10 * UNIT).unwrap(),
            low: Fixed8::from_scaled(10 * UNIT).unwrap(),
            close: Fixed8::from_scaled(10 * UNIT).unwrap(),
            volume: Quantity::positive(10_000).unwrap(),
            up_limit: Fixed8::from_scaled(11 * UNIT).unwrap(),
            down_limit: Fixed8::from_scaled(9 * UNIT).unwrap(),
            trading_status: TradingStatus::Tradable,
            status_evidence_id: "status".to_owned(),
        }]
    } else {
        vec![]
    };
    let market = MarketSnapshot::try_new(now, "a".repeat(64), bars).unwrap();
    MarketEvent::try_new(now, MarketEventKind::SessionOpen, market).unwrap()
}

fn model() -> DailyBarExecutionModel<CnFeeSchedule> {
    let fees =
        CnFeeSchedule::try_new(300, 5 * UNIT, 500, 10, 1_000_000, "cn-a-share-2025-v1").unwrap();
    DailyBarExecutionModel::try_new(vec![instrument()], fees, 1_000).unwrap()
}

#[test]
fn daily_execution_fills_at_open_with_exact_fees_and_slippage_cost() {
    let now = OffsetDateTime::UNIX_EPOCH + Duration::DAY;
    let accepted = accepted(now);
    let batch = model()
        .execute(&event(now, true), std::slice::from_ref(&accepted))
        .unwrap();
    let execution = &batch.executions()[0];
    let fill = &execution.fills()[0];

    assert_eq!(fill.quantity().units(), 100);
    assert_eq!(fill.price().scaled(), 10 * UNIT);
    assert_eq!(fill.commission().scaled(), 5 * UNIT);
    assert_eq!(fill.tax().scaled(), 0);
    assert_eq!(fill.transfer_fee().scaled(), 1_000_000);
    assert_eq!(fill.slippage().scaled(), UNIT);
    assert_eq!(execution.terminal(), &OrderTerminal::Filled);
}

#[test]
fn daily_execution_expires_accepted_order_when_event_has_no_bar() {
    let now = OffsetDateTime::UNIX_EPOCH + Duration::DAY;
    let accepted = accepted(now);
    let batch = model()
        .execute(&event(now, false), std::slice::from_ref(&accepted))
        .unwrap();
    let execution = &batch.executions()[0];

    assert!(execution.fills().is_empty());
    match execution.terminal() {
        OrderTerminal::Expired(expiry) => {
            assert_eq!(expiry.remaining_quantity().units(), 100);
            assert_eq!(expiry.event_time(), now);
        }
        OrderTerminal::Filled => panic!("missing bar cannot fill"),
    }
}

#[test]
fn daily_execution_expires_stale_bar_instead_of_filling_it() {
    let now = OffsetDateTime::UNIX_EPOCH + Duration::DAY;
    let accepted = accepted(now);
    let stale_market = MarketSnapshot::try_new(
        now,
        "b".repeat(64),
        vec![MarketBar {
            instrument_id: InstrumentId("600000.SH".to_owned()),
            event_time: now - Duration::DAY,
            open: Fixed8::from_scaled(10 * UNIT).unwrap(),
            high: Fixed8::from_scaled(10 * UNIT).unwrap(),
            low: Fixed8::from_scaled(10 * UNIT).unwrap(),
            close: Fixed8::from_scaled(10 * UNIT).unwrap(),
            volume: Quantity::positive(10_000).unwrap(),
            up_limit: Fixed8::from_scaled(11 * UNIT).unwrap(),
            down_limit: Fixed8::from_scaled(9 * UNIT).unwrap(),
            trading_status: TradingStatus::Tradable,
            status_evidence_id: "stale".to_owned(),
        }],
    )
    .unwrap();
    let stale_event =
        MarketEvent::try_new(now, MarketEventKind::SessionOpen, stale_market).unwrap();
    let batch = model()
        .execute(&stale_event, std::slice::from_ref(&accepted))
        .unwrap();
    assert!(batch.executions()[0].fills().is_empty());
}
