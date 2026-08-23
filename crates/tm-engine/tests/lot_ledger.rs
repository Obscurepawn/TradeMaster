use time::{Duration, OffsetDateTime};
use tm_core::{
    AcceptedOrder, AssetClass, Fill, InstrumentId, InstrumentSpec, MarketBar, MarketSnapshot,
    OrderExecution, OrderIntent, OrderTerminal, Quantity, SettlementEvent, SettlementPolicy, Side,
    SubmittedOrder, TradingStatus, Venue,
};
use tm_engine::{DataError, Ledger, LotLedger, TradingCalendar};

const UNIT: i128 = 100_000_000;

#[derive(Clone, Debug)]
struct TestCalendar;

impl TradingCalendar for TestCalendar {
    fn next_session_open(
        &self,
        _venue: Venue,
        after: OffsetDateTime,
    ) -> Result<OffsetDateTime, DataError> {
        Ok(after + Duration::DAY)
    }

    fn session_key(&self, _venue: Venue, at: OffsetDateTime) -> Result<String, DataError> {
        Ok(at.unix_timestamp().to_string())
    }
}

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

fn execution(side: Side, now: OffsetDateTime, costs: (i128, i128, i128, i128)) -> OrderExecution {
    let intent = OrderIntent::try_new(
        format!("order-{side:?}"),
        "signal",
        "600000.SH",
        side,
        100,
        now - Duration::DAY,
        now,
    )
    .unwrap();
    let accepted =
        AcceptedOrder::try_new(SubmittedOrder::try_new(intent, now).unwrap(), now).unwrap();
    let fill = Fill::try_new(
        format!("fill-{side:?}"),
        accepted.order_id(),
        now,
        100,
        10 * UNIT,
        costs.0,
        costs.1,
        costs.2,
        costs.3,
    )
    .unwrap();
    OrderExecution::try_new(accepted, vec![fill], OrderTerminal::Filled).unwrap()
}

fn market(now: OffsetDateTime) -> MarketSnapshot {
    MarketSnapshot::try_new(
        now,
        "a".repeat(64),
        vec![MarketBar {
            instrument_id: InstrumentId("600000.SH".to_owned()),
            event_time: now,
            open: tm_core::Fixed8::from_scaled(10 * UNIT).unwrap(),
            high: tm_core::Fixed8::from_scaled(10 * UNIT).unwrap(),
            low: tm_core::Fixed8::from_scaled(10 * UNIT).unwrap(),
            close: tm_core::Fixed8::from_scaled(10 * UNIT).unwrap(),
            volume: Quantity::positive(10_000).unwrap(),
            up_limit: tm_core::Fixed8::from_scaled(11 * UNIT).unwrap(),
            down_limit: tm_core::Fixed8::from_scaled(9 * UNIT).unwrap(),
            trading_status: TradingStatus::Tradable,
            status_evidence_id: "status".to_owned(),
        }],
    )
    .unwrap()
}

#[test]
fn lot_ledger_enforces_t1_unlock_fifo_sale_and_balanced_cash() {
    let t0 = OffsetDateTime::UNIX_EPOCH;
    let buy_time = t0 + Duration::DAY;
    let sellable_time = buy_time + Duration::DAY;
    let mut ledger =
        LotLedger::try_new(2_000 * UNIT, t0, vec![instrument()], TestCalendar).unwrap();

    let buy = execution(Side::Buy, buy_time, (5 * UNIT, 0, 1_000_000, UNIT));
    let buy_effect = ledger.apply(&buy).unwrap();
    assert_eq!(buy_effect.posting_groups().len(), 1);
    assert_eq!(buy_effect.lot_changes().len(), 1);
    let after_buy = ledger.snapshot(&market(buy_time)).unwrap();
    assert_eq!(after_buy.nav().cash().scaled(), 993 * UNIT + 99_000_000);
    assert_eq!(after_buy.positions()[0].quantity().units(), 100);
    assert_eq!(after_buy.positions()[0].sellable_quantity().units(), 0);
    assert_eq!(after_buy.lots()[0].sellable_at(), sellable_time);

    let premature_sell = execution(
        Side::Sell,
        buy_time,
        (5 * UNIT, 50_000_000, 1_000_000, UNIT),
    );
    assert!(ledger.apply(&premature_sell).is_err());
    let unchanged = ledger.snapshot(&market(buy_time)).unwrap();
    assert_eq!(unchanged.state_hash(), after_buy.state_hash());
    assert_eq!(unchanged.nav().cash(), after_buy.nav().cash());

    let settlement = ledger
        .settle(&SettlementEvent {
            event_time: sellable_time,
        })
        .unwrap();
    assert_eq!(settlement.lot_changes().len(), 1);
    let unlocked = ledger.snapshot(&market(sellable_time)).unwrap();
    assert_eq!(unlocked.positions()[0].sellable_quantity().units(), 100);

    let sell = execution(
        Side::Sell,
        sellable_time,
        (5 * UNIT, 50_000_000, 1_000_000, UNIT),
    );
    let sell_effect = ledger.apply(&sell).unwrap();
    assert_eq!(sell_effect.posting_groups().len(), 1);
    let after_sell = ledger.snapshot(&market(sellable_time)).unwrap();
    assert_eq!(after_sell.nav().cash().scaled(), 1_987 * UNIT + 48_000_000);
    assert!(after_sell.positions().is_empty());
    assert!(after_sell.lots().is_empty());
}
