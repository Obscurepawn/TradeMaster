use time::{Duration, OffsetDateTime};
use tm_core::{
    AssetClass, Fixed8, InstrumentId, InstrumentSpec, MarketBar, MarketEventKind, MarketSnapshot,
    OrderIntent, PortfolioPosition, PortfolioView, Quantity, RejectionCode, SettlementPolicy, Side,
    SubmittedOrder, TradingStatus, ValidationView, Venue,
};
use tm_engine::{CnAshareOrderValidator, CnFeeSchedule, FeeSchedule, OrderValidator};

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

fn submitted(side: Side, quantity: i128, now: OffsetDateTime) -> SubmittedOrder {
    submitted_for("600000.SH", side, quantity, now)
}

fn submitted_for(
    instrument_id: &str,
    side: Side,
    quantity: i128,
    now: OffsetDateTime,
) -> SubmittedOrder {
    let intent = OrderIntent::try_new(
        "order-1",
        "signal-1",
        instrument_id,
        side,
        quantity,
        now - Duration::DAY,
        now,
    )
    .unwrap();
    SubmittedOrder::try_new(intent, now).unwrap()
}

fn view(
    now: OffsetDateTime,
    open_scaled: i128,
    status: TradingStatus,
    cash_scaled: i128,
    quantity: i128,
    sellable: i128,
) -> ValidationView {
    view_with_bar_time(
        now,
        now,
        open_scaled,
        status,
        cash_scaled,
        quantity,
        sellable,
    )
}

fn view_with_bar_time(
    now: OffsetDateTime,
    bar_time: OffsetDateTime,
    open_scaled: i128,
    status: TradingStatus,
    cash_scaled: i128,
    quantity: i128,
    sellable: i128,
) -> ValidationView {
    let bar = MarketBar {
        instrument_id: InstrumentId("600000.SH".to_owned()),
        event_time: bar_time,
        open: Fixed8::from_scaled(open_scaled).unwrap(),
        high: Fixed8::from_scaled(open_scaled).unwrap(),
        low: Fixed8::from_scaled(open_scaled).unwrap(),
        close: Fixed8::from_scaled(open_scaled).unwrap(),
        volume: Quantity::positive(10_000).unwrap(),
        up_limit: Fixed8::from_scaled(11 * UNIT).unwrap(),
        down_limit: Fixed8::from_scaled(9 * UNIT).unwrap(),
        trading_status: status,
        status_evidence_id: "status-object".to_owned(),
    };
    let market = MarketSnapshot::try_new(now, "a".repeat(64), vec![bar]).unwrap();
    let positions = if quantity == 0 {
        vec![]
    } else {
        vec![
            PortfolioPosition::try_new("600000.SH", quantity, sellable, quantity * open_scaled)
                .unwrap(),
        ]
    };
    let portfolio = PortfolioView::try_new(now, "b".repeat(64), cash_scaled, positions).unwrap();
    ValidationView::try_new(
        MarketEventKind::SessionOpen,
        instrument(),
        market,
        portfolio,
    )
    .unwrap()
}

fn fee_schedule() -> CnFeeSchedule {
    CnFeeSchedule::try_new(300, 5 * UNIT, 500, 10, 1_000_000, "cn-a-share-2025-v1").unwrap()
}

#[test]
fn fee_schedule_matches_small_account_hand_calculation() {
    let now = OffsetDateTime::UNIX_EPOCH + Duration::DAY;
    let buy = tm_core::AcceptedOrder::try_new(submitted(Side::Buy, 100, now), now).unwrap();
    let sell = tm_core::AcceptedOrder::try_new(submitted(Side::Sell, 100, now), now).unwrap();
    let price = Fixed8::from_scaled(10 * UNIT).unwrap();
    let fees = fee_schedule();

    let buy_quote = fees
        .quote(&instrument(), &buy, Quantity::positive(100).unwrap(), price)
        .unwrap();
    assert_eq!(buy_quote.commission().scaled(), 5 * UNIT);
    assert_eq!(buy_quote.tax().scaled(), 0);
    assert_eq!(buy_quote.transfer_fee().scaled(), 1_000_000);

    let sell_quote = fees
        .quote(
            &instrument(),
            &sell,
            Quantity::positive(100).unwrap(),
            price,
        )
        .unwrap();
    assert_eq!(sell_quote.commission().scaled(), 5 * UNIT);
    assert_eq!(sell_quote.tax().scaled(), 50_000_000);
    assert_eq!(sell_quote.transfer_fee().scaled(), 1_000_000);
    assert_eq!(fees.schedule_id(), "cn-a-share-2025-v1");

    let etf = InstrumentSpec::try_new(
        "510300.SH",
        AssetClass::Etf,
        Venue::Sse,
        "CNY",
        100,
        100_000,
        SettlementPolicy::T1,
    )
    .unwrap();
    let etf_sell =
        tm_core::AcceptedOrder::try_new(submitted_for("510300.SH", Side::Sell, 100, now), now)
            .unwrap();
    let etf_quote = fees
        .quote(&etf, &etf_sell, Quantity::positive(100).unwrap(), price)
        .unwrap();
    assert_eq!(etf_quote.tax().scaled(), 0);
    assert_eq!(etf_quote.transfer_fee().scaled(), 0);
}

#[test]
fn validator_accepts_tradable_lot_with_cash_and_sellable_position() {
    let now = OffsetDateTime::UNIX_EPOCH + Duration::DAY;
    let validator = CnAshareOrderValidator::try_new(fee_schedule(), 1_000).unwrap();
    assert!(
        validator
            .validate(
                &submitted(Side::Buy, 100, now),
                &view(now, 10 * UNIT, TradingStatus::Tradable, 2_000 * UNIT, 0, 0),
            )
            .is_ok()
    );
    assert!(
        validator
            .validate(
                &submitted(Side::Sell, 100, now),
                &view(now, 10 * UNIT, TradingStatus::Tradable, 0, 100, 100),
            )
            .is_ok()
    );
}

#[test]
fn validator_rejects_a_share_market_constraints_with_stable_codes() {
    let now = OffsetDateTime::UNIX_EPOCH + Duration::DAY;
    let validator = CnAshareOrderValidator::try_new(fee_schedule(), 1_000).unwrap();
    let cases = [
        (
            submitted(Side::Buy, 150, now),
            view(now, 10 * UNIT, TradingStatus::Tradable, 2_000 * UNIT, 0, 0),
            RejectionCode::InvalidQuantity,
        ),
        (
            submitted(Side::Buy, 100, now),
            view(now, 10 * UNIT, TradingStatus::Suspended, 2_000 * UNIT, 0, 0),
            RejectionCode::Suspended,
        ),
        (
            submitted(Side::Buy, 100, now),
            view(now, 11 * UNIT, TradingStatus::Tradable, 2_000 * UNIT, 0, 0),
            RejectionCode::LimitUp,
        ),
        (
            submitted(Side::Sell, 100, now),
            view(now, 9 * UNIT, TradingStatus::Tradable, 0, 100, 100),
            RejectionCode::LimitDown,
        ),
        (
            submitted(Side::Buy, 100, now),
            view(now, 10 * UNIT, TradingStatus::Tradable, 1_000 * UNIT, 0, 0),
            RejectionCode::InsufficientCash,
        ),
        (
            submitted(Side::Sell, 100, now),
            view(now, 10 * UNIT, TradingStatus::Tradable, 0, 100, 0),
            RejectionCode::InsufficientSellable,
        ),
    ];
    for (order, market, code) in cases {
        let rejection = validator.validate(&order, &market).unwrap_err();
        assert_eq!(rejection.code(), code);
        assert_eq!(rejection.event_time(), now);
    }
}

#[test]
fn validator_binds_exact_event_bar_instrument_and_slippage_cash() {
    let now = OffsetDateTime::UNIX_EPOCH + Duration::DAY;
    let validator = CnAshareOrderValidator::try_new(fee_schedule(), 1_000).unwrap();

    let stale = view_with_bar_time(
        now,
        now - Duration::DAY,
        10 * UNIT,
        TradingStatus::Tradable,
        2_000 * UNIT,
        0,
        0,
    );
    assert_eq!(
        validator
            .validate(&submitted(Side::Buy, 100, now), &stale)
            .unwrap_err()
            .code(),
        RejectionCode::MissingMarketData
    );

    let other = InstrumentSpec::try_new(
        "000001.SZ",
        AssetClass::Stock,
        Venue::Szse,
        "CNY",
        1,
        1_000_000,
        SettlementPolicy::T1,
    )
    .unwrap();
    let bars = vec![
        MarketBar {
            instrument_id: InstrumentId("000001.SZ".to_owned()),
            event_time: now,
            open: Fixed8::from_scaled(10 * UNIT).unwrap(),
            high: Fixed8::from_scaled(10 * UNIT).unwrap(),
            low: Fixed8::from_scaled(10 * UNIT).unwrap(),
            close: Fixed8::from_scaled(10 * UNIT).unwrap(),
            volume: Quantity::positive(10_000).unwrap(),
            up_limit: Fixed8::from_scaled(11 * UNIT).unwrap(),
            down_limit: Fixed8::from_scaled(9 * UNIT).unwrap(),
            trading_status: TradingStatus::Tradable,
            status_evidence_id: "other".to_owned(),
        },
        MarketBar {
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
            status_evidence_id: "target".to_owned(),
        },
    ];
    let market = MarketSnapshot::try_new(now, "c".repeat(64), bars).unwrap();
    let portfolio = PortfolioView::try_new(now, "d".repeat(64), 2_000 * UNIT, vec![]).unwrap();
    let mismatched = ValidationView::try_new(
        MarketEventKind::SessionOpen,
        instrument(),
        market,
        portfolio,
    )
    .unwrap();
    assert_eq!(other.buy_lot_size(), 1);
    assert_eq!(
        validator
            .validate(&submitted_for("000001.SZ", Side::Buy, 1, now), &mismatched,)
            .unwrap_err()
            .code(),
        RejectionCode::MissingMarketData
    );

    let gross_plus_fees = 1_005 * UNIT + 1_000_000;
    assert_eq!(
        validator
            .validate(
                &submitted(Side::Buy, 100, now),
                &view(
                    now,
                    10 * UNIT,
                    TradingStatus::Tradable,
                    gross_plus_fees,
                    0,
                    0,
                ),
            )
            .unwrap_err()
            .code(),
        RejectionCode::InsufficientCash
    );
}
