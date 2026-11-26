//! The daily snapshot engine posts through `LotLedger::{apply_unaudited, settle_unaudited}`
//! and values accounts with `LotLedger::valuation`. These must reach exactly the state, errors
//! and valuations of the audited `Ledger` path that event runs persist.
use super::*;
use time::Duration;
use tm_core::{AssetClass, Fill, OrderIntent, SubmittedOrder};

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

/// Deterministic xorshift so the sequence needs no extra dependency.
struct Rng(u64);

impl Rng {
    fn below(&mut self, bound: u64) -> u64 {
        self.0 ^= self.0 << 13;
        self.0 ^= self.0 >> 7;
        self.0 ^= self.0 << 17;
        self.0 % bound
    }
}

const UNIT: i128 = 100_000_000;
const IDS: [&str; 3] = ["A", "B", "C"];

fn ledger() -> LotLedger<NextDay> {
    let instruments = IDS
        .iter()
        .enumerate()
        .map(|(i, id)| {
            let settlement = if i == 1 {
                SettlementPolicy::T0
            } else {
                SettlementPolicy::T1
            };
            InstrumentSpec::try_new(
                *id,
                AssetClass::Stock,
                Venue::Sse,
                "CNY",
                100,
                1,
                settlement,
            )
            .unwrap()
        })
        .collect();
    LotLedger::try_new(
        50_000 * UNIT,
        OffsetDateTime::UNIX_EPOCH,
        instruments,
        NextDay,
    )
    .unwrap()
}

fn execution(rng: &mut Rng, step: u64, now: OffsetDateTime) -> OrderExecution {
    let side = if rng.below(2) == 0 {
        Side::Buy
    } else {
        Side::Sell
    };
    let order_id = format!("order-{step}");
    let fills = (0..1 + rng.below(3))
        .map(|i| {
            let quantity = i128::from(100 * (1 + rng.below(8)));
            let price = i128::from(5 + rng.below(20)) * UNIT;
            // Occasionally reuse a fill id to exercise duplicate-lot rejection.
            let fill_id = if rng.below(25) == 0 {
                "order-0-fill-0".to_owned()
            } else {
                format!("{order_id}-fill-{i}")
            };
            let fee = i128::from(rng.below(4)) * UNIT;
            Fill::try_new(fill_id, &order_id, now, quantity, price, fee, 0, 0, 0).unwrap()
        })
        .collect::<Vec<_>>();
    let quantity = fills.iter().map(|fill| fill.quantity().units()).sum();
    let instrument = IDS[usize::try_from(rng.below(3)).unwrap()];
    let signal_time = now - Duration::HOUR;
    let intent = OrderIntent::try_new(
        &order_id,
        "signal",
        instrument,
        side,
        quantity,
        signal_time,
        now,
    )
    .unwrap();
    let accepted =
        AcceptedOrder::try_new(SubmittedOrder::try_new(intent, now).unwrap(), now).unwrap();
    OrderExecution::try_new(accepted, fills, OrderTerminal::Filled).unwrap()
}

#[test]
fn unaudited_transitions_match_the_audited_ledger() {
    // Outcomes seen: unlocking settlements, posted buys, posted sells, rejected executions.
    let mut seen = [0_u32; 4];
    for seed in 1..=40_u64 {
        let mut rng = Rng(seed.wrapping_mul(0x9e37_79b9_7f4a_7c15));
        let mut audited = ledger();
        let mut unaudited = ledger();
        let mut now = OffsetDateTime::UNIX_EPOCH;
        for step in 0..120 {
            if rng.below(4) == 0 {
                now += Duration::DAY;
                let event = SettlementEvent { event_time: now };
                let expected = audited
                    .settle(&event)
                    .map(|effect| seen[0] += u32::from(!effect.lot_changes().is_empty()))
                    .map_err(|e| e.to_string());
                let actual = unaudited
                    .settle_unaudited(&event)
                    .map_err(|e| e.to_string());
                assert_eq!(actual, expected, "seed {seed} step {step}");
            } else {
                let result = execution(&mut rng, step, now);
                let side = result.accepted_order().submitted_order().intent().side();
                let expected = audited
                    .apply(&result)
                    .map(|_| seen[usize::from(side == Side::Sell) + 1] += 1)
                    .map_err(|e| {
                        seen[3] += 1;
                        e.to_string()
                    });
                let actual = unaudited
                    .apply_unaudited(&result)
                    .map_err(|e| e.to_string());
                assert_eq!(actual, expected, "seed {seed} step {step}");
            }
            assert_eq!(
                format!("{unaudited:?}"),
                format!("{audited:?}"),
                "seed {seed} step {step}"
            );
            let marks = audited.last_prices.clone();
            let account = audited.snapshot_with_marks(now, &marks).unwrap();
            let valuation = unaudited.valuation(now, &marks).unwrap();
            assert_eq!(&valuation.nav, account.nav(), "seed {seed} step {step}");
            assert_eq!(valuation.positions, account.positions());
        }
    }
    assert!(seen.iter().all(|&count| count > 20), "{seen:?}");
}
