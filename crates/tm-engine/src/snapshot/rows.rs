//! Compact daily rows. Each wire row is decoded exactly as before (`DailyRow`, so field
//! validation and serde error text are unchanged) and immediately reduced to fixed-width
//! columns: interned session, instrument and reason identifiers, prices with `0` for absent
//! (valid prices are positive), and one flag word for permissions and markers.
use super::wire::{DailyRow, Permission};
use serde::de::{Deserialize, Deserializer, Error, SeqAccess, Visitor};
use std::collections::HashMap;
use std::fmt;

const BUY_SHIFT: u16 = 0;
const SELL_SHIFT: u16 = 2;
const PERMISSION_MASK: u16 = 0b11;
pub(super) const SUSPENDED: u16 = 1 << 4;
pub(super) const CORPORATE_ACTION_GAP: u16 = 1 << 5;
pub(super) const BUY_BLOCKED_BY_PRICE_LIMIT: u16 = 1 << 6;
pub(super) const SELL_BLOCKED_BY_PRICE_LIMIT: u16 = 1 << 7;
/// The row carries non-empty evidence; the engine never reads the evidence text itself.
pub(super) const DOCUMENTED: u16 = 1 << 8;
/// The row supplied a non-positive open or close, which preparation rejects.
pub(super) const NON_POSITIVE_PRICE: u16 = 1 << 9;

fn permission_bits(permission: Permission) -> u16 {
    match permission {
        Permission::Yes => 0,
        Permission::No => 1,
        Permission::Unknown => 2,
    }
}

fn permission(bits: u16) -> Permission {
    match bits & PERMISSION_MASK {
        0 => Permission::Yes,
        1 => Permission::No,
        _ => Permission::Unknown,
    }
}

pub(super) fn buy_permission(flags: u16) -> Permission {
    permission(flags >> BUY_SHIFT)
}

pub(super) fn sell_permission(flags: u16) -> Permission {
    permission(flags >> SELL_SHIFT)
}

/// Assigns dense identifiers to strings in first-seen order.
#[derive(Default)]
struct Interner {
    ids: HashMap<String, u32>,
    names: Vec<String>,
}

impl Interner {
    fn intern<E: Error>(&mut self, name: String) -> Result<u32, E> {
        if let Some(&id) = self.ids.get(&name) {
            return Ok(id);
        }
        let id = u32::try_from(self.names.len()).map_err(|_| E::custom("too many market names"))?;
        self.names.push(name.clone());
        self.ids.insert(name, id);
        Ok(id)
    }
}

/// Market rows in input order, as decoded from the `rows` array of a snapshot market.
#[derive(Default)]
pub struct MarketRows {
    pub(super) session: Vec<u32>,
    pub(super) instrument: Vec<u32>,
    pub(super) open: Vec<i64>,
    pub(super) close: Vec<i64>,
    pub(super) flags: Vec<u16>,
    /// `0` is no reason; `k` names `reasons[k - 1]`.
    pub(super) buy_reason: Vec<u32>,
    pub(super) sell_reason: Vec<u32>,
    pub(super) sessions: Vec<String>,
    pub(super) instruments: Vec<String>,
    pub(super) reasons: Vec<String>,
}

impl MarketRows {
    pub fn len(&self) -> usize {
        self.flags.len()
    }

    pub fn is_empty(&self) -> bool {
        self.flags.is_empty()
    }
}

impl fmt::Debug for MarketRows {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.debug_struct("MarketRows")
            .field("rows", &self.len())
            .field("sessions", &self.sessions.len())
            .field("instruments", &self.instruments.len())
            .finish()
    }
}

#[derive(Default)]
struct Builder {
    rows: MarketRows,
    sessions: Interner,
    instruments: Interner,
    reasons: Interner,
}

impl Builder {
    fn reason<E: Error>(&mut self, reason: Option<String>) -> Result<u32, E> {
        reason.map_or(Ok(0), |reason| {
            let id = self.reasons.intern(reason)?;
            id.checked_add(1)
                .ok_or_else(|| E::custom("too many market names"))
        })
    }

    fn push<E: Error>(&mut self, row: DailyRow) -> Result<(), E> {
        if self.rows.len() >= u32::MAX as usize {
            return Err(E::custom("too many market rows"));
        }
        let positive = |price: Option<i64>| price.is_none_or(|p| p > 0);
        let mut flags = (permission_bits(row.buy_at_open) << BUY_SHIFT)
            | (permission_bits(row.sell_at_open) << SELL_SHIFT);
        for (set, flag) in [
            (row.suspended, SUSPENDED),
            (row.corporate_action_gap, CORPORATE_ACTION_GAP),
            (row.buy_blocked_by_price_limit, BUY_BLOCKED_BY_PRICE_LIMIT),
            (row.sell_blocked_by_price_limit, SELL_BLOCKED_BY_PRICE_LIMIT),
            (row.evidence.is_some_and(|s| !s.is_empty()), DOCUMENTED),
            (
                !positive(row.open) || !positive(row.close),
                NON_POSITIVE_PRICE,
            ),
        ] {
            if set {
                flags |= flag;
            }
        }
        let session = self.sessions.intern(row.session)?;
        let instrument = self.instruments.intern(row.instrument_id)?;
        let buy_reason = self.reason(row.buy_reason)?;
        let sell_reason = self.reason(row.sell_reason)?;
        let rows = &mut self.rows;
        rows.session.push(session);
        rows.instrument.push(instrument);
        rows.open.push(row.open.unwrap_or(0));
        rows.close.push(row.close.unwrap_or(0));
        rows.flags.push(flags);
        rows.buy_reason.push(buy_reason);
        rows.sell_reason.push(sell_reason);
        Ok(())
    }

    fn finish(self) -> MarketRows {
        let mut rows = self.rows;
        for column in [&mut rows.session, &mut rows.instrument] {
            column.shrink_to_fit();
        }
        rows.open.shrink_to_fit();
        rows.close.shrink_to_fit();
        rows.flags.shrink_to_fit();
        rows.buy_reason.shrink_to_fit();
        rows.sell_reason.shrink_to_fit();
        rows.sessions = self.sessions.names;
        rows.instruments = self.instruments.names;
        rows.reasons = self.reasons.names;
        rows
    }
}

impl<'de> Deserialize<'de> for MarketRows {
    fn deserialize<D: Deserializer<'de>>(deserializer: D) -> Result<Self, D::Error> {
        struct RowsVisitor;
        impl<'de> Visitor<'de> for RowsVisitor {
            type Value = MarketRows;

            // Matches `Vec<DailyRow>` so type errors keep their wording.
            fn expecting(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
                f.write_str("a sequence")
            }

            fn visit_seq<A: SeqAccess<'de>>(self, mut seq: A) -> Result<MarketRows, A::Error> {
                let mut builder = Builder::default();
                while let Some(row) = seq.next_element::<DailyRow>()? {
                    builder.push(row)?;
                }
                Ok(builder.finish())
            }
        }
        deserializer.deserialize_seq(RowsVisitor)
    }
}

/// One prepared row, decoded from the columns.
#[derive(Clone, Copy)]
pub(super) struct RowView<'a> {
    pub open: Option<i64>,
    pub close: Option<i64>,
    pub buy_at_open: Permission,
    pub sell_at_open: Permission,
    pub buy_reason: Option<&'a str>,
    pub sell_reason: Option<&'a str>,
    pub documented: bool,
    pub suspended: bool,
    pub corporate_action_gap: bool,
    pub buy_blocked_by_price_limit: bool,
    pub sell_blocked_by_price_limit: bool,
}

impl RowView<'_> {
    /// An open, a close, or a `yes` permission is evidence the instrument was listed.
    pub fn observed(&self) -> bool {
        self.open.is_some()
            || self.close.is_some()
            || self.buy_at_open == Permission::Yes
            || self.sell_at_open == Permission::Yes
    }
}

pub(super) fn view<'a>(
    flags: u16,
    open: i64,
    close: i64,
    reasons: [Option<&'a str>; 2],
) -> RowView<'a> {
    let price = |p: i64| (p != 0).then_some(p);
    RowView {
        open: price(open),
        close: price(close),
        buy_at_open: buy_permission(flags),
        sell_at_open: sell_permission(flags),
        buy_reason: reasons[0],
        sell_reason: reasons[1],
        documented: flags & DOCUMENTED != 0,
        suspended: flags & SUSPENDED != 0,
        corporate_action_gap: flags & CORPORATE_ACTION_GAP != 0,
        buy_blocked_by_price_limit: flags & BUY_BLOCKED_BY_PRICE_LIMIT != 0,
        sell_blocked_by_price_limit: flags & SELL_BLOCKED_BY_PRICE_LIMIT != 0,
    }
}
