use time::{Duration, OffsetDateTime};
use tm_core::{
    AssetClass, Fixed8, InstrumentId, InstrumentSpec, IntentType, MarketBar, MarketEventKind,
    MarketSnapshot, PortfolioPosition, PortfolioView, Quantity, SettlementPolicy, Side,
    SignalRecord, StrategyView, TradingStatus, Venue,
};
use tm_engine::{ScheduledSignalStrategy, StrategyAdapter};

const UNIT: i128 = 100_000_000;

fn view(now: OffsetDateTime, kind: MarketEventKind) -> StrategyView {
    let market = MarketSnapshot::try_new(
        now,
        "a".repeat(64),
        vec![MarketBar {
            instrument_id: InstrumentId("600000.SH".to_owned()),
            event_time: now,
            open: Fixed8::from_scaled(10 * UNIT).unwrap(),
            high: Fixed8::from_scaled(10 * UNIT).unwrap(),
            low: Fixed8::from_scaled(10 * UNIT).unwrap(),
            close: Fixed8::from_scaled(10 * UNIT).unwrap(),
            volume: Quantity::positive(1_000).unwrap(),
            up_limit: Fixed8::from_scaled(11 * UNIT).unwrap(),
            down_limit: Fixed8::from_scaled(9 * UNIT).unwrap(),
            trading_status: TradingStatus::Tradable,
            status_evidence_id: "status".to_owned(),
        }],
    )
    .unwrap();
    let portfolio = PortfolioView::try_new(now, "b".repeat(64), 10_000 * UNIT, vec![]).unwrap();
    StrategyView::try_new(kind, market, vec![], portfolio).unwrap()
}

fn target_weight_view(now: OffsetDateTime) -> StrategyView {
    let bars = [("000001.SZ", 10), ("600000.SH", 20)]
        .into_iter()
        .map(|(instrument_id, open)| MarketBar {
            instrument_id: InstrumentId(instrument_id.to_owned()),
            event_time: now,
            open: Fixed8::from_scaled(open * UNIT).unwrap(),
            high: Fixed8::from_scaled(open * UNIT).unwrap(),
            low: Fixed8::from_scaled(open * UNIT).unwrap(),
            close: Fixed8::from_scaled(open * UNIT).unwrap(),
            volume: Quantity::positive(10_000).unwrap(),
            up_limit: Fixed8::from_scaled(open * 11 * UNIT / 10).unwrap(),
            down_limit: Fixed8::from_scaled(open * 9 * UNIT / 10).unwrap(),
            trading_status: TradingStatus::Tradable,
            status_evidence_id: "status".to_owned(),
        })
        .collect();
    let market = MarketSnapshot::try_new(now, "a".repeat(64), bars).unwrap();
    let portfolio = PortfolioView::try_new(
        now,
        "b".repeat(64),
        10_000 * UNIT,
        vec![PortfolioPosition::try_new("000001.SZ", 100, 100, 1_000 * UNIT).unwrap()],
    )
    .unwrap();
    StrategyView::try_new(MarketEventKind::SessionOpen, market, vec![], portfolio).unwrap()
}

fn target_weight_instruments() -> Vec<InstrumentSpec> {
    [("000001.SZ", Venue::Szse), ("600000.SH", Venue::Sse)]
        .into_iter()
        .map(|(instrument_id, venue)| {
            InstrumentSpec::try_new(
                instrument_id,
                AssetClass::Stock,
                venue,
                "CNY",
                100,
                1_000_000,
                SettlementPolicy::T1,
            )
            .unwrap()
        })
        .collect()
}

#[test]
fn scheduled_quantity_signal_waits_for_open_and_is_consumed_once() {
    let close = OffsetDateTime::UNIX_EPOCH + Duration::DAY;
    let next_open = close + Duration::DAY;
    let signal = SignalRecord::try_new(
        "signal-1",
        "timing",
        "600000.SH",
        close,
        next_open,
        IntentType::Quantity,
        100 * UNIT,
        "buy",
        "a".repeat(64),
    )
    .unwrap();
    let mut strategy = ScheduledSignalStrategy::try_new(vec![signal]).unwrap();

    assert!(
        strategy
            .on_event(&view(close, MarketEventKind::BarClose))
            .unwrap()
            .is_empty()
    );
    let orders = strategy
        .on_event(&view(next_open, MarketEventKind::SessionOpen))
        .unwrap();
    assert_eq!(orders.len(), 1);
    assert_eq!(orders[0].order_id(), "order:signal-1");
    assert_eq!(orders[0].side(), Side::Buy);
    assert_eq!(orders[0].quantity().units(), 100);
    assert_eq!(orders[0].eligible_execution_time(), next_open);
    assert!(
        strategy
            .on_event(&view(next_open, MarketEventKind::SessionOpen))
            .unwrap()
            .is_empty()
    );
}

#[test]
fn scheduled_strategy_rejects_target_weight_and_fractional_quantity() {
    let close = OffsetDateTime::UNIX_EPOCH + Duration::DAY;
    let next_open = close + Duration::DAY;
    let target_weight = SignalRecord::try_new(
        "weight",
        "cross-section",
        "600000.SH",
        close,
        next_open,
        IntentType::TargetWeight,
        UNIT / 2,
        "weight",
        "a".repeat(64),
    )
    .unwrap();
    assert!(ScheduledSignalStrategy::try_new(vec![target_weight]).is_err());

    let fractional = SignalRecord::try_new(
        "fractional",
        "timing",
        "600000.SH",
        close,
        next_open,
        IntentType::Quantity,
        100 * UNIT + 1,
        "fractional",
        "a".repeat(64),
    )
    .unwrap();
    assert!(ScheduledSignalStrategy::try_new(vec![fractional]).is_err());
}

#[test]
fn multiple_due_targets_use_virtual_position_deltas() {
    let close = OffsetDateTime::UNIX_EPOCH + Duration::DAY;
    let next_open = close + Duration::DAY;
    let signals = [("first", 100), ("second", 200)]
        .into_iter()
        .map(|(signal_id, target)| {
            SignalRecord::try_new(
                signal_id,
                "timing",
                "600000.SH",
                close,
                next_open,
                IntentType::Quantity,
                target * UNIT,
                "target",
                "a".repeat(64),
            )
            .unwrap()
        })
        .collect();
    let mut strategy = ScheduledSignalStrategy::try_new(signals).unwrap();
    let orders = strategy
        .on_event(&view(next_open, MarketEventKind::SessionOpen))
        .unwrap();

    assert_eq!(
        orders
            .iter()
            .map(|order| order.quantity().units())
            .collect::<Vec<_>>(),
        [100, 100]
    );
}

#[test]
fn target_weight_batch_uses_open_nav_lots_and_emits_sells_before_buys() {
    let close = OffsetDateTime::UNIX_EPOCH + Duration::DAY;
    let next_open = close + Duration::DAY;
    let signals = [
        ("target-a", "000001.SZ", 0),
        ("target-b", "600000.SH", UNIT),
    ]
    .into_iter()
    .map(|(signal_id, instrument_id, weight)| {
        SignalRecord::try_new(
            signal_id,
            "industry-fundamental-top5",
            instrument_id,
            close,
            next_open,
            IntentType::TargetWeight,
            weight,
            "semiannual target",
            "a".repeat(64),
        )
        .unwrap()
    })
    .collect();
    let mut strategy =
        ScheduledSignalStrategy::try_new_with_instruments(signals, target_weight_instruments())
            .unwrap();

    let orders = strategy.on_event(&target_weight_view(next_open)).unwrap();

    assert_eq!(orders.len(), 2);
    assert_eq!(orders[0].instrument_id().0, "000001.SZ");
    assert_eq!(orders[0].side(), Side::Sell);
    assert_eq!(orders[0].quantity().units(), 100);
    assert_eq!(orders[1].instrument_id().0, "600000.SH");
    assert_eq!(orders[1].side(), Side::Buy);
    assert_eq!(orders[1].quantity().units(), 500);
    assert!(orders[0].order_id() < orders[1].order_id());
}

#[test]
fn target_weight_execution_order_is_independent_of_signal_ids() {
    let close = OffsetDateTime::UNIX_EPOCH + Duration::DAY;
    let next_open = close + Duration::DAY;
    let make = |signal_id: &str, instrument_id: &str| {
        SignalRecord::try_new(
            signal_id,
            "industry-fundamental",
            instrument_id,
            close,
            next_open,
            IntentType::TargetWeight,
            UNIT / 2,
            "same target portfolio",
            "a".repeat(64),
        )
        .unwrap()
    };
    let mut first = ScheduledSignalStrategy::try_new_with_instruments(
        vec![make("a", "600000.SH"), make("z", "000001.SZ")],
        target_weight_instruments(),
    )
    .unwrap();
    let mut second = ScheduledSignalStrategy::try_new_with_instruments(
        vec![make("z", "600000.SH"), make("a", "000001.SZ")],
        target_weight_instruments(),
    )
    .unwrap();

    let first_orders = first.on_event(&target_weight_view(next_open)).unwrap();
    let second_orders = second.on_event(&target_weight_view(next_open)).unwrap();
    let economic = |orders: &[tm_core::OrderIntent]| {
        orders
            .iter()
            .map(|order| {
                (
                    order.instrument_id().0.clone(),
                    order.side(),
                    order.quantity().units(),
                )
            })
            .collect::<Vec<_>>()
    };

    assert_eq!(economic(&first_orders), economic(&second_orders));
    assert_eq!(
        economic(&first_orders),
        vec![
            ("000001.SZ".to_owned(), Side::Buy, 400),
            ("600000.SH".to_owned(), Side::Buy, 200),
        ]
    );
}

#[test]
fn zero_weight_exit_only_batch_can_retry_a_failed_rebalance_sale() {
    let close = OffsetDateTime::UNIX_EPOCH + Duration::DAY;
    let retry_open = close + Duration::DAY * 2;
    let signal = SignalRecord::try_new(
        "retry-exit-a",
        "industry-fundamental-top5",
        "000001.SZ",
        close,
        retry_open,
        IntentType::TargetWeight,
        0,
        "retry semiannual exit",
        "a".repeat(64),
    )
    .unwrap();
    let mut strategy = ScheduledSignalStrategy::try_new_with_instruments(
        vec![signal],
        target_weight_instruments(),
    )
    .unwrap();

    let orders = strategy.on_event(&target_weight_view(retry_open)).unwrap();

    assert_eq!(orders.len(), 1);
    assert_eq!(orders[0].instrument_id().0, "000001.SZ");
    assert_eq!(orders[0].side(), Side::Sell);
    assert_eq!(orders[0].quantity().units(), 100);
}
