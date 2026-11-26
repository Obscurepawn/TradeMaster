//! Characterization of persisted ledger state: `account-state/v1` hashes, ledger-effect
//! contents, and daily snapshot results must stay byte-identical across internal changes
//! to how `LotLedger` stages and validates transitions.
use serde_json::{Value, json};
use time::{Duration, OffsetDateTime};
use tm_core::{
    AcceptedOrder, AssetClass, Expiry, Fill, Fixed8, InstrumentId, InstrumentSpec, MarketBar,
    MarketSnapshot, OrderExecution, OrderIntent, OrderTerminal, Quantity, SettlementEvent,
    SettlementPolicy, Side, SubmittedOrder, TradingStatus, Venue,
};
use tm_engine::snapshot::{SnapshotData, SnapshotRun, run_snapshot};
use tm_engine::{DataError, Ledger, LotLedger, TradingCalendar};

const UNIT: i128 = 100_000_000;

#[derive(Clone, Debug)]
struct NextDay;

impl TradingCalendar for NextDay {
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

/// 64-bit FNV-1a, enough to pin a serialized record without embedding it verbatim.
fn fingerprint(value: &impl serde::Serialize) -> String {
    let text = serde_json::to_string(value).unwrap();
    let hash = text.bytes().fold(0xcbf2_9ce4_8422_2325_u64, |hash, byte| {
        (hash ^ u64::from(byte)).wrapping_mul(0x0000_0100_0000_01b3)
    });
    format!("{hash:016x}")
}

fn instrument(id: &str, settlement: SettlementPolicy) -> InstrumentSpec {
    InstrumentSpec::try_new(
        id,
        AssetClass::Stock,
        Venue::Sse,
        "CNY",
        100,
        1_000_000,
        settlement,
    )
    .unwrap()
}

fn day(n: i64) -> OffsetDateTime {
    OffsetDateTime::UNIX_EPOCH + Duration::days(n)
}

/// One accepted order with the given `(quantity, price)` fills at `now`.
fn execution(
    order_id: &str,
    instrument_id: &str,
    side: Side,
    now: OffsetDateTime,
    fills: &[(i128, i128)],
) -> OrderExecution {
    let quantity = fills.iter().map(|fill| fill.0).sum::<i128>().max(100);
    let intent = OrderIntent::try_new(
        order_id,
        "signal",
        instrument_id,
        side,
        quantity,
        now - Duration::HOUR,
        now,
    )
    .unwrap();
    let accepted =
        AcceptedOrder::try_new(SubmittedOrder::try_new(intent, now).unwrap(), now).unwrap();
    let fills = fills
        .iter()
        .enumerate()
        .map(|(i, &(qty, price))| {
            let notional = qty * price;
            Fill::try_new(
                format!("{order_id}-fill-{i}"),
                order_id,
                now,
                qty,
                price,
                (notional / 3_000).max(5 * UNIT),
                if side == Side::Sell {
                    notional / 2_000
                } else {
                    0
                },
                notional / 100_000,
                notional / 10_000,
            )
            .unwrap()
        })
        .collect::<Vec<_>>();
    let terminal = if fills.is_empty() {
        OrderTerminal::Expired(Expiry::try_new(order_id, now, quantity, "no_bar").unwrap())
    } else {
        OrderTerminal::Filled
    };
    OrderExecution::try_new(accepted, fills, terminal).unwrap()
}

fn market(now: OffsetDateTime, a: i128, b: i128) -> MarketSnapshot {
    let bar = |id: &str, price: i128| MarketBar {
        instrument_id: InstrumentId(id.to_owned()),
        event_time: now,
        open: Fixed8::from_scaled(price).unwrap(),
        high: Fixed8::from_scaled(price).unwrap(),
        low: Fixed8::from_scaled(price).unwrap(),
        close: Fixed8::from_scaled(price).unwrap(),
        volume: Quantity::positive(1_000_000).unwrap(),
        up_limit: None,
        down_limit: None,
        trading_status: TradingStatus::Tradable,
        status_evidence_id: "status".to_owned(),
    };
    MarketSnapshot::try_new(now, "a".repeat(64), vec![bar("A", a), bar("B", b)]).unwrap()
}

#[test]
fn lot_ledger_transitions_keep_pinned_state_hashes_and_effects() {
    let mut ledger = LotLedger::try_new(
        100_000 * UNIT,
        day(0),
        vec![
            instrument("A", SettlementPolicy::T1),
            instrument("B", SettlementPolicy::T0),
        ],
        NextDay,
    )
    .unwrap();
    let mut pins: Vec<(String, String, String, String)> = vec![];
    let mut record = |label: &str, effect: &tm_core::LedgerEffect| {
        pins.push((
            label.to_owned(),
            effect.before_state_hash().to_owned(),
            effect.after_state_hash().to_owned(),
            fingerprint(effect),
        ));
    };

    // Two-fill buy: two locked lots and two posting groups in one transition.
    let buy_a = execution(
        "buy-a",
        "A",
        Side::Buy,
        day(1),
        &[(300, 10 * UNIT), (200, 11 * UNIT)],
    );
    record("multi-fill buy", &ledger.apply(&buy_a).unwrap());
    let buy_b = execution("buy-b", "B", Side::Buy, day(1), &[(100, 20 * UNIT)]);
    record("t0 buy", &ledger.apply(&buy_b).unwrap());
    let expired = execution("expired", "A", Side::Buy, day(1), &[]);
    record("no-op execution", &ledger.apply(&expired).unwrap());
    let locked = execution("locked", "A", Side::Sell, day(1), &[(100, 10 * UNIT)]);
    let error = ledger.apply(&locked).unwrap_err().to_string();
    assert_eq!(
        error,
        "ledger invariant violated: insufficient sellable lots"
    );
    record(
        "settlement unlock",
        &ledger
            .settle(&SettlementEvent { event_time: day(2) })
            .unwrap(),
    );
    let buy_a2 = execution("buy-a2", "A", Side::Buy, day(2), &[(100, 12 * UNIT)]);
    record("buy after unlock", &ledger.apply(&buy_a2).unwrap());
    // FIFO: the whole first lot and part of the second; the new lot stays locked.
    let partial = execution("sell-a", "A", Side::Sell, day(2), &[(400, 13 * UNIT)]);
    record("partial FIFO sell", &ledger.apply(&partial).unwrap());
    let oversell = execution("oversell", "A", Side::Sell, day(2), &[(300, 13 * UNIT)]);
    let error = ledger.apply(&oversell).unwrap_err().to_string();
    assert_eq!(
        error,
        "ledger invariant violated: insufficient sellable lots"
    );
    record(
        "empty settlement",
        &ledger
            .settle(&SettlementEvent { event_time: day(2) })
            .unwrap(),
    );
    record(
        "second unlock",
        &ledger
            .settle(&SettlementEvent { event_time: day(3) })
            .unwrap(),
    );
    let exit_a = execution(
        "exit-a",
        "A",
        Side::Sell,
        day(3),
        &[(150, 9 * UNIT), (50, 9 * UNIT)],
    );
    record("full FIFO exit", &ledger.apply(&exit_a).unwrap());
    let exit_b = execution("exit-b", "B", Side::Sell, day(3), &[(100, 21 * UNIT)]);
    record("t0 exit", &ledger.apply(&exit_b).unwrap());
    let closing = ledger
        .snapshot(&market(day(3), 9 * UNIT, 21 * UNIT))
        .unwrap();

    let expected = [
        (
            "multi-fill buy",
            "0ac9f9587ecb632fea7b88ee2c8b4df3b61326ba02834adade38cad35afaa6d4",
            "880d6e8de3f65bfb9020e8ad40b9f7ba421d2fe73d89e0f01be904737d8d7d92",
            "243e4372999e7f9c",
        ),
        (
            "t0 buy",
            "880d6e8de3f65bfb9020e8ad40b9f7ba421d2fe73d89e0f01be904737d8d7d92",
            "8a8c86ee73661b93c1797575e60e83edf8bdf3e6784abed25c1b21195b2f34c2",
            "8c99daabc1184ec2",
        ),
        (
            "no-op execution",
            "8a8c86ee73661b93c1797575e60e83edf8bdf3e6784abed25c1b21195b2f34c2",
            "8a8c86ee73661b93c1797575e60e83edf8bdf3e6784abed25c1b21195b2f34c2",
            "bbf2f2ee5265f071",
        ),
        (
            "settlement unlock",
            "8a8c86ee73661b93c1797575e60e83edf8bdf3e6784abed25c1b21195b2f34c2",
            "236ebd1f3a52ac0af13a904710cbfd2dad2693d84fb4fac8caac96e33c82f1ef",
            "5a6ed8fd9fa15d3b",
        ),
        (
            "buy after unlock",
            "236ebd1f3a52ac0af13a904710cbfd2dad2693d84fb4fac8caac96e33c82f1ef",
            "c4b01def9b015a547183d6e1f4ea99f23948884775a2564ef781dcfd47ec9605",
            "2a7825c205166efe",
        ),
        (
            "partial FIFO sell",
            "c4b01def9b015a547183d6e1f4ea99f23948884775a2564ef781dcfd47ec9605",
            "f5c9ea3ae3b2d5d841f271eaefd94c60d2453c54f80864ad4ce3dd1de8dd1ee6",
            "5f7fa31ffdecca35",
        ),
        (
            "empty settlement",
            "f5c9ea3ae3b2d5d841f271eaefd94c60d2453c54f80864ad4ce3dd1de8dd1ee6",
            "f5c9ea3ae3b2d5d841f271eaefd94c60d2453c54f80864ad4ce3dd1de8dd1ee6",
            "f607400f8be10b13",
        ),
        (
            "second unlock",
            "f5c9ea3ae3b2d5d841f271eaefd94c60d2453c54f80864ad4ce3dd1de8dd1ee6",
            "69c39758a1c11edfbd95921bcdbd98d450a19cd000ec2a1a7c01fa379b1735dd",
            "51a6baa62923c1ad",
        ),
        (
            "full FIFO exit",
            "69c39758a1c11edfbd95921bcdbd98d450a19cd000ec2a1a7c01fa379b1735dd",
            "8d48c5d0c1d5abce97c6ba907eedd91b492c84ecfb1b6859a9d64f03fc45f61b",
            "8e23bce673f180f4",
        ),
        (
            "t0 exit",
            "8d48c5d0c1d5abce97c6ba907eedd91b492c84ecfb1b6859a9d64f03fc45f61b",
            "1ffc0b97d518e3dd98451bafc2b14eb8eb3fabba0f1ae2b010996e92d1c03c61",
            "da57188bd4dca06c",
        ),
    ];
    let actual = pins
        .iter()
        .map(|(label, before, after, effect)| {
            (
                label.as_str(),
                before.as_str(),
                after.as_str(),
                effect.as_str(),
            )
        })
        .collect::<Vec<_>>();
    assert_eq!(actual, expected, "{actual:#?}");
    assert_eq!(
        closing.state_hash(),
        "1ffc0b97d518e3dd98451bafc2b14eb8eb3fabba0f1ae2b010996e92d1c03c61",
        "closing account"
    );
}

fn daily_scenario() -> (Value, Value) {
    const U: i64 = 100_000_000;
    let days = [
        "2024-03-04",
        "2024-03-05",
        "2024-03-06",
        "2024-03-07",
        "2024-03-08",
        "2024-03-11",
        "2024-03-12",
        "2024-03-13",
    ];
    let mut instruments = vec![];
    let mut rows = vec![];
    for (id, settlement, base) in [("A", "t1", 10), ("B", "t0", 20), ("C", "t1", 5)] {
        instruments.push(
            json!({"instrument_id":id,"asset_class":"stock","venue":"sse",
            "currency":"CNY","buy_lot_size":100,"tick_size":U/100,"settlement":settlement}),
        );
        for (i, day) in days.iter().enumerate() {
            let drift = [0, -3, 2, -4, 1, -2, 3, -1][i];
            let price = base * U + drift * U / 10;
            rows.push(
                json!({"session":day,"instrument_id":id,"open":price,"close":price + U / 20,
                "buy_at_open":"yes","sell_at_open":"yes","evidence":"characterization"}),
            );
        }
    }
    let target = |decision: usize, weights: Value| {
        json!({"batch_id":format!("t{decision}"),"decision_session":days[decision],
            "eligible_session":days[decision + 1],"weights":weights})
    };
    (
        json!({"sessions":days,"next_session":"2024-03-14","instruments":instruments,"rows":rows,
            "cash_dividends":[{"instrument_id":"A","record_session":days[2],"ex_session":days[3],
                "pay_session":days[4],"amount_per_share":U / 5}]}),
        json!({"initial_cash":2_000_000 * U,
            "initial_lots":[{"instrument_id":"C","quantity":30_000,"sellable_session":days[1]}],
            "targets":[
                target(0, json!({"A":30_000_000,"B":20_000_000})),
                target(1, json!({"A":45_000_000,"B":25_000_000,"C":5_000_000})),
                target(2, json!({"A":50_000_000,"B":30_000_000})),
                target(3, json!({"A":20_000_000,"B":40_000_000,"C":10_000_000})),
                target(5, json!({"B":10_000_000})),
                target(6, json!({})),
            ],
            "policy":{"commission_ppm":300,"minimum_commission":5 * U,"sell_stamp_duty_ppm":500,
                "sse_transfer_fee_ppm":10,"slippage_ppm":100,"buy_cap_ppm":20_000,"max_attempts":2},
            "detail":true}),
    )
}

#[test]
fn daily_snapshot_lot_accounting_keeps_pinned_results() {
    let (data, run) = daily_scenario();
    let data: SnapshotData = serde_json::from_value(data).unwrap();
    let run: SnapshotRun = serde_json::from_value(run).unwrap();
    let result = serde_json::to_value(run_snapshot(&data, &run).unwrap()).unwrap();
    assert_eq!(result["status"], "completed");
    assert_eq!(result["dividends"][0]["paid"], true);
    assert!(
        result["fills"]
            .as_array()
            .unwrap()
            .iter()
            .any(|fill| fill["side"] == "sell"),
        "the scenario must sell lots"
    );
    assert_eq!(
        (
            result["fill_count"].clone(),
            result["daily"].as_array().unwrap().last().unwrap()["equity"].clone(),
            fingerprint(&result),
        ),
        (
            json!(13),
            json!(224_798_337_400_000_i64),
            "ec6a6657bd56665a".to_owned()
        ),
        "{result:#}"
    );
}
