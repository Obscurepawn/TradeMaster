//! Daily target portfolios. The planner uses previous closing marks, and the
//! execution step sees only orders whose quantities have already been fixed.
mod dividends;
mod execution;
mod market;
pub use market::{PreparedMarket, PriceGrid};
mod sizing;
mod wire;
pub use wire::*;

use super::{
    CnFeeSchedule, DataError, ExecutionError, LedgerError, LedgerLot, LotLedger, TradingCalendar,
    Valuation, ppm_cost,
};
use execution::execute_plan;
use sizing::{Costs, Sizing, size};
use std::collections::{BTreeMap, BTreeSet};
use std::sync::Arc;
use thiserror::Error;
use time::{Date, Duration, OffsetDateTime, format_description::well_known::Iso8601};
use tm_core::{
    AcceptedOrder, ContractError, ExactValueError, Fill, Fixed8, InstrumentSpec, OrderExecution,
    OrderIntent, OrderTerminal, PositionSnapshot, Quantity, SettlementEvent, Side, SubmittedOrder,
    Venue,
};

const U: i128 = 100_000_000;
const CENT: i128 = 1_000_000;

#[derive(Debug, Error)]
pub enum SnapshotError {
    #[error("invalid snapshot run: {0}")]
    Invalid(String),
    #[error("arithmetic overflow")]
    Overflow,
    #[error("{0}: {1}")]
    Gap(String, String, &'static str),
    #[error(transparent)]
    Ledger(#[from] LedgerError),
    #[error(transparent)]
    Contract(#[from] ContractError),
    #[error(transparent)]
    Execution(#[from] ExecutionError),
    #[error(transparent)]
    Exact(#[from] ExactValueError),
}
fn invalid(reason: impl Into<String>) -> SnapshotError {
    SnapshotError::Invalid(reason.into())
}
fn add(a: i128, b: i128) -> Result<i128, SnapshotError> {
    a.checked_add(b).ok_or(SnapshotError::Overflow)
}
fn mul(a: i128, b: i128) -> Result<i128, SnapshotError> {
    a.checked_mul(b).ok_or(SnapshotError::Overflow)
}
fn ceil_div(a: i128, b: i128) -> Result<i128, SnapshotError> {
    add(a / b, i128::from(a % b != 0))
}

#[derive(Clone)]
struct Sessions(Arc<Vec<OffsetDateTime>>);
impl TradingCalendar for Sessions {
    fn next_session_open(
        &self,
        _: Venue,
        after: OffsetDateTime,
    ) -> Result<OffsetDateTime, DataError> {
        self.0
            .get(self.0.partition_point(|d| *d <= after))
            .copied()
            .ok_or_else(|| DataError::Unavailable("no next session".into()))
    }
    fn session_key(&self, _: Venue, at: OffsetDateTime) -> Result<String, DataError> {
        Ok(at.date().to_string())
    }
}
fn open_time(session: &str) -> Result<OffsetDateTime, SnapshotError> {
    let date = Date::parse(session, &Iso8601::DATE)
        .map_err(|_| invalid(format!("invalid session {session}")))?;
    if date.to_string() != session {
        return Err(invalid(format!("invalid session {session}")));
    }
    // Times are internal ordering keys. Every session has one open and one close.
    Ok(date.midnight().assume_utc())
}
/// The execution function receives only the opening price and permission for its side.
struct Opening<'a> {
    price: Option<i64>,
    permission: &'a Permission,
    reason: Option<&'a str>,
    documented: bool,
    suspended: bool,
    blocked_by_price_limit: bool,
    corporate_action_gap: bool,
}
impl DailyRow {
    fn opening(&self, side: Side) -> Opening<'_> {
        let (permission, reason, blocked_by_price_limit) = match side {
            Side::Buy => (
                &self.buy_at_open,
                self.buy_reason.as_deref(),
                self.buy_blocked_by_price_limit,
            ),
            Side::Sell => (
                &self.sell_at_open,
                self.sell_reason.as_deref(),
                self.sell_blocked_by_price_limit,
            ),
        };
        Opening {
            price: self.open,
            permission,
            reason,
            documented: self.evidence.as_ref().is_some_and(|s| !s.is_empty()),
            suspended: self.suspended,
            blocked_by_price_limit,
            corporate_action_gap: self.corporate_action_gap,
        }
    }
}

impl SnapshotResult {
    fn new() -> Self {
        Self {
            status: "completed",
            first_issue: None,
            daily: vec![],
            dividends: vec![],
            positions: vec![],
            attempts: vec![],
            orders: vec![],
            fills: vec![],
            batches: vec![],
            total_costs: 0,
            turnover_notional: 0,
            order_count: 0,
            fill_count: 0,
            rejected_order_count: 0,
        }
    }
}

/// Cash, shares and unpaid dividends at one open or close. Monetary fields are fixed-8.
pub struct SnapshotAccount {
    pub cash: Fixed8,
    pub market_value: Fixed8,
    pub dividend_receivable: Fixed8,
    pub equity: Fixed8,
    pub positions: Vec<PositionSnapshot>,
}

impl SnapshotAccount {
    fn from_ledger(account: Valuation, receivable: i128) -> Result<Self, SnapshotError> {
        let nav = &account.nav;
        Ok(Self {
            cash: nav.cash(),
            market_value: nav.market_value(),
            dividend_receivable: Fixed8::from_scaled(receivable)?,
            equity: Fixed8::from_scaled(add(nav.net_asset_value().scaled(), receivable)?)?,
            positions: account.positions,
        })
    }
}

/// The account, active targets and results of a single backtest.
pub struct SnapshotSession<'a> {
    market: &'a PreparedMarket<'a>,
    ledger: LotLedger<Sessions>,
    costs: Costs,
    policy: Policy,
    targets: Vec<TargetBatch>,
    target_ids: BTreeSet<String>,
    decisions: BTreeSet<usize>,
    arrivals: Vec<Vec<usize>>,
    active: Option<usize>,
    latest: Option<usize>,
    marks: BTreeMap<String, i128>,
    cursor: usize,
    out: SnapshotResult,
    detail: bool,
    feedback: bool,
    fills_read: usize,
    dividend_receivable: i128,
    recorded_shares: Vec<i128>,
    dividend_entries: Vec<Option<usize>>,
}

impl<'a> SnapshotSession<'a> {
    pub fn new(
        market: &'a PreparedMarket<'a>,
        run: &SnapshotRun,
        feedback: bool,
    ) -> Result<Self, SnapshotError> {
        if run.initial_cash < 0
            || (run.initial_cash == 0 && run.initial_lots.is_empty())
            || run.policy.max_attempts == 0
            || run.policy.minimum_commission < 0
        {
            return Err(invalid(
                "need a funded account, nonnegative fees and a positive attempt limit",
            ));
        }
        let start = market.calendar.0[0];
        let next_session = market.calendar.0[market.sessions().len()];
        let mut ledger = LotLedger::with_cash(
            i128::from(run.initial_cash),
            start,
            Arc::clone(&market.instruments),
            market.calendar.clone(),
        )?;
        for (i, lot) in run.initial_lots.iter().enumerate() {
            let unlock = open_time(&lot.sellable_session)?;
            let within_calendar = unlock > start && unlock <= next_session;
            if lot.quantity <= 0
                || !ledger.instruments.contains_key(&lot.instrument_id)
                || (within_calendar && !market.index.contains_key(lot.sellable_session.as_str()))
            {
                return Err(invalid(
                    "unknown initial instrument, invalid lot quantity or sellable session",
                ));
            }
            let id = format!("initial-{i}");
            ledger.lots.insert(
                id.clone(),
                LedgerLot {
                    lot_id: id,
                    instrument_id: lot.instrument_id.clone(),
                    quantity: i128::from(lot.quantity),
                    acquired_at: start,
                    sellable_at: unlock.max(start),
                    unit_cost_scaled: 0,
                },
            );
        }
        let policy = &run.policy;
        let fees = CnFeeSchedule {
            commission_ppm: policy.commission_ppm,
            minimum_commission: Fixed8::from_scaled(i128::from(policy.minimum_commission))?,
            sell_stamp_duty_ppm: policy.sell_stamp_duty_ppm,
            sse_transfer_fee_ppm: policy.sse_transfer_fee_ppm,
            rounding_unit: Fixed8::from_scaled(CENT)?,
            schedule_id: "snapshot".into(),
        };
        let mut session = Self {
            market,
            ledger,
            costs: Costs {
                fees,
                slippage_ppm: policy.slippage_ppm,
            },
            policy: policy.clone(),
            targets: Vec::new(),
            target_ids: BTreeSet::new(),
            decisions: BTreeSet::new(),
            arrivals: vec![Vec::new(); market.sessions().len() + 1],
            active: None,
            latest: None,
            marks: BTreeMap::new(),
            cursor: 0,
            out: SnapshotResult::new(),
            detail: run.detail,
            feedback,
            fills_read: 0,
            dividend_receivable: 0,
            recorded_shares: vec![0; market.data.cash_dividends.len()],
            dividend_entries: vec![None; market.data.cash_dividends.len()],
        };
        for target in &run.targets {
            session.register(target.clone())?;
        }
        Ok(session)
    }

    fn register(&mut self, target: TargetBatch) -> Result<(), SnapshotError> {
        let decision = *self
            .market
            .index
            .get(target.decision_session.as_str())
            .ok_or_else(|| invalid("unknown decision session"))?;
        let eligible = *self
            .market
            .index
            .get(target.eligible_session.as_str())
            .ok_or_else(|| invalid("unknown eligible session"))?;
        if decision >= eligible
            || self.decisions.contains(&decision)
            || target.batch_id.is_empty()
            || self.target_ids.contains(&target.batch_id)
        {
            return Err(invalid(
                "need unique batch id and decision date before the eligible session",
            ));
        }
        let mut sum = 0;
        for (id, weight) in &target.weights {
            if !self.ledger.instruments.contains_key(id) || *weight < 0 {
                return Err(invalid("unknown target instrument or negative weight"));
            }
            sum = add(sum, i128::from(*weight))?;
        }
        if sum > U {
            return Err(invalid("stock weights exceed one"));
        }
        self.decisions.insert(decision);
        self.target_ids.insert(target.batch_id.clone());
        self.arrivals[eligible].push(self.targets.len());
        self.out.batches.push(BatchOutcome {
            batch_id: target.batch_id.clone(),
            attempts: 0,
            status: "end_of_run",
        });
        self.targets.push(target);
        Ok(())
    }

    /// Register the target returned by the strategy for the most recent valued close.
    pub fn submit(&mut self, target: TargetBatch) -> Result<(), SnapshotError> {
        if self.out.first_issue.is_some()
            || self.out.daily.last().map(|day| &day.session) != Some(&target.decision_session)
        {
            return Err(invalid("callback target must use the current valued close"));
        }
        self.register(target)
    }

    /// Move through one open and close. A missing price can end the run at either phase.
    pub fn advance(&mut self) -> Result<Option<SnapshotAccount>, SnapshotError> {
        let i = self.cursor;
        let Some(session) = self.market.sessions().get(i) else {
            return Ok(None);
        };
        if self.out.first_issue.is_some() {
            return Ok(None);
        }
        if !self.detail && self.fills_read > 0 {
            self.out.fills.clear();
            self.fills_read = 0;
        }
        self.cursor += 1;
        match self.trade_day(i, session) {
            Ok(account) => Ok(Some(account)),
            Err(SnapshotError::Gap(id, reason, phase)) => {
                self.out.status = "insufficient_data";
                self.out.first_issue = Some(DataIssue {
                    phase,
                    session: session.clone(),
                    instrument_id: id,
                    reason,
                });
                Ok(None)
            }
            Err(error) => Err(error),
        }
    }

    fn trade_day(&mut self, i: usize, session: &str) -> Result<SnapshotAccount, SnapshotError> {
        let open = self.market.calendar.0[i];
        let rows = &self.market.rows[i];
        // The newest decision wins when several batches become eligible at the same open.
        self.arrivals[i].sort_by_key(|&k| &self.targets[k].decision_session);
        for &arriving in &self.arrivals[i] {
            if self.latest.is_some_and(|k| {
                self.targets[arriving].decision_session <= self.targets[k].decision_session
            }) {
                self.out.batches[arriving].status = "replaced";
            } else {
                if let Some(previous) = self.active.replace(arriving) {
                    self.out.batches[previous].status = "replaced";
                }
                self.latest = Some(arriving);
            }
        }
        if i > 0 {
            self.ledger
                .settle_unaudited(&SettlementEvent { event_time: open })?;
        }
        for lot in self.ledger.lots.values() {
            if rows
                .get(lot.instrument_id.as_str())
                .is_some_and(|r| r.corporate_action_gap)
            {
                return Err(SnapshotError::Gap(
                    lot.instrument_id.clone(),
                    "corporate_action_gap".into(),
                    "open",
                ));
            }
        }
        let dividend_day = self.market.dividends.get(&i);
        if let Some(day) = dividend_day {
            self.accrue_dividends(day)?;
        }
        if let Some(k) = self.active {
            let batch = &self.targets[k];
            self.out.batches[k].attempts += 1;
            let account = SnapshotAccount::from_ledger(
                self.ledger.valuation(open, &self.marks)?,
                self.dividend_receivable,
            )?;
            let plan = size(
                batch,
                &account,
                &self.marks,
                &self.ledger.instruments,
                &self.policy,
                &self.costs,
            )?;
            let done = execute_plan(
                &mut self.ledger,
                &self.costs,
                |id, side| rows.get(id).map(|r| r.opening(side)),
                session,
                open,
                batch,
                k,
                plan,
                &mut self.out,
                self.policy.enforce_price_limits,
                self.detail,
                self.feedback,
            )?;
            if done || self.out.batches[k].attempts >= self.policy.max_attempts {
                self.out.batches[k].status = if done { "reached" } else { "expired" };
                self.active = None;
            }
        }
        self.close_session(i, session, open)
    }

    fn close_session(
        &mut self,
        i: usize,
        session: &str,
        open: OffsetDateTime,
    ) -> Result<SnapshotAccount, SnapshotError> {
        let rows = &self.market.rows[i];
        let mut next = BTreeMap::new();
        let mut carried = BTreeSet::new();
        for (&id, &row) in rows {
            if row.suspended {
                if row.evidence.as_ref().is_some_and(|s| !s.is_empty())
                    && let Some(&last) = self.marks.get(id)
                {
                    next.insert(id.to_owned(), last);
                    carried.insert(id);
                }
            } else if let Some(price) = row.close {
                next.insert(id.to_owned(), i128::from(price));
            }
        }
        for lot in self.ledger.lots.values() {
            let id = &lot.instrument_id;
            if rows
                .get(id.as_str())
                .is_some_and(|r| r.corporate_action_gap)
            {
                return Err(SnapshotError::Gap(
                    id.clone(),
                    "corporate_action_gap".into(),
                    "close",
                ));
            }
            if !next.contains_key(id) {
                return Err(SnapshotError::Gap(
                    id.clone(),
                    "missing_close".into(),
                    "close",
                ));
            }
        }
        let close = open + Duration::hours(1);
        if let Some(day) = self.market.dividends.get(&i) {
            self.close_dividends(day)?;
        }
        let account = SnapshotAccount::from_ledger(
            self.ledger.valuation(close, &next)?,
            self.dividend_receivable,
        )?;
        // Later ledger transitions start from the most recent close and its marks.
        self.ledger.current_time = close;
        for p in &account.positions {
            let id = &p.instrument_id().0;
            self.ledger.last_prices.insert(id.clone(), next[id]);
            if self.detail {
                self.out.positions.push(DailyPosition {
                    session: session.into(),
                    instrument_id: id.clone(),
                    quantity: p.quantity().units(),
                    sellable_quantity: p.sellable_quantity().units(),
                    market_value: p.market_value().scaled(),
                    carried_mark: carried.contains(id.as_str()),
                });
            }
        }
        self.marks = next;
        self.out.daily.push(DailyEquity {
            session: session.into(),
            cash: account.cash.scaled(),
            market_value: account.market_value.scaled(),
            equity: account.equity.scaled(),
            dividend_receivable: account.dividend_receivable.scaled(),
        });
        Ok(account)
    }

    /// Fills since the preceding call, including sessions between scheduled decisions.
    pub fn new_fills(&mut self) -> &[Trade] {
        let start = self.fills_read;
        self.fills_read = self.out.fills.len();
        &self.out.fills[start..]
    }

    pub fn finish(mut self) -> SnapshotResult {
        for (outcome, target) in self.out.batches.iter_mut().zip(&self.targets) {
            if outcome.status == "end_of_run" {
                if outcome.attempts == 0
                    && self
                        .latest
                        .is_some_and(|k| target.decision_session < self.targets[k].decision_session)
                {
                    outcome.status = "replaced";
                } else if self.out.first_issue.is_some() {
                    outcome.status = "insufficient_data";
                }
            }
        }
        if !self.detail {
            self.out.fills.clear();
        }
        self.out
    }
}

pub fn run_snapshot(
    data: &SnapshotData,
    run: &SnapshotRun,
) -> Result<SnapshotResult, SnapshotError> {
    let market = PreparedMarket::new(data)?;
    let mut session = SnapshotSession::new(&market, run, false)?;
    while session.advance()?.is_some() {}
    Ok(session.finish())
}
