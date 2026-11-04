//! Cash distributions on raw prices. The opening auction precedes cash payment.
use super::*;
use crate::LedgerInstruments;

#[derive(Default)]
pub(super) struct DividendDay {
    pub(super) record: Vec<usize>,
    pub(super) ex: Vec<usize>,
    pub(super) pay: Vec<usize>,
}

pub(super) fn schedule(
    data: &SnapshotData,
    instruments: &LedgerInstruments,
    index: &BTreeMap<&str, usize>,
) -> Result<BTreeMap<usize, DividendDay>, SnapshotError> {
    let mut calendar = BTreeMap::<usize, DividendDay>::new();
    let mut keys = BTreeSet::new();
    for (i, d) in data.cash_dividends.iter().enumerate() {
        for day in [&d.record_session, &d.ex_session, &d.pay_session] {
            open_time(day)?;
            if day < &data.next_session && !index.contains_key(day.as_str()) {
                return Err(invalid("cash dividend falls outside the trading calendar"));
            }
        }
        let Some(&record) = index.get(d.record_session.as_str()) else {
            return Err(invalid("cash dividend needs an in-sample record session"));
        };
        if d.record_session >= d.ex_session || d.ex_session > d.pay_session {
            return Err(invalid(
                "cash dividend needs record_session < ex_session <= pay_session",
            ));
        }
        if record >= data.sessions.len()
            || !instruments.contains_key(&d.instrument_id)
            || d.amount_per_share <= 0
            || !keys.insert((&d.ex_session, &d.instrument_id))
        {
            return Err(invalid("invalid or duplicate cash dividend"));
        }
        calendar.entry(record).or_default().record.push(i);
        if let Some(&ex) = index.get(d.ex_session.as_str()) {
            calendar.entry(ex).or_default().ex.push(i);
        }
        if let Some(&pay) = index.get(d.pay_session.as_str()) {
            calendar.entry(pay).or_default().pay.push(i);
        }
    }
    Ok(calendar)
}

impl SnapshotSession<'_> {
    fn shares(&self, symbol: &str) -> Result<i128, SnapshotError> {
        self.ledger
            .lots
            .values()
            .filter(|lot| lot.instrument_id == symbol)
            .try_fold(0, |total, lot| add(total, lot.quantity))
    }

    pub(super) fn accrue_dividends(&mut self, day: &DividendDay) -> Result<(), SnapshotError> {
        for &i in &day.ex {
            let d = &self.market.data.cash_dividends[i];
            // Use the ex-dividend reference for sizing, including suspended carried marks.
            let rate = i128::from(d.amount_per_share);
            if let Some(mark) = self.marks.get_mut(&d.instrument_id) {
                if *mark <= rate {
                    return Err(invalid(
                        "cash dividend must be below the previous closing price",
                    ));
                }
                *mark -= rate;
                if let Some(last) = self.ledger.last_prices.get_mut(&d.instrument_id) {
                    *last = *mark;
                }
            }
            let quantity = self.recorded_shares[i];
            if quantity == 0 {
                continue;
            }
            // Round the entire distribution once, to the nearest fen (half up).
            let amount = mul(add(mul(quantity, rate)?, CENT / 2)? / CENT, CENT)?;
            self.dividend_receivable = add(self.dividend_receivable, amount)?;
            self.dividend_entries[i] = Some(self.out.dividends.len());
            self.out.dividends.push(DividendEntitlement {
                action: d.clone(),
                quantity,
                amount,
                paid: false,
            });
        }
        Ok(())
    }

    pub(super) fn close_dividends(&mut self, day: &DividendDay) -> Result<(), SnapshotError> {
        for &i in &day.record {
            self.recorded_shares[i] =
                self.shares(&self.market.data.cash_dividends[i].instrument_id)?;
        }
        for &i in &day.pay {
            if let Some(entry) = self.dividend_entries[i] {
                let dividend = &mut self.out.dividends[entry];
                self.ledger.cash_scaled = add(self.ledger.cash_scaled, dividend.amount)?;
                self.dividend_receivable -= dividend.amount;
                dividend.paid = true;
            }
        }
        Ok(())
    }
}
