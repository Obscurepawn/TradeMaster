//! Prices and trading-day indexes shared by runs on the same data snapshot.
use super::*;
use crate::{LedgerInstruments, index_instruments};

pub(super) type Rows<'a> = BTreeMap<&'a str, &'a DailyRow>;

pub struct PreparedMarket<'a> {
    pub(super) instruments: LedgerInstruments,
    pub(super) data: &'a SnapshotData,
    pub(super) rows: Vec<Rows<'a>>,
    pub(super) index: BTreeMap<&'a str, usize>,
    pub(super) calendar: Sessions,
    pub(super) dividends: BTreeMap<usize, dividends::DividendDay>,
}

impl<'a> PreparedMarket<'a> {
    pub fn new(data: &'a SnapshotData) -> Result<Self, SnapshotError> {
        if data.sessions.is_empty() || data.instruments.is_empty() {
            return Err(invalid("need sessions and instruments"));
        }
        let dates: Vec<_> = data
            .sessions
            .iter()
            .chain([&data.next_session])
            .map(|day| open_time(day))
            .collect::<Result<_, _>>()?;
        if dates.windows(2).any(|pair| pair[0] >= pair[1]) {
            return Err(invalid("sessions and next_session must increase"));
        }
        let index: BTreeMap<_, _> = data
            .sessions
            .iter()
            .chain([&data.next_session])
            .enumerate()
            .map(|(i, day)| (day.as_str(), i))
            .collect();
        if data
            .instruments
            .iter()
            .any(|spec| spec.currency() != data.instruments[0].currency())
        {
            return Err(invalid("all instruments must use the same currency"));
        }
        let instruments = index_instruments(data.instruments.clone())?;
        let mut rows = vec![Rows::new(); data.sessions.len()];
        for row in &data.rows {
            if (row.buy_blocked_by_price_limit && !matches!(row.buy_at_open, Permission::No))
                || (row.sell_blocked_by_price_limit && !matches!(row.sell_at_open, Permission::No))
            {
                return Err(invalid(
                    "a price-limit marker requires a known blocked permission",
                ));
            }
            let day = index
                .get(row.session.as_str())
                .and_then(|&i| rows.get_mut(i))
                .ok_or_else(|| invalid("row outside sampled sessions"))?;
            if !instruments.contains_key(row.instrument_id.as_str())
                || day.insert(&row.instrument_id, row).is_some()
                || row.open.is_some_and(|p| p <= 0)
                || row.close.is_some_and(|p| p <= 0)
            {
                return Err(invalid(format!(
                    "invalid or duplicate row: {} {}",
                    row.session, row.instrument_id
                )));
            }
        }
        let dividends = dividends::schedule(data, &instruments, &index)?;
        Ok(Self {
            dividends,
            instruments,
            data,
            rows,
            index,
            calendar: Sessions(Arc::new(dates)),
        })
    }

    pub fn sessions(&self) -> &[String] {
        &self.data.sessions
    }
}
