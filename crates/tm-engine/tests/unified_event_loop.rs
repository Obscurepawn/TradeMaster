use time::{Duration, OffsetDateTime};
use tm_core::{
    AssetClass, Fixed8, InstrumentId, InstrumentSpec, IntentType, MarketBar, MarketEvent,
    MarketEventKind, MarketSnapshot, Quantity, SettlementPolicy, SignalRecord, TradingStatus,
    Venue,
};
use tm_engine::{
    CnAshareOrderValidator, CnFeeSchedule, DailyBarExecutionModel, DataError, EventLoop,
    EventSource, LotLedger, ScheduledSignalStrategy, TradingCalendar,
};

const UNIT: i128 = 100_000_000;

struct Events(std::vec::IntoIter<MarketEvent>);

impl EventSource for Events {
    fn next(&mut self) -> Result<Option<MarketEvent>, DataError> {
        Ok(self.0.next())
    }
}

#[derive(Clone, Debug)]
struct Calendar;

impl TradingCalendar for Calendar {
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

fn market_event(at: OffsetDateTime, kind: MarketEventKind) -> MarketEvent {
    let market = MarketSnapshot::try_new(
        at,
        "a".repeat(64),
        vec![MarketBar {
            instrument_id: InstrumentId("600000.SH".to_owned()),
            event_time: at,
            open: Fixed8::from_scaled(10 * UNIT).unwrap(),
            high: Fixed8::from_scaled(10 * UNIT).unwrap(),
            low: Fixed8::from_scaled(10 * UNIT).unwrap(),
            close: Fixed8::from_scaled(10 * UNIT).unwrap(),
            volume: Quantity::positive(10_000).unwrap(),
            up_limit: Fixed8::from_scaled(11 * UNIT).unwrap(),
            down_limit: Fixed8::from_scaled(9 * UNIT).unwrap(),
            trading_status: TradingStatus::Tradable,
            status_evidence_id: "status".to_owned(),
        }],
    )
    .unwrap();
    MarketEvent::try_new(at, kind, market).unwrap()
}

#[test]
fn scheduled_and_event_components_share_one_deterministic_runtime_chain() {
    let t0 = OffsetDateTime::UNIX_EPOCH;
    let day1_open = t0 + Duration::DAY;
    let day1_close = day1_open + Duration::HOUR;
    let day2_open = day1_open + Duration::DAY;
    let signals = vec![
        SignalRecord::try_new(
            "buy",
            "round-trip",
            "600000.SH",
            t0,
            day1_open,
            IntentType::Quantity,
            100 * UNIT,
            "buy",
            "a".repeat(64),
        )
        .unwrap(),
        SignalRecord::try_new(
            "sell",
            "round-trip",
            "600000.SH",
            day1_close,
            day2_open,
            IntentType::Quantity,
            0,
            "sell",
            "a".repeat(64),
        )
        .unwrap(),
    ];
    let strategy = ScheduledSignalStrategy::try_new(signals).unwrap();
    let fees = CnFeeSchedule::try_new(300, 5 * UNIT, 500, 10, 1_000_000, "cn-a-share-v1").unwrap();
    let validator = CnAshareOrderValidator::try_new(fees.clone(), 1_000).unwrap();
    let execution = DailyBarExecutionModel::try_new(vec![instrument()], fees, 1_000).unwrap();
    let ledger = LotLedger::try_new(2_000 * UNIT, t0, vec![instrument()], Calendar).unwrap();
    let events = Events(
        vec![
            market_event(day1_open, MarketEventKind::SessionOpen),
            market_event(day1_close, MarketEventKind::BarClose),
            market_event(day2_open, MarketEventKind::Settlement),
            market_event(day2_open, MarketEventKind::SessionOpen),
        ]
        .into_iter(),
    );
    let mut runtime = EventLoop::try_new(
        events,
        strategy,
        validator,
        execution,
        ledger,
        vec![instrument()],
    )
    .unwrap();

    let trace = runtime.run().unwrap();
    assert_eq!(trace.executions().len(), 2);
    assert_eq!(trace.rejections().len(), 0);
    assert_eq!(trace.ledger_effects().len(), 3);
    let final_account = trace.account_snapshots().last().unwrap();
    assert_eq!(
        final_account.nav().cash().scaled(),
        1_987 * UNIT + 48_000_000
    );
    assert!(final_account.positions().is_empty());

    let replay_ids = trace
        .orders()
        .iter()
        .map(|order| order.order_id())
        .collect::<Vec<_>>();
    assert_eq!(replay_ids, ["order:buy", "order:sell"]);
    assert_eq!(trace.signals().len(), 2);
    assert_eq!(
        trace
            .events()
            .iter()
            .map(|event| event.event_seq())
            .collect::<Vec<_>>(),
        [0, 1, 2, 3]
    );
}

#[test]
fn runtime_requires_settlement_before_next_open_and_rejects_unconsumed_signals() {
    let t0 = OffsetDateTime::UNIX_EPOCH;
    let day1_open = t0 + Duration::DAY;
    let day2_open = day1_open + Duration::DAY;
    let future_signal = SignalRecord::try_new(
        "future",
        "timing",
        "600000.SH",
        t0,
        day2_open,
        IntentType::Quantity,
        100 * UNIT,
        "future",
        "a".repeat(64),
    )
    .unwrap();
    let strategy = ScheduledSignalStrategy::try_new(vec![future_signal]).unwrap();
    let fees = CnFeeSchedule::try_new(300, 5 * UNIT, 500, 10, 1_000_000, "cn-a-share-v1").unwrap();
    let validator = CnAshareOrderValidator::try_new(fees.clone(), 1_000).unwrap();
    let execution = DailyBarExecutionModel::try_new(vec![instrument()], fees, 1_000).unwrap();
    let ledger = LotLedger::try_new(2_000 * UNIT, t0, vec![instrument()], Calendar).unwrap();
    let mut truncated = EventLoop::try_new(
        Events(vec![market_event(day1_open, MarketEventKind::SessionOpen)].into_iter()),
        strategy,
        validator,
        execution,
        ledger,
        vec![instrument()],
    )
    .unwrap();
    assert!(truncated.run().is_err());

    let buy = SignalRecord::try_new(
        "buy-only",
        "timing",
        "600000.SH",
        t0,
        day1_open,
        IntentType::Quantity,
        100 * UNIT,
        "buy",
        "a".repeat(64),
    )
    .unwrap();
    let strategy = ScheduledSignalStrategy::try_new(vec![buy]).unwrap();
    let fees = CnFeeSchedule::try_new(300, 5 * UNIT, 500, 10, 1_000_000, "cn-a-share-v1").unwrap();
    let validator = CnAshareOrderValidator::try_new(fees.clone(), 1_000).unwrap();
    let execution = DailyBarExecutionModel::try_new(vec![instrument()], fees, 1_000).unwrap();
    let ledger = LotLedger::try_new(2_000 * UNIT, t0, vec![instrument()], Calendar).unwrap();
    let mut missing_settlement = EventLoop::try_new(
        Events(
            vec![
                market_event(day1_open, MarketEventKind::SessionOpen),
                market_event(day1_open + Duration::HOUR, MarketEventKind::BarClose),
                market_event(day2_open, MarketEventKind::SessionOpen),
            ]
            .into_iter(),
        ),
        strategy,
        validator,
        execution,
        ledger,
        vec![instrument()],
    )
    .unwrap();
    assert!(missing_settlement.run().is_err());
}
