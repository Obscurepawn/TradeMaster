//! Prices and trading-day indexes shared by runs on the same data snapshot.
//!
//! Rows are regrouped by session into compressed sparse rows (CSR): `offsets[day]..
//! offsets[day + 1]` addresses that session's rows, sorted by instrument index, so a lookup is
//! a binary search within one session and iteration walks a contiguous slice.
use super::rows::{
    BUY_BLOCKED_BY_PRICE_LIMIT, MarketRows, NON_POSITIVE_PRICE, RowView,
    SELL_BLOCKED_BY_PRICE_LIMIT, buy_permission, sell_permission, view,
};
use super::*;
use crate::{LedgerInstruments, index_instruments};
use std::ops::Range;

/// No session or instrument: the interned name is outside the calendar or universe.
const NONE: u32 = u32::MAX;

pub struct PreparedMarket {
    pub(super) instruments: LedgerInstruments,
    /// Instrument IDs in sorted order; a position here is an instrument index.
    ids: Vec<String>,
    /// Snapshot position of each instrument index (the snapshot keeps its own order).
    positions: Vec<u32>,
    sessions: Vec<String>,
    next_session: String,
    pub(super) index: BTreeMap<String, usize>,
    pub(super) calendar: Sessions,
    pub(super) dividends: BTreeMap<usize, dividends::DividendDay>,
    pub(super) cash_dividends: Vec<CashDividend>,
    offsets: Vec<u32>,
    instrument: Vec<u32>,
    open: Vec<i64>,
    close: Vec<i64>,
    flags: Vec<u16>,
    buy_reason: Vec<u32>,
    sell_reason: Vec<u32>,
    reasons: Vec<String>,
}

/// One cell of the instrument-major session grid (see [`PriceGrid`]).
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct GridCell {
    /// `snapshot position × sessions + session index`.
    pub cell: usize,
    pub close: Option<i64>,
    /// The row carries an open, a close, or a `yes` opening permission.
    pub observed: bool,
}

impl PreparedMarket {
    pub fn new(data: SnapshotData) -> Result<Self, SnapshotError> {
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
            .map(|(i, day)| (day.clone(), i))
            .collect();
        if data
            .instruments
            .iter()
            .any(|spec| spec.currency() != data.instruments[0].currency())
        {
            return Err(invalid("all instruments must use the same currency"));
        }
        let instruments = index_instruments(data.instruments.clone())?;
        let ids: Vec<String> = instruments.keys().cloned().collect();
        let mut positions = vec![0; ids.len()];
        for (position, spec) in data.instruments.iter().enumerate() {
            // Instruments were checked unique above, so every ID has one index.
            let k = ids
                .binary_search(&spec.instrument_id().0)
                .map_err(|_| invalid("unknown instrument"))?;
            positions[k] = u32::try_from(position).map_err(|_| SnapshotError::Overflow)?;
        }
        let order = order_rows(&data.rows, &index, data.sessions.len(), &ids)?;
        let dividends = dividends::schedule(&data, &instruments, &index)?;
        let SnapshotData {
            sessions,
            next_session,
            rows,
            cash_dividends,
            ..
        } = data;
        let instrument_index: Vec<u32> = rows
            .instruments
            .iter()
            .map(|id| ids.binary_search(id).map_or(NONE, |k| k as u32))
            .collect();
        let MarketRows {
            instrument,
            open,
            close,
            flags,
            buy_reason,
            sell_reason,
            reasons,
            ..
        } = rows;
        let RowOrder { offsets, rows } = order;
        let order = rows.as_deref();
        let instrument = arrange(
            instrument
                .into_iter()
                .map(|id| instrument_index[id as usize])
                .collect(),
            order,
        );
        Ok(Self {
            open: arrange(open, order),
            close: arrange(close, order),
            flags: arrange(flags, order),
            buy_reason: arrange(buy_reason, order),
            sell_reason: arrange(sell_reason, order),
            instrument,
            reasons,
            offsets,
            dividends,
            instruments,
            ids,
            positions,
            sessions,
            next_session,
            cash_dividends,
            index,
            calendar: Sessions(Arc::new(dates)),
        })
    }

    pub fn sessions(&self) -> &[String] {
        &self.sessions
    }

    pub fn next_session(&self) -> &str {
        &self.next_session
    }

    /// Instrument IDs in snapshot order, the row order of [`PriceGrid`].
    pub fn instrument_ids(&self) -> Vec<&str> {
        let mut ids = vec![""; self.ids.len()];
        for (id, &position) in self.ids.iter().zip(&self.positions) {
            ids[position as usize] = id;
        }
        ids
    }

    /// Index of an instrument in sorted ID order.
    pub(super) fn instrument_index(&self, id: &str) -> Option<usize> {
        self.ids
            .binary_search_by(|probe| probe.as_str().cmp(id))
            .ok()
    }

    pub(super) fn instrument_count(&self) -> usize {
        self.ids.len()
    }

    /// Positions of the rows of session `day`, sorted by instrument index.
    pub(super) fn day(&self, day: usize) -> Range<usize> {
        self.offsets[day] as usize..self.offsets[day + 1] as usize
    }

    /// The instrument index and contents of the row at `position`.
    pub(super) fn row(&self, position: usize) -> (usize, RowView<'_>) {
        let reason = |id: u32| (id != 0).then(|| self.reasons[id as usize - 1].as_str());
        (
            self.instrument[position] as usize,
            view(
                self.flags[position],
                self.open[position],
                self.close[position],
                [
                    reason(self.buy_reason[position]),
                    reason(self.sell_reason[position]),
                ],
            ),
        )
    }

    /// The row of instrument index `k` on session `day`, if one was published.
    pub(super) fn find(&self, day: usize, k: usize) -> Option<RowView<'_>> {
        let range = self.day(day);
        let k = u32::try_from(k).ok()?;
        let found = self.instrument[range.clone()].binary_search(&k).ok()?;
        Some(self.row(range.start + found).1)
    }

    /// The row of `id` on session `day`, if the instrument is known and has one.
    pub(super) fn lookup(&self, day: usize, id: &str) -> Option<RowView<'_>> {
        self.find(day, self.instrument_index(id)?)
    }

    /// Every published row as a cell of the instrument-major session grid.
    pub fn grid_cells(&self) -> impl Iterator<Item = GridCell> + '_ {
        let sessions = self.sessions.len();
        (0..sessions).flat_map(move |day| {
            self.day(day).map(move |position| {
                let (k, row) = self.row(position);
                GridCell {
                    cell: self.positions[k] as usize * sessions + day,
                    close: row.close,
                    observed: row.observed(),
                }
            })
        })
    }

    /// Lays out raw closes and listing evidence instrument-major (`instruments × sessions`,
    /// in snapshot instrument order) so read-only research views reuse this parse.
    pub fn price_grid(&self) -> PriceGrid {
        let cells = self.ids.len() * self.sessions.len();
        let mut grid = PriceGrid {
            closes: vec![0; cells],
            observed: vec![0; cells],
        };
        for cell in self.grid_cells() {
            grid.closes[cell.cell] = cell.close.unwrap_or(0);
            grid.observed[cell.cell] = u8::from(cell.observed);
        }
        grid
    }
}

/// Instrument-major session grid exported from a prepared market.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct PriceGrid {
    /// Fixed-point raw closes; `0` marks a missing close because valid closes are positive.
    pub closes: Vec<i64>,
    /// `1` when a row carries an open, a close, or a `yes` opening permission.
    pub observed: Vec<u8>,
}

/// Session offsets and, unless the input was already sorted, the row permutation.
struct RowOrder {
    offsets: Vec<u32>,
    rows: Option<Vec<u32>>,
}

/// Puts a column in session order; an already sorted column is moved as is.
fn arrange<T: Copy>(column: Vec<T>, order: Option<&[u32]>) -> Vec<T> {
    match order {
        None => column,
        Some(order) => order.iter().map(|&row| column[row as usize]).collect(),
    }
}

/// Validate rows in input order and sort them by `(session, instrument)`.
///
/// The reported error is the first input row that fails, as if rows were checked one by
/// one: a price-limit marker without a blocked permission, a session outside the sample,
/// then an unknown instrument, a repeated `(session, instrument)` key, or a non-positive
/// price. Repeats are found after sorting: within a run of equal keys sorted by input
/// position, the second row is the one a sequential check would reject.
fn order_rows(
    rows: &MarketRows,
    index: &BTreeMap<String, usize>,
    sessions: usize,
    ids: &[String],
) -> Result<RowOrder, SnapshotError> {
    let day_of: Vec<u32> = rows
        .sessions
        .iter()
        .map(|day| {
            index
                .get(day)
                .filter(|&&i| i < sessions)
                .map_or(NONE, |&i| i as u32)
        })
        .collect();
    let index_of: Vec<u32> = rows
        .instruments
        .iter()
        .map(|id| ids.binary_search(id).map_or(NONE, |k| k as u32))
        .collect();
    let describe = |row: usize| {
        invalid(format!(
            "invalid or duplicate row: {} {}",
            rows.sessions[rows.session[row] as usize],
            rows.instruments[rows.instrument[row] as usize]
        ))
    };
    let key = |row: usize| {
        (
            day_of[rows.session[row] as usize],
            index_of[rows.instrument[row] as usize],
        )
    };
    let mut failure = None;
    let mut sorted = true;
    for (row, &flags) in rows.flags.iter().enumerate() {
        let (day, k) = key(row);
        if (flags & BUY_BLOCKED_BY_PRICE_LIMIT != 0 && buy_permission(flags) != Permission::No)
            || (flags & SELL_BLOCKED_BY_PRICE_LIMIT != 0
                && sell_permission(flags) != Permission::No)
        {
            failure = Some((
                row,
                invalid("a price-limit marker requires a known blocked permission"),
            ));
        } else if day == NONE {
            failure = Some((row, invalid("row outside sampled sessions")));
        } else if k == NONE || flags & NON_POSITIVE_PRICE != 0 {
            failure = Some((row, describe(row)));
        }
        if failure.is_some() {
            break;
        }
        sorted &= row == 0 || key(row - 1) < (day, k);
    }
    let checked = failure.as_ref().map_or(rows.len(), |(row, _)| *row);
    let mut offsets = vec![0u32; sessions + 1];
    for row in 0..checked {
        offsets[key(row).0 as usize + 1] += 1;
    }
    for day in 0..sessions {
        offsets[day + 1] += offsets[day];
    }
    let order = (!sorted).then(|| {
        let mut next = offsets.clone();
        let mut order = vec![0u32; checked];
        for row in 0..checked {
            let slot = &mut next[key(row).0 as usize];
            order[*slot as usize] = row as u32;
            *slot += 1;
        }
        for day in 0..sessions {
            order[offsets[day] as usize..offsets[day + 1] as usize]
                .sort_unstable_by_key(|&row| (key(row as usize).1, row));
        }
        order
    });
    let repeated = order.as_ref().and_then(|order| {
        (0..sessions)
            .flat_map(|day| {
                order[offsets[day] as usize..offsets[day + 1] as usize]
                    .windows(2)
                    .filter(|pair| key(pair[0] as usize).1 == key(pair[1] as usize).1)
                    .map(|pair| pair[1] as usize)
            })
            .min()
    });
    if let Some(row) = repeated {
        return Err(describe(row));
    }
    if let Some((_, error)) = failure {
        return Err(error);
    }
    Ok(RowOrder {
        offsets,
        rows: order,
    })
}
