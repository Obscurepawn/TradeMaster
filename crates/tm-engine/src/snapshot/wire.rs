//! Snapshot prices, amounts and weights are integers scaled by 100,000,000.
//! Quantities are whole shares and policy rates are parts per million.
use super::rows::MarketRows;
use serde::{Deserialize, Serialize};
use std::collections::BTreeMap;
use tm_core::{InstrumentSpec, Side};

#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct SnapshotData {
    pub sessions: Vec<String>,
    pub next_session: String,
    pub instruments: Vec<InstrumentSpec>,
    /// Decoded row by row from `DailyRow` records into a compact columnar form.
    pub rows: MarketRows,
    #[serde(default)]
    pub cash_dividends: Vec<CashDividend>,
}

#[derive(Debug, Clone, Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
pub struct CashDividend {
    pub instrument_id: String,
    pub record_session: String,
    pub ex_session: String,
    pub pay_session: String,
    pub amount_per_share: i64,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum Permission {
    Yes,
    No,
    Unknown,
}

#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct DailyRow {
    pub session: String,
    pub instrument_id: String,
    pub open: Option<i64>,
    pub close: Option<i64>,
    pub buy_at_open: Permission,
    pub sell_at_open: Permission,
    pub buy_reason: Option<String>,
    pub sell_reason: Option<String>,
    pub evidence: Option<String>,
    #[serde(default)]
    pub suspended: bool,
    #[serde(default)]
    pub corporate_action_gap: bool,
    /// The known blocked permission on this side is caused only by a price limit.
    #[serde(default)]
    pub buy_blocked_by_price_limit: bool,
    #[serde(default)]
    pub sell_blocked_by_price_limit: bool,
}

#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct SnapshotRun {
    pub initial_cash: i64,
    #[serde(default)]
    pub initial_lots: Vec<InitialLot>,
    pub targets: Vec<TargetBatch>,
    pub policy: Policy,
    pub detail: bool,
}

#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct InitialLot {
    pub instrument_id: String,
    pub quantity: i64,
    pub sellable_session: String,
}

#[derive(Debug, Clone, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct TargetBatch {
    pub batch_id: String,
    pub decision_session: String,
    pub eligible_session: String,
    pub weights: BTreeMap<String, i64>,
}

#[derive(Debug, Clone, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Policy {
    pub commission_ppm: u32,
    pub minimum_commission: i64,
    pub sell_stamp_duty_ppm: u32,
    pub sse_transfer_fee_ppm: u32,
    pub slippage_ppm: u32,
    pub buy_cap_ppm: u32,
    pub max_attempts: u32,
    #[serde(default = "enforce_price_limits_by_default")]
    pub enforce_price_limits: bool,
}

fn enforce_price_limits_by_default() -> bool {
    true
}

#[derive(Debug, Serialize)]
pub struct SnapshotResult {
    pub status: &'static str,
    pub first_issue: Option<DataIssue>,
    pub daily: Vec<DailyEquity>,
    #[serde(skip_serializing_if = "Vec::is_empty")]
    pub dividends: Vec<DividendEntitlement>,
    pub positions: Vec<DailyPosition>,
    pub attempts: Vec<Attempt>,
    pub orders: Vec<Order>,
    pub fills: Vec<Trade>,
    pub batches: Vec<BatchOutcome>,
    pub total_costs: i128,
    pub turnover_notional: i128,
    pub order_count: u64,
    pub fill_count: u64,
    pub rejected_order_count: u64,
}
#[derive(Debug, Serialize)]
pub struct DataIssue {
    pub phase: &'static str,
    pub session: String,
    pub instrument_id: String,
    pub reason: String,
}
#[derive(Debug, Serialize)]
pub struct DividendEntitlement {
    #[serde(flatten)]
    pub action: CashDividend,
    pub quantity: i128,
    pub amount: i128,
    pub paid: bool,
}

fn zero(amount: &i128) -> bool {
    *amount == 0
}

#[derive(Debug, Serialize)]
pub struct DailyEquity {
    pub session: String,
    pub cash: i128,
    pub market_value: i128,
    pub equity: i128,
    #[serde(skip_serializing_if = "zero")]
    pub dividend_receivable: i128,
}
#[derive(Debug, Serialize)]
pub struct DailyPosition {
    pub session: String,
    pub instrument_id: String,
    pub quantity: i128,
    pub sellable_quantity: i128,
    pub market_value: i128,
    pub carried_mark: bool,
}
#[derive(Debug, Serialize)]
pub struct BatchOutcome {
    pub batch_id: String,
    pub status: &'static str,
    pub attempts: u32,
}
#[derive(Debug, Serialize)]
pub struct Attempt {
    pub session: String,
    pub batch_id: String,
    pub attempt_id: String,
    pub reserve: i128,
    pub buy_budget: i128,
    pub shortfalls: Vec<Shortfall>,
}
#[derive(Debug, Serialize)]
pub struct Shortfall {
    pub instrument_id: String,
    pub reason: String,
    pub remaining_quantity: Option<i128>,
    pub order_id: Option<String>,
}
#[derive(Debug, Serialize)]
pub struct Order {
    pub session: String,
    pub batch_id: String,
    pub attempt_id: String,
    pub order_id: String,
    pub instrument_id: String,
    pub side: Side,
    pub quantity: i128,
    pub buy_cap: Option<i128>,
    pub reserved_cash: i128,
    pub status: &'static str,
    pub reason: Option<String>,
}
#[derive(Debug, Serialize)]
pub struct Trade {
    pub session: String,
    pub batch_id: String,
    pub attempt_id: String,
    pub order_id: String,
    pub fill_id: String,
    pub instrument_id: String,
    pub side: Side,
    pub quantity: i128,
    pub price: i128,
    pub commission: i128,
    pub tax: i128,
    pub transfer_fee: i128,
    pub slippage: i128,
}
