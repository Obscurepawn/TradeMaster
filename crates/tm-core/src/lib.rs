//! Shared TradeMaster domain contracts.

use std::collections::{BTreeMap, BTreeSet, HashMap};
use std::sync::Arc;

use arrow_array::{
    Array, ArrayRef, Decimal128Array, RecordBatch, StringArray, TimestampMicrosecondArray,
    UInt8Array,
};
use arrow_schema::{DataType, Field, Schema, TimeUnit};
use bytes::Bytes;
use parquet::arrow::arrow_reader::ParquetRecordBatchReaderBuilder;
use serde::de::Error as DeError;
use serde::{Deserialize, Serialize};
use sha2::{Digest, Sha256};
use thiserror::Error;
use time::format_description::well_known::Rfc3339;
use time::{OffsetDateTime, UtcOffset};

pub const RUN_ARTIFACT_SCHEMA_JSON: &str = include_str!("../schemas/run-artifacts-v1.json");
pub const RUN_MANIFEST_SCHEMA_JSON: &str = include_str!("../schemas/run-manifest-v1.json");
pub const RUN_MANIFEST_FIXTURE_JSON: &str = include_str!("../schemas/run-manifest-v1.fixture.json");
const DECIMAL128_LIMIT: i128 = 10_i128.pow(38);

#[derive(Clone, Copy, Debug, PartialEq, Eq, PartialOrd, Ord, Serialize)]
#[serde(transparent)]
pub struct Fixed8(i128);

#[derive(Clone, Copy, Debug, PartialEq, Eq, PartialOrd, Ord, Serialize)]
#[serde(transparent)]
pub struct Quantity(i128);

#[derive(Clone, Debug, Error, PartialEq, Eq)]
pub enum ExactValueError {
    #[error("scaled value exceeds decimal128(38) domain")]
    OutOfRange,
    #[error("quantity must be positive")]
    NonPositiveQuantity,
    #[error("quantity must not be negative")]
    NegativeQuantity,
}

impl Fixed8 {
    pub fn from_scaled(value: i128) -> Result<Self, ExactValueError> {
        if value.unsigned_abs() >= DECIMAL128_LIMIT as u128 {
            return Err(ExactValueError::OutOfRange);
        }
        Ok(Self(value))
    }

    pub const fn scaled(self) -> i128 {
        self.0
    }
}

impl<'de> Deserialize<'de> for Fixed8 {
    fn deserialize<D: serde::Deserializer<'de>>(deserializer: D) -> Result<Self, D::Error> {
        let value = i128::deserialize(deserializer)?;
        Self::from_scaled(value).map_err(D::Error::custom)
    }
}

impl Quantity {
    pub fn new(value: i128) -> Result<Self, ExactValueError> {
        if value < 0 {
            return Err(ExactValueError::NegativeQuantity);
        }
        if value >= DECIMAL128_LIMIT {
            return Err(ExactValueError::OutOfRange);
        }
        Ok(Self(value))
    }

    pub fn positive(value: i128) -> Result<Self, ExactValueError> {
        if value <= 0 {
            return Err(ExactValueError::NonPositiveQuantity);
        }
        Self::new(value)
    }

    pub const fn units(self) -> i128 {
        self.0
    }
}

impl<'de> Deserialize<'de> for Quantity {
    fn deserialize<D: serde::Deserializer<'de>>(deserializer: D) -> Result<Self, D::Error> {
        let value = i128::deserialize(deserializer)?;
        Self::new(value).map_err(D::Error::custom)
    }
}

#[derive(Clone, Debug, PartialEq, Eq, Hash, Serialize, Deserialize)]
pub struct InstrumentId(pub String);

#[derive(Clone, Copy, Debug, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum AssetClass {
    Stock,
    Etf,
    Index,
}

#[derive(Clone, Copy, Debug, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum Venue {
    Sse,
    Szse,
    Bse,
    Hkex,
    Nyse,
    Nasdaq,
    Other,
}

#[derive(Clone, Copy, Debug, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum SettlementPolicy {
    T0,
    T1,
}

#[derive(Clone, Debug, PartialEq, Eq, Serialize)]
pub struct InstrumentSpec {
    instrument_id: InstrumentId,
    asset_class: AssetClass,
    venue: Venue,
    currency: String,
    buy_lot_size: u64,
    tick_size: Fixed8,
    settlement: SettlementPolicy,
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct InstrumentSpecWire {
    instrument_id: InstrumentId,
    asset_class: AssetClass,
    venue: Venue,
    currency: String,
    buy_lot_size: u64,
    tick_size: Fixed8,
    settlement: SettlementPolicy,
}

impl<'de> Deserialize<'de> for InstrumentSpec {
    fn deserialize<D: serde::Deserializer<'de>>(deserializer: D) -> Result<Self, D::Error> {
        let wire = InstrumentSpecWire::deserialize(deserializer)?;
        Self::try_new(
            wire.instrument_id.0,
            wire.asset_class,
            wire.venue,
            wire.currency,
            wire.buy_lot_size,
            wire.tick_size.scaled(),
            wire.settlement,
        )
        .map_err(D::Error::custom)
    }
}

impl InstrumentSpec {
    #[allow(clippy::too_many_arguments)]
    pub fn try_new(
        instrument_id: impl Into<String>,
        asset_class: AssetClass,
        venue: Venue,
        currency: impl Into<String>,
        buy_lot_size: u64,
        tick_size_scaled: i128,
        settlement: SettlementPolicy,
    ) -> Result<Self, ContractError> {
        let instrument_id = instrument_id.into();
        let currency = currency.into();
        if instrument_id.is_empty()
            || currency.len() != 3
            || buy_lot_size == 0
            || tick_size_scaled <= 0
        {
            return Err(ContractError::InvalidInstrumentSpec);
        }
        Ok(Self {
            instrument_id: InstrumentId(instrument_id),
            asset_class,
            venue,
            currency,
            buy_lot_size,
            tick_size: Fixed8::from_scaled(tick_size_scaled)?,
            settlement,
        })
    }

    pub fn instrument_id(&self) -> &InstrumentId {
        &self.instrument_id
    }
    pub const fn asset_class(&self) -> AssetClass {
        self.asset_class
    }
    pub const fn venue(&self) -> Venue {
        self.venue
    }
    pub fn currency(&self) -> &str {
        &self.currency
    }
    pub const fn buy_lot_size(&self) -> u64 {
        self.buy_lot_size
    }
    pub const fn tick_size(&self) -> Fixed8 {
        self.tick_size
    }
    pub const fn settlement(&self) -> SettlementPolicy {
        self.settlement
    }
}

#[derive(Clone, Copy, Debug, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum MarketEventKind {
    SessionOpen,
    BarClose,
    Settlement,
}

#[derive(Clone, Copy, Debug, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum TradingStatus {
    Tradable,
    Suspended,
}

#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize)]
pub struct MarketBar {
    pub instrument_id: InstrumentId,
    pub event_time: OffsetDateTime,
    pub open: Fixed8,
    pub high: Fixed8,
    pub low: Fixed8,
    pub close: Fixed8,
    pub volume: Quantity,
    pub up_limit: Fixed8,
    pub down_limit: Fixed8,
    pub trading_status: TradingStatus,
    pub status_evidence_id: String,
}

#[derive(Clone, Debug, PartialEq, Eq, Serialize)]
pub struct MarketSnapshot {
    event_time: OffsetDateTime,
    snapshot_id: String,
    bars: Vec<MarketBar>,
}

impl MarketSnapshot {
    pub fn try_new(
        event_time: OffsetDateTime,
        snapshot_id: impl Into<String>,
        bars: Vec<MarketBar>,
    ) -> Result<Self, ContractError> {
        let snapshot_id = snapshot_id.into();
        if !is_sha256(&snapshot_id)
            || bars.iter().any(|bar| bar.event_time > event_time)
            || bars
                .windows(2)
                .any(|pair| pair[0].instrument_id.0.as_str() >= pair[1].instrument_id.0.as_str())
        {
            return Err(ContractError::InvalidMarketView);
        }
        Ok(Self {
            event_time,
            snapshot_id,
            bars,
        })
    }

    pub const fn event_time(&self) -> OffsetDateTime {
        self.event_time
    }

    pub fn snapshot_id(&self) -> &str {
        &self.snapshot_id
    }

    pub fn bars(&self) -> &[MarketBar] {
        &self.bars
    }
}

#[derive(Clone, Debug, PartialEq, Eq, Serialize)]
pub struct MarketEvent {
    event_time: OffsetDateTime,
    kind: MarketEventKind,
    market: MarketSnapshot,
}

impl MarketEvent {
    pub fn try_new(
        event_time: OffsetDateTime,
        kind: MarketEventKind,
        market: MarketSnapshot,
    ) -> Result<Self, ContractError> {
        if market.event_time() != event_time {
            return Err(ContractError::InvalidMarketView);
        }
        Ok(Self {
            event_time,
            kind,
            market,
        })
    }

    pub const fn event_time(&self) -> OffsetDateTime {
        self.event_time
    }

    pub const fn kind(&self) -> MarketEventKind {
        self.kind
    }

    pub const fn market(&self) -> &MarketSnapshot {
        &self.market
    }
}

#[derive(Clone, Debug, PartialEq, Eq, Serialize)]
pub struct PortfolioPosition {
    instrument_id: InstrumentId,
    quantity: Quantity,
    sellable_quantity: Quantity,
    market_value: Fixed8,
}

impl PortfolioPosition {
    pub fn try_new(
        instrument_id: impl Into<String>,
        quantity: i128,
        sellable_quantity: i128,
        market_value_scaled: i128,
    ) -> Result<Self, ContractError> {
        if sellable_quantity > quantity || market_value_scaled < 0 {
            return Err(ContractError::InvalidPortfolioView);
        }
        Ok(Self {
            instrument_id: InstrumentId(instrument_id.into()),
            quantity: Quantity::new(quantity)?,
            sellable_quantity: Quantity::new(sellable_quantity)?,
            market_value: Fixed8::from_scaled(market_value_scaled)?,
        })
    }

    pub fn instrument_id(&self) -> &InstrumentId {
        &self.instrument_id
    }

    pub const fn quantity(&self) -> Quantity {
        self.quantity
    }

    pub const fn sellable_quantity(&self) -> Quantity {
        self.sellable_quantity
    }

    pub const fn market_value(&self) -> Fixed8 {
        self.market_value
    }
}

#[derive(Clone, Debug, PartialEq, Eq, Serialize)]
pub struct PortfolioView {
    event_time: OffsetDateTime,
    state_hash: String,
    cash: Fixed8,
    positions: Vec<PortfolioPosition>,
}

impl PortfolioView {
    pub fn try_new(
        event_time: OffsetDateTime,
        state_hash: impl Into<String>,
        cash_scaled: i128,
        positions: Vec<PortfolioPosition>,
    ) -> Result<Self, ContractError> {
        let state_hash = state_hash.into();
        if !is_sha256(&state_hash)
            || cash_scaled < 0
            || positions.windows(2).any(|pair| {
                pair[0].instrument_id().0.as_str() >= pair[1].instrument_id().0.as_str()
            })
        {
            return Err(ContractError::InvalidPortfolioView);
        }
        Ok(Self {
            event_time,
            state_hash,
            cash: Fixed8::from_scaled(cash_scaled)?,
            positions,
        })
    }

    pub const fn cash(&self) -> Fixed8 {
        self.cash
    }

    pub const fn event_time(&self) -> OffsetDateTime {
        self.event_time
    }

    pub fn positions(&self) -> &[PortfolioPosition] {
        &self.positions
    }

    pub fn state_hash(&self) -> &str {
        &self.state_hash
    }
}

#[derive(Clone, Copy, Debug, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum IntentType {
    TargetWeight,
    Quantity,
}

#[derive(Clone, Copy, Debug, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum Side {
    Buy,
    Sell,
}

#[derive(Clone, Debug, PartialEq, Eq, Serialize)]
pub struct SignalRecord {
    signal_id: String,
    strategy_id: String,
    instrument_id: InstrumentId,
    signal_time: OffsetDateTime,
    eligible_execution_time: OffsetDateTime,
    intent_type: IntentType,
    value: Fixed8,
    reason: String,
    snapshot_id: String,
}

impl SignalRecord {
    #[allow(clippy::too_many_arguments)]
    pub fn try_new(
        signal_id: impl Into<String>,
        strategy_id: impl Into<String>,
        instrument_id: impl Into<String>,
        signal_time: OffsetDateTime,
        eligible_execution_time: OffsetDateTime,
        intent_type: IntentType,
        value_scaled: i128,
        reason: impl Into<String>,
        snapshot_id: impl Into<String>,
    ) -> Result<Self, ContractError> {
        let snapshot_id = snapshot_id.into();
        if eligible_execution_time <= signal_time {
            return Err(ContractError::InvalidExecutionTime);
        }
        if !is_sha256(&snapshot_id) {
            return Err(ContractError::InvalidHash);
        }
        Ok(Self {
            signal_id: signal_id.into(),
            strategy_id: strategy_id.into(),
            instrument_id: InstrumentId(instrument_id.into()),
            signal_time,
            eligible_execution_time,
            intent_type,
            value: Fixed8::from_scaled(value_scaled)?,
            reason: reason.into(),
            snapshot_id,
        })
    }

    pub fn signal_id(&self) -> &str {
        &self.signal_id
    }

    pub fn strategy_id(&self) -> &str {
        &self.strategy_id
    }

    pub fn instrument_id(&self) -> &InstrumentId {
        &self.instrument_id
    }

    pub const fn signal_time(&self) -> OffsetDateTime {
        self.signal_time
    }

    pub const fn eligible_execution_time(&self) -> OffsetDateTime {
        self.eligible_execution_time
    }

    pub const fn intent_type(&self) -> IntentType {
        self.intent_type
    }

    pub const fn value(&self) -> Fixed8 {
        self.value
    }

    pub fn reason(&self) -> &str {
        &self.reason
    }

    pub fn snapshot_id(&self) -> &str {
        &self.snapshot_id
    }
}

#[derive(Clone, Debug, PartialEq, Eq, Serialize)]
pub struct OrderIntent {
    order_id: String,
    signal_id: String,
    instrument_id: InstrumentId,
    side: Side,
    quantity: Quantity,
    signal_time: OffsetDateTime,
    eligible_execution_time: OffsetDateTime,
}

#[derive(Clone, Debug, Error, PartialEq, Eq)]
pub enum ContractError {
    #[error("eligible execution time must be later than signal time")]
    InvalidExecutionTime,
    #[error("submission time must be on or after eligible execution time")]
    InvalidSubmissionTime,
    #[error("acceptance time must be on or after submission time")]
    InvalidAcceptanceTime,
    #[error(transparent)]
    ExactValue(#[from] ExactValueError),
    #[error("execution batch does not account for every accepted order exactly once")]
    IncompleteExecutionBatch,
    #[error("fill does not belong to its accepted order")]
    FillOrderMismatch,
    #[error("fill quantities do not conserve the requested order quantity")]
    QuantityMismatch,
    #[error("fill id must be unique within an execution batch")]
    DuplicateFillId,
    #[error("ledger posting amount must be positive")]
    NonPositivePosting,
    #[error("ledger posting group must share source, currency, and balance")]
    UnbalancedPostingGroup,
    #[error("fill price must be positive and costs must be non-negative")]
    InvalidMonetaryValue,
    #[error("execution outcome time precedes acceptance or moves backwards")]
    InvalidOutcomeTime,
    #[error("ledger effect hashes do not match whether state changed")]
    InvalidLedgerEffect,
    #[error("expected a lowercase sha256 value")]
    InvalidHash,
    #[error("account snapshot positions, lots, and NAV are inconsistent")]
    InvalidAccountSnapshot,
    #[error("market snapshot or event violates its frozen-time contract")]
    InvalidMarketView,
    #[error("portfolio view is negative, duplicated, or inconsistent")]
    InvalidPortfolioView,
    #[error("strategy or validation view violates event-time invariants")]
    InvalidStrategyView,
    #[error("instrument spec has an empty identity/currency or non-positive lot/tick")]
    InvalidInstrumentSpec,
}

impl OrderIntent {
    #[allow(clippy::too_many_arguments)]
    pub fn try_new(
        order_id: impl Into<String>,
        signal_id: impl Into<String>,
        instrument_id: impl Into<String>,
        side: Side,
        quantity: i128,
        signal_time: OffsetDateTime,
        eligible_execution_time: OffsetDateTime,
    ) -> Result<Self, ContractError> {
        if eligible_execution_time <= signal_time {
            return Err(ContractError::InvalidExecutionTime);
        }
        Ok(Self {
            order_id: order_id.into(),
            signal_id: signal_id.into(),
            instrument_id: InstrumentId(instrument_id.into()),
            side,
            quantity: Quantity::positive(quantity)?,
            signal_time,
            eligible_execution_time,
        })
    }

    pub fn order_id(&self) -> &str {
        &self.order_id
    }

    pub fn signal_id(&self) -> &str {
        &self.signal_id
    }

    pub fn instrument_id(&self) -> &InstrumentId {
        &self.instrument_id
    }

    pub const fn side(&self) -> Side {
        self.side
    }

    pub const fn quantity(&self) -> Quantity {
        self.quantity
    }

    pub const fn signal_time(&self) -> OffsetDateTime {
        self.signal_time
    }

    pub const fn eligible_execution_time(&self) -> OffsetDateTime {
        self.eligible_execution_time
    }
}

#[derive(Clone, Debug, PartialEq, Eq, Serialize)]
pub struct SubmittedOrder {
    intent: OrderIntent,
    submitted_at: OffsetDateTime,
}

impl SubmittedOrder {
    pub fn try_new(
        intent: OrderIntent,
        submitted_at: OffsetDateTime,
    ) -> Result<Self, ContractError> {
        if submitted_at < intent.eligible_execution_time() {
            return Err(ContractError::InvalidSubmissionTime);
        }
        Ok(Self {
            intent,
            submitted_at,
        })
    }

    pub const fn intent(&self) -> &OrderIntent {
        &self.intent
    }

    pub const fn submitted_at(&self) -> OffsetDateTime {
        self.submitted_at
    }
}

#[derive(Clone, Debug, PartialEq, Eq, Serialize)]
pub struct AcceptedOrder {
    submitted_order: SubmittedOrder,
    accepted_at: OffsetDateTime,
}

impl AcceptedOrder {
    pub fn try_new(
        submitted_order: SubmittedOrder,
        accepted_at: OffsetDateTime,
    ) -> Result<Self, ContractError> {
        if accepted_at < submitted_order.submitted_at() {
            return Err(ContractError::InvalidAcceptanceTime);
        }
        Ok(Self {
            submitted_order,
            accepted_at,
        })
    }

    pub fn order_id(&self) -> &str {
        self.submitted_order.intent().order_id()
    }

    pub const fn submitted_order(&self) -> &SubmittedOrder {
        &self.submitted_order
    }

    pub const fn accepted_at(&self) -> OffsetDateTime {
        self.accepted_at
    }
}

#[derive(Clone, Debug, PartialEq, Eq, Serialize)]
pub struct Fill {
    fill_id: String,
    order_id: String,
    event_time: OffsetDateTime,
    quantity: Quantity,
    price: Fixed8,
    commission: Fixed8,
    tax: Fixed8,
    transfer_fee: Fixed8,
    slippage: Fixed8,
}

impl Fill {
    #[allow(clippy::too_many_arguments)]
    pub fn try_new(
        fill_id: impl Into<String>,
        order_id: impl Into<String>,
        event_time: OffsetDateTime,
        quantity: i128,
        price_scaled: i128,
        commission_scaled: i128,
        tax_scaled: i128,
        transfer_fee_scaled: i128,
        slippage_scaled: i128,
    ) -> Result<Self, ContractError> {
        if price_scaled <= 0
            || commission_scaled < 0
            || tax_scaled < 0
            || transfer_fee_scaled < 0
            || slippage_scaled < 0
        {
            return Err(ContractError::InvalidMonetaryValue);
        }
        Ok(Self {
            fill_id: fill_id.into(),
            order_id: order_id.into(),
            event_time,
            quantity: Quantity::positive(quantity)?,
            price: Fixed8::from_scaled(price_scaled)?,
            commission: Fixed8::from_scaled(commission_scaled)?,
            tax: Fixed8::from_scaled(tax_scaled)?,
            transfer_fee: Fixed8::from_scaled(transfer_fee_scaled)?,
            slippage: Fixed8::from_scaled(slippage_scaled)?,
        })
    }

    pub fn fill_id(&self) -> &str {
        &self.fill_id
    }

    pub fn order_id(&self) -> &str {
        &self.order_id
    }

    pub const fn event_time(&self) -> OffsetDateTime {
        self.event_time
    }

    pub const fn quantity(&self) -> Quantity {
        self.quantity
    }

    pub const fn price(&self) -> Fixed8 {
        self.price
    }

    pub const fn commission(&self) -> Fixed8 {
        self.commission
    }

    pub const fn tax(&self) -> Fixed8 {
        self.tax
    }

    pub const fn transfer_fee(&self) -> Fixed8 {
        self.transfer_fee
    }

    pub const fn slippage(&self) -> Fixed8 {
        self.slippage
    }
}

#[derive(Clone, Copy, Debug, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum RejectionCode {
    InvalidTime,
    InvalidQuantity,
    Suspended,
    LimitUp,
    LimitDown,
    InsufficientCash,
    InsufficientSellable,
    MissingMarketData,
}

#[derive(Clone, Debug, PartialEq, Eq, Serialize)]
pub struct Rejection {
    order_id: String,
    event_time: OffsetDateTime,
    code: RejectionCode,
    message: String,
}

impl Rejection {
    pub fn try_new(
        order: &SubmittedOrder,
        event_time: OffsetDateTime,
        code: RejectionCode,
        message: impl Into<String>,
    ) -> Result<Self, ContractError> {
        if event_time < order.submitted_at() {
            return Err(ContractError::InvalidOutcomeTime);
        }
        Ok(Self {
            order_id: order.intent().order_id().to_owned(),
            event_time,
            code,
            message: message.into(),
        })
    }

    pub fn order_id(&self) -> &str {
        &self.order_id
    }

    pub const fn event_time(&self) -> OffsetDateTime {
        self.event_time
    }

    pub const fn code(&self) -> RejectionCode {
        self.code
    }

    pub fn message(&self) -> &str {
        &self.message
    }
}

#[derive(Clone, Debug, PartialEq, Eq, Serialize)]
pub struct Expiry {
    order_id: String,
    event_time: OffsetDateTime,
    remaining_quantity: Quantity,
    reason: String,
}

impl Expiry {
    pub fn try_new(
        order_id: impl Into<String>,
        event_time: OffsetDateTime,
        remaining_quantity: i128,
        reason: impl Into<String>,
    ) -> Result<Self, ContractError> {
        Ok(Self {
            order_id: order_id.into(),
            event_time,
            remaining_quantity: Quantity::positive(remaining_quantity)?,
            reason: reason.into(),
        })
    }

    pub fn order_id(&self) -> &str {
        &self.order_id
    }

    pub const fn remaining_quantity(&self) -> Quantity {
        self.remaining_quantity
    }

    pub const fn event_time(&self) -> OffsetDateTime {
        self.event_time
    }

    pub fn reason(&self) -> &str {
        &self.reason
    }
}

#[derive(Clone, Debug, PartialEq, Eq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum OrderTerminal {
    Filled,
    Expired(Expiry),
}

#[derive(Clone, Debug, PartialEq, Eq, Serialize)]
pub struct OrderExecution {
    accepted_order: AcceptedOrder,
    fills: Vec<Fill>,
    terminal: OrderTerminal,
}

impl OrderExecution {
    pub fn try_new(
        accepted_order: AcceptedOrder,
        fills: Vec<Fill>,
        terminal: OrderTerminal,
    ) -> Result<Self, ContractError> {
        let order_id = accepted_order.order_id();
        if fills.iter().any(|fill| fill.order_id() != order_id) {
            return Err(ContractError::FillOrderMismatch);
        }
        let accepted_at = accepted_order.accepted_at();
        if fills.iter().any(|fill| fill.event_time() < accepted_at)
            || fills
                .windows(2)
                .any(|pair| pair[1].event_time() < pair[0].event_time())
        {
            return Err(ContractError::InvalidOutcomeTime);
        }
        let filled = fills.iter().try_fold(0_i128, |total, fill| {
            total.checked_add(fill.quantity().units())
        });
        let Some(filled) = filled else {
            return Err(ContractError::QuantityMismatch);
        };
        let requested = accepted_order.submitted_order().intent().quantity().units();
        let valid_terminal = match &terminal {
            OrderTerminal::Filled => filled == requested && filled > 0,
            OrderTerminal::Expired(expiry) => {
                expiry.order_id() == order_id
                    && filled < requested
                    && expiry.remaining_quantity().units() == requested - filled
                    && expiry.event_time() >= accepted_at
                    && fills
                        .last()
                        .is_none_or(|fill| expiry.event_time() >= fill.event_time())
            }
        };
        if !valid_terminal {
            return Err(ContractError::QuantityMismatch);
        }
        Ok(Self {
            accepted_order,
            fills,
            terminal,
        })
    }

    pub const fn accepted_order(&self) -> &AcceptedOrder {
        &self.accepted_order
    }

    pub fn fills(&self) -> &[Fill] {
        &self.fills
    }

    pub const fn terminal(&self) -> &OrderTerminal {
        &self.terminal
    }
}

#[derive(Clone, Debug, PartialEq, Eq, Serialize)]
pub struct ExecutionBatch {
    executions: Vec<OrderExecution>,
}

impl ExecutionBatch {
    pub fn try_new(
        accepted: &[AcceptedOrder],
        executions: Vec<OrderExecution>,
    ) -> Result<Self, ContractError> {
        if accepted.len() != executions.len()
            || accepted
                .iter()
                .zip(&executions)
                .any(|(order, execution)| order != execution.accepted_order())
        {
            return Err(ContractError::IncompleteExecutionBatch);
        }
        let order_ids = accepted
            .iter()
            .map(AcceptedOrder::order_id)
            .collect::<BTreeSet<_>>();
        if order_ids.len() != accepted.len() {
            return Err(ContractError::IncompleteExecutionBatch);
        }
        let fill_ids: Vec<&str> = executions
            .iter()
            .flat_map(|execution| execution.fills().iter().map(Fill::fill_id))
            .collect();
        if fill_ids.iter().copied().collect::<BTreeSet<_>>().len() != fill_ids.len() {
            return Err(ContractError::DuplicateFillId);
        }
        Ok(Self { executions })
    }

    pub fn executions(&self) -> &[OrderExecution] {
        &self.executions
    }
}

#[derive(Clone, Copy, Debug, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum PostingAccount {
    Cash,
    Position,
    CommissionExpense,
    TaxExpense,
    TransferFeeExpense,
    SlippageExpense,
}

#[derive(Clone, Copy, Debug, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum DebitCredit {
    Debit,
    Credit,
}

#[derive(Clone, Debug, PartialEq, Eq, Serialize)]
pub struct LedgerPosting {
    posting_id: String,
    event_time: OffsetDateTime,
    account: PostingAccount,
    debit_credit: DebitCredit,
    currency: String,
    raw_units: i128,
    source_id: String,
}

impl LedgerPosting {
    #[allow(clippy::too_many_arguments)]
    pub fn try_new(
        posting_id: impl Into<String>,
        event_time: OffsetDateTime,
        account: PostingAccount,
        debit_credit: DebitCredit,
        currency: impl Into<String>,
        raw_units: i128,
        source_id: impl Into<String>,
    ) -> Result<Self, ContractError> {
        if raw_units <= 0 || raw_units >= DECIMAL128_LIMIT {
            return Err(ContractError::NonPositivePosting);
        }
        Ok(Self {
            posting_id: posting_id.into(),
            event_time,
            account,
            debit_credit,
            currency: currency.into(),
            raw_units,
            source_id: source_id.into(),
        })
    }

    pub const fn debit_credit(&self) -> DebitCredit {
        self.debit_credit
    }

    pub fn posting_id(&self) -> &str {
        &self.posting_id
    }

    pub const fn event_time(&self) -> OffsetDateTime {
        self.event_time
    }

    pub const fn account(&self) -> PostingAccount {
        self.account
    }

    pub fn currency(&self) -> &str {
        &self.currency
    }

    pub const fn raw_units(&self) -> i128 {
        self.raw_units
    }

    pub fn source_id(&self) -> &str {
        &self.source_id
    }
}

#[derive(Clone, Debug, PartialEq, Eq, Serialize)]
pub struct LedgerPostingGroup {
    postings: Vec<LedgerPosting>,
}

impl LedgerPostingGroup {
    pub fn try_new(postings: Vec<LedgerPosting>) -> Result<Self, ContractError> {
        let Some(first) = postings.first() else {
            return Err(ContractError::UnbalancedPostingGroup);
        };
        if postings.iter().any(|posting| {
            posting.source_id() != first.source_id()
                || posting.currency() != first.currency()
                || posting.event_time() != first.event_time()
        }) {
            return Err(ContractError::UnbalancedPostingGroup);
        }
        let mut debit = 0_i128;
        let mut credit = 0_i128;
        for posting in &postings {
            let total = match posting.debit_credit() {
                DebitCredit::Debit => &mut debit,
                DebitCredit::Credit => &mut credit,
            };
            *total = (*total)
                .checked_add(posting.raw_units())
                .ok_or(ContractError::UnbalancedPostingGroup)?;
        }
        if debit != credit {
            return Err(ContractError::UnbalancedPostingGroup);
        }
        Ok(Self { postings })
    }

    pub fn postings(&self) -> &[LedgerPosting] {
        &self.postings
    }
}

#[derive(Clone, Copy, Debug, PartialEq, Eq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum PositionLotChangeKind {
    Added,
    Reduced,
    Unlocked,
}

#[derive(Clone, Debug, PartialEq, Eq, Serialize)]
pub struct PositionLotChange {
    kind: PositionLotChangeKind,
    lot: Option<PositionLot>,
    lot_id: String,
    quantity: Quantity,
}

impl PositionLotChange {
    pub fn added(lot: PositionLot) -> Self {
        Self {
            kind: PositionLotChangeKind::Added,
            lot_id: lot.lot_id().to_owned(),
            quantity: lot.quantity(),
            lot: Some(lot),
        }
    }

    pub fn reduced(lot_id: impl Into<String>, quantity: i128) -> Result<Self, ContractError> {
        Self::quantity_change(PositionLotChangeKind::Reduced, lot_id, quantity)
    }

    pub fn unlocked(lot_id: impl Into<String>, quantity: i128) -> Result<Self, ContractError> {
        Self::quantity_change(PositionLotChangeKind::Unlocked, lot_id, quantity)
    }

    fn quantity_change(
        kind: PositionLotChangeKind,
        lot_id: impl Into<String>,
        quantity: i128,
    ) -> Result<Self, ContractError> {
        let lot_id = lot_id.into();
        if lot_id.is_empty() {
            return Err(ContractError::InvalidLedgerEffect);
        }
        Ok(Self {
            kind,
            lot: None,
            lot_id,
            quantity: Quantity::positive(quantity)?,
        })
    }
}

#[derive(Clone, Debug, PartialEq, Eq, Serialize)]
pub struct LedgerEffect {
    transition_id: String,
    source_id: String,
    event_time: OffsetDateTime,
    posting_groups: Vec<LedgerPostingGroup>,
    lot_changes: Vec<PositionLotChange>,
    before_state_hash: String,
    after_state_hash: String,
}

impl LedgerEffect {
    pub fn try_new(
        transition_id: impl Into<String>,
        source_id: impl Into<String>,
        event_time: OffsetDateTime,
        posting_groups: Vec<LedgerPostingGroup>,
        lot_changes: Vec<PositionLotChange>,
        before: &AccountSnapshot,
        after: &AccountSnapshot,
    ) -> Result<Self, ContractError> {
        let transition_id = transition_id.into();
        let source_id = source_id.into();
        let before_state_hash = before.state_hash().to_owned();
        let after_state_hash = after.state_hash().to_owned();
        let changed = !posting_groups.is_empty() || !lot_changes.is_empty();
        let cash_delta = posting_groups.iter().try_fold(0_i128, |total, group| {
            group
                .postings()
                .iter()
                .try_fold(total, |subtotal, posting| {
                    if posting.account() != PostingAccount::Cash {
                        return Some(subtotal);
                    }
                    match posting.debit_credit() {
                        DebitCredit::Debit => subtotal.checked_add(posting.raw_units()),
                        DebitCredit::Credit => subtotal.checked_sub(posting.raw_units()),
                    }
                })
        });
        let expected_cash_delta = after
            .nav()
            .cash()
            .scaled()
            .checked_sub(before.nav().cash().scaled());
        let invalid_time = posting_groups
            .iter()
            .flat_map(LedgerPostingGroup::postings)
            .any(|posting| {
                posting.event_time() < before.nav().event_time()
                    || posting.event_time() > after.nav().event_time()
            });
        let after_lots = after
            .lots()
            .iter()
            .map(|lot| (lot.lot_id().to_owned(), lot))
            .collect::<BTreeMap<_, _>>();
        let mut replayed_lots = before
            .lots()
            .iter()
            .cloned()
            .map(|lot| (lot.lot_id().to_owned(), lot))
            .collect::<BTreeMap<_, _>>();
        let lot_replay_valid = lot_changes.iter().all(|change| match change.kind {
            PositionLotChangeKind::Added => {
                let Some(lot) = change.lot.clone() else {
                    return false;
                };
                replayed_lots.insert(change.lot_id.clone(), lot).is_none()
            }
            PositionLotChangeKind::Reduced => {
                let Some(lot) = replayed_lots.get_mut(&change.lot_id) else {
                    return false;
                };
                if change.quantity.units() > lot.quantity.units() {
                    return false;
                }
                let remaining = lot.quantity.units() - change.quantity.units();
                if remaining == 0 {
                    replayed_lots.remove(&change.lot_id);
                } else {
                    lot.quantity = Quantity::positive(remaining).expect("positive remainder");
                }
                true
            }
            PositionLotChangeKind::Unlocked => {
                let (Some(lot), Some(target)) = (
                    replayed_lots.get_mut(&change.lot_id),
                    after_lots.get(&change.lot_id),
                ) else {
                    return false;
                };
                if change.quantity != lot.quantity
                    || lot.sellable_at <= before.nav().event_time()
                    || target.sellable_at > after.nav().event_time()
                {
                    return false;
                }
                lot.sellable_at = target.sellable_at;
                true
            }
        });
        let replayed_lots = replayed_lots.into_values().collect::<Vec<_>>();
        if transition_id.is_empty()
            || source_id.is_empty()
            || event_time < before.nav().event_time()
            || event_time > after.nav().event_time()
            || changed == (before_state_hash == after_state_hash)
            || cash_delta != expected_cash_delta
            || invalid_time
            || !lot_replay_valid
            || replayed_lots != after.lots()
        {
            return Err(ContractError::InvalidLedgerEffect);
        }
        Ok(Self {
            transition_id,
            source_id,
            event_time,
            posting_groups,
            lot_changes,
            before_state_hash,
            after_state_hash,
        })
    }

    pub fn posting_groups(&self) -> &[LedgerPostingGroup] {
        &self.posting_groups
    }

    pub fn transition_id(&self) -> &str {
        &self.transition_id
    }

    pub fn source_id(&self) -> &str {
        &self.source_id
    }

    pub const fn event_time(&self) -> OffsetDateTime {
        self.event_time
    }

    pub fn lot_changes(&self) -> &[PositionLotChange] {
        &self.lot_changes
    }

    pub fn before_state_hash(&self) -> &str {
        &self.before_state_hash
    }

    pub fn after_state_hash(&self) -> &str {
        &self.after_state_hash
    }
}

#[derive(Clone, Debug, PartialEq, Eq, Serialize)]
pub struct PositionLot {
    lot_id: String,
    instrument_id: InstrumentId,
    quantity: Quantity,
    acquired_at: OffsetDateTime,
    sellable_at: OffsetDateTime,
    unit_cost: Fixed8,
}

impl PositionLot {
    #[allow(clippy::too_many_arguments)]
    pub fn try_new(
        lot_id: impl Into<String>,
        instrument_id: impl Into<String>,
        quantity: i128,
        acquired_at: OffsetDateTime,
        sellable_at: OffsetDateTime,
        unit_cost_scaled: i128,
    ) -> Result<Self, ContractError> {
        if sellable_at < acquired_at || unit_cost_scaled < 0 {
            return Err(ContractError::InvalidMonetaryValue);
        }
        Ok(Self {
            lot_id: lot_id.into(),
            instrument_id: InstrumentId(instrument_id.into()),
            quantity: Quantity::positive(quantity)?,
            acquired_at,
            sellable_at,
            unit_cost: Fixed8::from_scaled(unit_cost_scaled)?,
        })
    }

    pub fn lot_id(&self) -> &str {
        &self.lot_id
    }

    pub fn instrument_id(&self) -> &InstrumentId {
        &self.instrument_id
    }

    pub const fn quantity(&self) -> Quantity {
        self.quantity
    }

    pub const fn acquired_at(&self) -> OffsetDateTime {
        self.acquired_at
    }

    pub const fn sellable_at(&self) -> OffsetDateTime {
        self.sellable_at
    }

    pub const fn unit_cost(&self) -> Fixed8 {
        self.unit_cost
    }
}

#[derive(Clone, Debug, PartialEq, Eq, Serialize)]
pub struct NavSnapshot {
    event_time: OffsetDateTime,
    cash: Fixed8,
    market_value: Fixed8,
    net_asset_value: Fixed8,
}

impl NavSnapshot {
    pub fn try_new(
        event_time: OffsetDateTime,
        cash_scaled: i128,
        market_value_scaled: i128,
        net_asset_value_scaled: i128,
    ) -> Result<Self, ContractError> {
        if cash_scaled < 0
            || market_value_scaled < 0
            || net_asset_value_scaled < 0
            || cash_scaled.checked_add(market_value_scaled) != Some(net_asset_value_scaled)
        {
            return Err(ContractError::InvalidMonetaryValue);
        }
        Ok(Self {
            event_time,
            cash: Fixed8::from_scaled(cash_scaled)?,
            market_value: Fixed8::from_scaled(market_value_scaled)?,
            net_asset_value: Fixed8::from_scaled(net_asset_value_scaled)?,
        })
    }

    pub const fn event_time(&self) -> OffsetDateTime {
        self.event_time
    }

    pub const fn market_value(&self) -> Fixed8 {
        self.market_value
    }

    pub const fn cash(&self) -> Fixed8 {
        self.cash
    }

    pub const fn net_asset_value(&self) -> Fixed8 {
        self.net_asset_value
    }
}

#[derive(Clone, Debug, PartialEq, Eq, Serialize)]
pub struct PositionSnapshot {
    event_time: OffsetDateTime,
    instrument_id: InstrumentId,
    quantity: Quantity,
    sellable_quantity: Quantity,
    market_value: Fixed8,
}

impl PositionSnapshot {
    pub fn try_new(
        event_time: OffsetDateTime,
        instrument_id: impl Into<String>,
        quantity: i128,
        sellable_quantity: i128,
        market_value_scaled: i128,
    ) -> Result<Self, ContractError> {
        if sellable_quantity > quantity || market_value_scaled < 0 {
            return Err(ContractError::InvalidMonetaryValue);
        }
        Ok(Self {
            event_time,
            instrument_id: InstrumentId(instrument_id.into()),
            quantity: Quantity::positive(quantity)?,
            sellable_quantity: Quantity::new(sellable_quantity)?,
            market_value: Fixed8::from_scaled(market_value_scaled)?,
        })
    }

    pub const fn event_time(&self) -> OffsetDateTime {
        self.event_time
    }

    pub fn instrument_id(&self) -> &InstrumentId {
        &self.instrument_id
    }

    pub const fn quantity(&self) -> Quantity {
        self.quantity
    }

    pub const fn sellable_quantity(&self) -> Quantity {
        self.sellable_quantity
    }

    pub const fn market_value(&self) -> Fixed8 {
        self.market_value
    }
}

#[derive(Clone, Debug, PartialEq, Eq, Serialize)]
pub struct AccountSnapshot {
    state_hash: String,
    nav: NavSnapshot,
    positions: Vec<PositionSnapshot>,
    lots: Vec<PositionLot>,
}

impl AccountSnapshot {
    pub fn try_new(
        nav: NavSnapshot,
        positions: Vec<PositionSnapshot>,
        lots: Vec<PositionLot>,
    ) -> Result<Self, ContractError> {
        let position_ids = positions
            .iter()
            .map(|position| position.instrument_id().0.as_str())
            .collect::<BTreeSet<_>>();
        let lot_ids = lots
            .iter()
            .map(|lot| lot.lot_id.as_str())
            .collect::<BTreeSet<_>>();
        if position_ids.len() != positions.len()
            || lot_ids.len() != lots.len()
            || positions.windows(2).any(|pair| {
                pair[0].instrument_id().0.as_str() >= pair[1].instrument_id().0.as_str()
            })
            || lots
                .windows(2)
                .any(|pair| pair[0].lot_id.as_str() >= pair[1].lot_id.as_str())
            || positions
                .iter()
                .any(|position| position.event_time() != nav.event_time())
        {
            return Err(ContractError::InvalidAccountSnapshot);
        }
        let market_value = positions.iter().try_fold(0_i128, |total, position| {
            total.checked_add(position.market_value().scaled())
        });
        if market_value != Some(nav.market_value().scaled()) {
            return Err(ContractError::InvalidAccountSnapshot);
        }
        let mut lot_quantities: BTreeMap<&str, i128> = BTreeMap::new();
        let mut sellable_quantities: BTreeMap<&str, i128> = BTreeMap::new();
        for lot in &lots {
            if lot.acquired_at() > nav.event_time() {
                return Err(ContractError::InvalidAccountSnapshot);
            }
            let total = lot_quantities.entry(&lot.instrument_id.0).or_default();
            *total = (*total)
                .checked_add(lot.quantity.units())
                .ok_or(ContractError::InvalidAccountSnapshot)?;
            if lot.sellable_at() <= nav.event_time() {
                let sellable = sellable_quantities.entry(&lot.instrument_id.0).or_default();
                *sellable = (*sellable)
                    .checked_add(lot.quantity.units())
                    .ok_or(ContractError::InvalidAccountSnapshot)?;
            }
        }
        if positions.iter().any(|position| {
            lot_quantities
                .get(position.instrument_id().0.as_str())
                .copied()
                != Some(position.quantity().units())
                || sellable_quantities
                    .get(position.instrument_id().0.as_str())
                    .copied()
                    .unwrap_or(0)
                    != position.sellable_quantity().units()
        }) || lot_quantities.len() != positions.len()
        {
            return Err(ContractError::InvalidAccountSnapshot);
        }
        let position_payload = positions
            .iter()
            .map(|position| {
                (
                    &position.instrument_id.0,
                    position.quantity.units(),
                    position.sellable_quantity.units(),
                    position.market_value.scaled(),
                )
            })
            .collect::<Vec<_>>();
        let lot_payload = lots
            .iter()
            .map(|lot| {
                (
                    &lot.lot_id,
                    &lot.instrument_id.0,
                    lot.quantity.units(),
                    timestamp_micros(lot.acquired_at),
                    timestamp_micros(lot.sellable_at),
                    lot.unit_cost.scaled(),
                )
            })
            .collect::<Vec<_>>();
        let payload = (
            "account-state/v1",
            timestamp_micros(nav.event_time),
            nav.cash.scaled(),
            nav.market_value.scaled(),
            nav.net_asset_value.scaled(),
            position_payload,
            lot_payload,
        );
        let encoded =
            serde_json::to_vec(&payload).map_err(|_| ContractError::InvalidAccountSnapshot)?;
        let state_hash = format!("{:x}", Sha256::digest(encoded));
        Ok(Self {
            state_hash,
            nav,
            positions,
            lots,
        })
    }

    pub fn state_hash(&self) -> &str {
        &self.state_hash
    }

    pub const fn nav(&self) -> &NavSnapshot {
        &self.nav
    }

    pub fn positions(&self) -> &[PositionSnapshot] {
        &self.positions
    }

    pub fn lots(&self) -> &[PositionLot] {
        &self.lots
    }
}

#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct RunArtifactObject {
    artifact_name: String,
    uri: String,
    sha256: String,
    row_count: u64,
}

impl RunArtifactObject {
    pub fn try_new(
        artifact_name: impl Into<String>,
        uri: impl Into<String>,
        sha256: impl Into<String>,
        row_count: u64,
    ) -> Result<Self, ContractError> {
        let value = Self {
            artifact_name: artifact_name.into(),
            uri: uri.into(),
            sha256: sha256.into(),
            row_count,
        };
        value.validate()?;
        Ok(value)
    }

    fn validate(&self) -> Result<(), ContractError> {
        if !run_artifact_schemas().contains_key(&self.artifact_name)
            || self.uri.is_empty()
            || !is_sha256(&self.sha256)
        {
            return Err(ContractError::InvalidHash);
        }
        Ok(())
    }

    pub fn artifact_name(&self) -> &str {
        &self.artifact_name
    }

    pub fn uri(&self) -> &str {
        &self.uri
    }

    pub fn sha256(&self) -> &str {
        &self.sha256
    }

    pub const fn row_count(&self) -> u64 {
        self.row_count
    }
}

#[derive(Clone, Debug, PartialEq, Eq, Serialize)]
pub struct RunManifest {
    manifest_version: String,
    status: String,
    run_id: String,
    #[serde(with = "time::serde::rfc3339")]
    created_at: OffsetDateTime,
    snapshot_id: String,
    strategy_id: String,
    engine_version: String,
    config_hash: String,
    artifact_schema_id: String,
    artifact_schema_sha256: String,
    artifacts: Vec<RunArtifactObject>,
}

impl RunManifest {
    pub fn try_new(
        run_id: impl Into<String>,
        created_at: OffsetDateTime,
        snapshot_id: impl Into<String>,
        strategy_id: impl Into<String>,
        engine_version: impl Into<String>,
        config_hash: impl Into<String>,
        artifacts: Vec<RunArtifactObject>,
    ) -> Result<Self, ContractError> {
        let run_id = run_id.into();
        let snapshot_id = snapshot_id.into();
        let strategy_id = strategy_id.into();
        let engine_version = engine_version.into();
        let config_hash = config_hash.into();
        let artifact_names = artifacts
            .iter()
            .map(|artifact| artifact.artifact_name())
            .collect::<Vec<_>>();
        let schemas = run_artifact_schemas();
        let required_names = schemas.keys().map(String::as_str).collect::<Vec<_>>();
        if run_id.is_empty()
            || strategy_id.is_empty()
            || engine_version.is_empty()
            || !created_at.nanosecond().is_multiple_of(1_000)
            || !is_sha256(&snapshot_id)
            || !is_sha256(&config_hash)
            || artifacts
                .iter()
                .any(|artifact| artifact.validate().is_err())
            || artifact_names != required_names
        {
            return Err(ContractError::InvalidHash);
        }
        Ok(Self {
            manifest_version: "run/v1".to_owned(),
            status: "complete".to_owned(),
            run_id,
            created_at: created_at.to_offset(UtcOffset::UTC),
            snapshot_id,
            strategy_id,
            engine_version,
            config_hash,
            artifact_schema_id: "trademaster.run-artifacts/v1".to_owned(),
            artifact_schema_sha256: run_artifact_schema_sha256(),
            artifacts,
        })
    }

    pub fn artifact_schema_id(&self) -> &str {
        &self.artifact_schema_id
    }

    pub fn artifact_schema_sha256(&self) -> &str {
        &self.artifact_schema_sha256
    }

    pub fn verify_artifacts(
        &self,
        serialized_artifacts: &BTreeMap<String, Vec<u8>>,
    ) -> Result<(), ArtifactArrowError> {
        let mut rows = BTreeMap::new();
        for (name, bytes) in serialized_artifacts {
            rows.insert(name.clone(), artifact_rows_from_parquet(name, bytes)?);
        }
        validate_run_artifact_rows(&rows)?;
        if serialized_artifacts.len() != self.artifacts.len() {
            return Err(run_semantic("manifest artifact content hashes"));
        }
        for artifact in &self.artifacts {
            let Some(artifact_rows) = rows.get(artifact.artifact_name()) else {
                return Err(run_semantic("manifest artifact rows"));
            };
            if artifact.row_count() != artifact_rows.len() as u64
                || serialized_artifacts
                    .get(artifact.artifact_name())
                    .map(|bytes| format!("{:x}", Sha256::digest(bytes)))
                    .as_deref()
                    != Some(artifact.sha256())
                || artifact_rows
                    .iter()
                    .any(|row| row_utf8(row, "run_id") != self.run_id)
            {
                return Err(run_semantic("manifest artifact binding"));
            }
        }
        if rows["signals"].iter().any(|row| {
            row_utf8(row, "strategy_id") != self.strategy_id
                || row_utf8(row, "snapshot_id") != self.snapshot_id
        }) {
            return Err(run_semantic("manifest signal identity"));
        }
        Ok(())
    }
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct RunManifestWire {
    manifest_version: String,
    status: String,
    run_id: String,
    created_at: String,
    snapshot_id: String,
    strategy_id: String,
    engine_version: String,
    config_hash: String,
    artifact_schema_id: String,
    artifact_schema_sha256: String,
    artifacts: Vec<RunArtifactObject>,
}

impl<'de> Deserialize<'de> for RunManifest {
    fn deserialize<D: serde::Deserializer<'de>>(deserializer: D) -> Result<Self, D::Error> {
        let wire = RunManifestWire::deserialize(deserializer)?;
        if wire.manifest_version != "run/v1"
            || wire.status != "complete"
            || wire.artifact_schema_id != "trademaster.run-artifacts/v1"
            || wire.artifact_schema_sha256 != run_artifact_schema_sha256()
        {
            return Err(D::Error::custom("run manifest contract mismatch"));
        }
        if !is_canonical_wire_timestamp(&wire.created_at) {
            return Err(D::Error::custom("non-canonical run timestamp"));
        }
        let created_at =
            OffsetDateTime::parse(&wire.created_at, &Rfc3339).map_err(D::Error::custom)?;
        Self::try_new(
            wire.run_id,
            created_at,
            wire.snapshot_id,
            wire.strategy_id,
            wire.engine_version,
            wire.config_hash,
            wire.artifacts,
        )
        .map_err(D::Error::custom)
    }
}

fn is_canonical_wire_timestamp(value: &str) -> bool {
    let Some(body) = value.strip_suffix('Z') else {
        return false;
    };
    let (base, fraction) = body
        .split_once('.')
        .map_or((body, None), |(base, fraction)| (base, Some(fraction)));
    base.len() == 19
        && base.as_bytes().get(..4) != Some(b"0000")
        && base.as_bytes().get(4) == Some(&b'-')
        && base.as_bytes().get(7) == Some(&b'-')
        && base.as_bytes().get(10) == Some(&b'T')
        && base.as_bytes().get(13) == Some(&b':')
        && base.as_bytes().get(16) == Some(&b':')
        && fraction.is_none_or(|digits| {
            !digits.is_empty()
                && digits.len() <= 6
                && digits.bytes().all(|byte| byte.is_ascii_digit())
        })
}

fn is_sha256(value: &str) -> bool {
    value.len() == 64
        && value
            .bytes()
            .all(|byte| byte.is_ascii_digit() || (b'a'..=b'f').contains(&byte))
}

fn timestamp_micros(value: OffsetDateTime) -> i64 {
    (value.unix_timestamp_nanos() / 1_000)
        .try_into()
        .expect("time crate range fits i64 micros")
}

pub fn run_artifact_schema_sha256() -> String {
    format!("{:x}", Sha256::digest(RUN_ARTIFACT_SCHEMA_JSON.as_bytes()))
}

#[derive(Clone, Debug, PartialEq, Eq, Serialize)]
pub struct StrategyView {
    event_kind: MarketEventKind,
    market: MarketSnapshot,
    history: Vec<MarketSnapshot>,
    portfolio: PortfolioView,
}

impl StrategyView {
    pub fn try_new(
        event_kind: MarketEventKind,
        market: MarketSnapshot,
        history: Vec<MarketSnapshot>,
        portfolio: PortfolioView,
    ) -> Result<Self, ContractError> {
        if portfolio.event_time() != market.event_time()
            || history.iter().any(|item| {
                item.event_time() > market.event_time()
                    || item.snapshot_id() != market.snapshot_id()
            })
            || history
                .windows(2)
                .any(|pair| pair[0].event_time() >= pair[1].event_time())
        {
            return Err(ContractError::InvalidStrategyView);
        }
        Ok(Self {
            event_kind,
            market,
            history,
            portfolio,
        })
    }

    pub const fn event_kind(&self) -> MarketEventKind {
        self.event_kind
    }

    pub const fn market(&self) -> &MarketSnapshot {
        &self.market
    }

    pub fn history(&self) -> &[MarketSnapshot] {
        &self.history
    }

    pub const fn portfolio(&self) -> &PortfolioView {
        &self.portfolio
    }
}

#[derive(Clone, Debug, PartialEq, Eq, Serialize)]
pub struct ValidationView {
    event_kind: MarketEventKind,
    instrument: InstrumentSpec,
    market: MarketSnapshot,
    portfolio: PortfolioView,
}

impl ValidationView {
    pub fn try_new(
        event_kind: MarketEventKind,
        instrument: InstrumentSpec,
        market: MarketSnapshot,
        portfolio: PortfolioView,
    ) -> Result<Self, ContractError> {
        if portfolio.event_time() != market.event_time()
            || !market
                .bars()
                .iter()
                .any(|bar| bar.instrument_id == instrument.instrument_id)
        {
            return Err(ContractError::InvalidStrategyView);
        }
        Ok(Self {
            event_kind,
            instrument,
            market,
            portfolio,
        })
    }

    pub const fn event_kind(&self) -> MarketEventKind {
        self.event_kind
    }

    pub const fn instrument(&self) -> &InstrumentSpec {
        &self.instrument
    }

    pub const fn market(&self) -> &MarketSnapshot {
        &self.market
    }

    pub const fn portfolio(&self) -> &PortfolioView {
        &self.portfolio
    }
}

#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize)]
pub struct SettlementEvent {
    pub event_time: OffsetDateTime,
}

pub fn run_artifact_schemas() -> BTreeMap<String, Schema> {
    let golden: serde_json::Value =
        serde_json::from_str(RUN_ARTIFACT_SCHEMA_JSON).expect("embedded schema must be valid");
    let schema_id = golden["schema_id"]
        .as_str()
        .expect("schema id must be a string");
    golden["records"]
        .as_object()
        .expect("records must be an object")
        .iter()
        .map(|(record_name, field_values)| {
            let fields = field_values
                .as_array()
                .expect("record fields must be an array")
                .iter()
                .map(|value| {
                    Field::new(
                        value["name"].as_str().expect("field name"),
                        artifact_data_type(value["type"].as_str().expect("field type")),
                        value["nullable"].as_bool().expect("field nullable"),
                    )
                })
                .collect::<Vec<_>>();
            let metadata =
                HashMap::from([("trademaster.schema_id".to_owned(), schema_id.to_owned())]);
            (
                record_name.clone(),
                Schema::new_with_metadata(fields, metadata),
            )
        })
        .collect()
}

fn artifact_data_type(name: &str) -> DataType {
    match name {
        "utf8" => DataType::Utf8,
        "timestamp_us_utc" => DataType::Timestamp(TimeUnit::Microsecond, Some("UTC".into())),
        "decimal128_38_8" => DataType::Decimal128(38, 8),
        "decimal128_38_0" => DataType::Decimal128(38, 0),
        "uint8" => DataType::UInt8,
        other => panic!("unsupported artifact type {other}"),
    }
}

#[derive(Clone, Debug, PartialEq, Eq, PartialOrd, Ord)]
pub enum ArtifactScalar {
    Utf8(String),
    TimestampUsUtc(i64),
    Decimal128(i128),
    UInt8(u8),
}

pub type ArtifactRow = BTreeMap<String, ArtifactScalar>;

#[derive(Clone, Debug, PartialEq, Eq)]
pub struct ArtifactSequence {
    run_id: String,
    event_seq: i128,
    parent_seq: i128,
    item_seq: i128,
}

impl ArtifactSequence {
    pub fn try_new(
        run_id: impl Into<String>,
        event_seq: i128,
        item_seq: i128,
    ) -> Result<Self, ExactValueError> {
        Self::try_new_child(run_id, event_seq, item_seq, item_seq)
    }

    pub fn try_new_child(
        run_id: impl Into<String>,
        event_seq: i128,
        parent_seq: i128,
        item_seq: i128,
    ) -> Result<Self, ExactValueError> {
        if event_seq < 0 || parent_seq < 0 || item_seq < 0 {
            return Err(ExactValueError::NegativeQuantity);
        }
        if event_seq >= DECIMAL128_LIMIT
            || parent_seq >= DECIMAL128_LIMIT
            || item_seq >= DECIMAL128_LIMIT
        {
            return Err(ExactValueError::OutOfRange);
        }
        Ok(Self {
            run_id: run_id.into(),
            event_seq,
            parent_seq,
            item_seq,
        })
    }
}

pub trait ArtifactRecord {
    const RECORD_NAME: &'static str;

    fn to_artifact_row(&self, sequence: &ArtifactSequence) -> ArtifactRow;
}

pub fn typed_artifact_record_batch<T: ArtifactRecord>(
    records: &[(ArtifactSequence, T)],
) -> Result<RecordBatch, ArtifactArrowError> {
    let rows = records
        .iter()
        .map(|(sequence, record)| record.to_artifact_row(sequence))
        .collect::<Vec<_>>();
    artifact_record_batch(T::RECORD_NAME, &rows)
}

fn sequenced_row(sequence: &ArtifactSequence, item_name: Option<&str>) -> ArtifactRow {
    let mut row = BTreeMap::from([
        (
            "run_id".to_owned(),
            ArtifactScalar::Utf8(sequence.run_id.clone()),
        ),
        (
            "event_seq".to_owned(),
            ArtifactScalar::Decimal128(sequence.event_seq),
        ),
    ]);
    if let Some(name) = item_name {
        row.insert(
            name.to_owned(),
            ArtifactScalar::Decimal128(sequence.item_seq),
        );
    }
    row
}

fn timestamp_scalar(value: OffsetDateTime) -> ArtifactScalar {
    let micros = value.unix_timestamp_nanos() / 1_000;
    ArtifactScalar::TimestampUsUtc(micros.try_into().expect("time crate range fits i64 micros"))
}

fn utf8_scalar(value: impl Into<String>) -> ArtifactScalar {
    ArtifactScalar::Utf8(value.into())
}

impl ArtifactRecord for SignalRecord {
    const RECORD_NAME: &'static str = "signals";

    fn to_artifact_row(&self, sequence: &ArtifactSequence) -> ArtifactRow {
        let mut row = sequenced_row(sequence, Some("signal_seq"));
        row.extend([
            ("signal_id".into(), utf8_scalar(&self.signal_id)),
            ("strategy_id".into(), utf8_scalar(&self.strategy_id)),
            ("instrument_id".into(), utf8_scalar(&self.instrument_id.0)),
            ("signal_time".into(), timestamp_scalar(self.signal_time)),
            (
                "eligible_execution_time".into(),
                timestamp_scalar(self.eligible_execution_time),
            ),
            (
                "intent_type".into(),
                utf8_scalar(match self.intent_type {
                    IntentType::TargetWeight => "target_weight",
                    IntentType::Quantity => "quantity",
                }),
            ),
            (
                "value".into(),
                ArtifactScalar::Decimal128(self.value.scaled()),
            ),
            ("reason".into(), utf8_scalar(&self.reason)),
            ("snapshot_id".into(), utf8_scalar(&self.snapshot_id)),
        ]);
        row
    }
}

impl ArtifactRecord for SubmittedOrder {
    const RECORD_NAME: &'static str = "orders";

    fn to_artifact_row(&self, sequence: &ArtifactSequence) -> ArtifactRow {
        let intent = self.intent();
        let mut row = sequenced_row(sequence, Some("order_seq"));
        row.extend([
            ("order_id".into(), utf8_scalar(intent.order_id())),
            ("signal_id".into(), utf8_scalar(intent.signal_id())),
            (
                "instrument_id".into(),
                utf8_scalar(&intent.instrument_id().0),
            ),
            (
                "side".into(),
                utf8_scalar(match intent.side() {
                    Side::Buy => "buy",
                    Side::Sell => "sell",
                }),
            ),
            (
                "requested_quantity".into(),
                ArtifactScalar::Decimal128(intent.quantity().units()),
            ),
            ("submitted_at".into(), timestamp_scalar(self.submitted_at())),
            (
                "eligible_at".into(),
                timestamp_scalar(intent.eligible_execution_time()),
            ),
        ]);
        row
    }
}

impl ArtifactRecord for AcceptedOrder {
    const RECORD_NAME: &'static str = "accepted_orders";

    fn to_artifact_row(&self, sequence: &ArtifactSequence) -> ArtifactRow {
        let mut row = sequenced_row(sequence, Some("order_seq"));
        row.extend([
            ("order_id".into(), utf8_scalar(self.order_id())),
            ("accepted_at".into(), timestamp_scalar(self.accepted_at())),
        ]);
        row
    }
}

impl ArtifactRecord for Fill {
    const RECORD_NAME: &'static str = "fills";

    fn to_artifact_row(&self, sequence: &ArtifactSequence) -> ArtifactRow {
        let mut row = sequenced_row(sequence, Some("fill_seq"));
        row.insert(
            "order_seq".into(),
            ArtifactScalar::Decimal128(sequence.parent_seq),
        );
        row.extend([
            ("fill_id".into(), utf8_scalar(&self.fill_id)),
            ("order_id".into(), utf8_scalar(&self.order_id)),
            ("event_time".into(), timestamp_scalar(self.event_time)),
            (
                "quantity".into(),
                ArtifactScalar::Decimal128(self.quantity.units()),
            ),
            (
                "price".into(),
                ArtifactScalar::Decimal128(self.price.scaled()),
            ),
            (
                "commission".into(),
                ArtifactScalar::Decimal128(self.commission.scaled()),
            ),
            ("tax".into(), ArtifactScalar::Decimal128(self.tax.scaled())),
            (
                "transfer_fee".into(),
                ArtifactScalar::Decimal128(self.transfer_fee.scaled()),
            ),
            (
                "slippage".into(),
                ArtifactScalar::Decimal128(self.slippage.scaled()),
            ),
        ]);
        row
    }
}

impl ArtifactRecord for Rejection {
    const RECORD_NAME: &'static str = "rejections";

    fn to_artifact_row(&self, sequence: &ArtifactSequence) -> ArtifactRow {
        let mut row = sequenced_row(sequence, Some("order_seq"));
        row.extend([
            ("order_id".into(), utf8_scalar(&self.order_id)),
            ("event_time".into(), timestamp_scalar(self.event_time)),
            (
                "code".into(),
                utf8_scalar(match self.code {
                    RejectionCode::InvalidTime => "invalid_time",
                    RejectionCode::InvalidQuantity => "invalid_quantity",
                    RejectionCode::Suspended => "suspended",
                    RejectionCode::LimitUp => "limit_up",
                    RejectionCode::LimitDown => "limit_down",
                    RejectionCode::InsufficientCash => "insufficient_cash",
                    RejectionCode::InsufficientSellable => "insufficient_sellable",
                    RejectionCode::MissingMarketData => "missing_market_data",
                }),
            ),
            ("message".into(), utf8_scalar(&self.message)),
        ]);
        row
    }
}

impl ArtifactRecord for Expiry {
    const RECORD_NAME: &'static str = "expiries";

    fn to_artifact_row(&self, sequence: &ArtifactSequence) -> ArtifactRow {
        let mut row = sequenced_row(sequence, Some("order_seq"));
        row.extend([
            ("order_id".into(), utf8_scalar(self.order_id())),
            ("event_time".into(), timestamp_scalar(self.event_time())),
            (
                "remaining_quantity".into(),
                ArtifactScalar::Decimal128(self.remaining_quantity().units()),
            ),
            ("reason".into(), utf8_scalar(self.reason())),
        ]);
        row
    }
}

impl ArtifactRecord for LedgerPosting {
    const RECORD_NAME: &'static str = "ledger_postings";

    fn to_artifact_row(&self, sequence: &ArtifactSequence) -> ArtifactRow {
        let mut row = sequenced_row(sequence, Some("posting_seq"));
        row.extend([
            ("posting_id".into(), utf8_scalar(self.posting_id())),
            ("event_time".into(), timestamp_scalar(self.event_time())),
            (
                "account".into(),
                utf8_scalar(match self.account() {
                    PostingAccount::Cash => "cash",
                    PostingAccount::Position => "position",
                    PostingAccount::CommissionExpense => "commission_expense",
                    PostingAccount::TaxExpense => "tax_expense",
                    PostingAccount::TransferFeeExpense => "transfer_fee_expense",
                    PostingAccount::SlippageExpense => "slippage_expense",
                }),
            ),
            (
                "debit_credit".into(),
                utf8_scalar(match self.debit_credit() {
                    DebitCredit::Debit => "debit",
                    DebitCredit::Credit => "credit",
                }),
            ),
            ("unit_type".into(), utf8_scalar("currency")),
            ("unit_id".into(), utf8_scalar(self.currency())),
            (
                "raw_units".into(),
                ArtifactScalar::Decimal128(self.raw_units()),
            ),
            ("scale".into(), ArtifactScalar::UInt8(8)),
            ("source_id".into(), utf8_scalar(self.source_id())),
        ]);
        row
    }
}

impl ArtifactRecord for LedgerEffect {
    const RECORD_NAME: &'static str = "account_states";

    fn to_artifact_row(&self, sequence: &ArtifactSequence) -> ArtifactRow {
        let mut row = sequenced_row(sequence, Some("transition_seq"));
        row.extend([
            ("transition_id".into(), utf8_scalar(self.transition_id())),
            ("source_id".into(), utf8_scalar(self.source_id())),
            ("event_time".into(), timestamp_scalar(self.event_time())),
            (
                "before_state_hash".into(),
                utf8_scalar(self.before_state_hash()),
            ),
            (
                "after_state_hash".into(),
                utf8_scalar(self.after_state_hash()),
            ),
        ]);
        row
    }
}

impl ArtifactRecord for PositionLot {
    const RECORD_NAME: &'static str = "position_lots";

    fn to_artifact_row(&self, sequence: &ArtifactSequence) -> ArtifactRow {
        let mut row = sequenced_row(sequence, None);
        row.extend([
            ("lot_id".into(), utf8_scalar(&self.lot_id)),
            ("instrument_id".into(), utf8_scalar(&self.instrument_id.0)),
            (
                "quantity".into(),
                ArtifactScalar::Decimal128(self.quantity.units()),
            ),
            ("acquired_at".into(), timestamp_scalar(self.acquired_at)),
            ("sellable_at".into(), timestamp_scalar(self.sellable_at)),
            (
                "unit_cost".into(),
                ArtifactScalar::Decimal128(self.unit_cost.scaled()),
            ),
        ]);
        row
    }
}

impl ArtifactRecord for PositionSnapshot {
    const RECORD_NAME: &'static str = "positions";

    fn to_artifact_row(&self, sequence: &ArtifactSequence) -> ArtifactRow {
        let mut row = sequenced_row(sequence, None);
        row.extend([
            ("event_time".into(), timestamp_scalar(self.event_time)),
            ("instrument_id".into(), utf8_scalar(&self.instrument_id.0)),
            (
                "quantity".into(),
                ArtifactScalar::Decimal128(self.quantity.units()),
            ),
            (
                "sellable_quantity".into(),
                ArtifactScalar::Decimal128(self.sellable_quantity.units()),
            ),
            (
                "market_value".into(),
                ArtifactScalar::Decimal128(self.market_value.scaled()),
            ),
        ]);
        row
    }
}

impl ArtifactRecord for NavSnapshot {
    const RECORD_NAME: &'static str = "nav";

    fn to_artifact_row(&self, sequence: &ArtifactSequence) -> ArtifactRow {
        let mut row = sequenced_row(sequence, None);
        row.extend([
            ("event_time".into(), timestamp_scalar(self.event_time)),
            (
                "cash".into(),
                ArtifactScalar::Decimal128(self.cash.scaled()),
            ),
            (
                "market_value".into(),
                ArtifactScalar::Decimal128(self.market_value.scaled()),
            ),
            (
                "net_asset_value".into(),
                ArtifactScalar::Decimal128(self.net_asset_value.scaled()),
            ),
        ]);
        row
    }
}

#[derive(Clone, Debug, Error, PartialEq, Eq)]
pub enum ArtifactArrowError {
    #[error("unknown artifact record: {0}")]
    UnknownRecord(String),
    #[error("artifact row {row} differs at field {field}")]
    FieldSetMismatch { row: usize, field: String },
    #[error("artifact row {row} field {field} does not match {expected:?}")]
    TypeMismatch {
        row: usize,
        field: String,
        expected: DataType,
    },
    #[error("failed to construct Arrow artifact: {0}")]
    Arrow(String),
    #[error("artifact row {row} violates semantic contract for {field}")]
    Semantic { row: usize, field: String },
}

/// Materialize exact Rust values as a real Arrow batch governed by the shared schema.
///
/// This boundary fails closed on unknown records, missing or extra fields, and scalar type
/// mismatches. It is the sole generic adapter used by artifact persistence implementations.
pub fn artifact_record_batch(
    record_name: &str,
    rows: &[ArtifactRow],
) -> Result<RecordBatch, ArtifactArrowError> {
    let schemas = run_artifact_schemas();
    let schema = schemas
        .get(record_name)
        .cloned()
        .ok_or_else(|| ArtifactArrowError::UnknownRecord(record_name.to_owned()))?;
    let expected_names: BTreeSet<&str> = schema
        .fields()
        .iter()
        .map(|field| field.name().as_str())
        .collect();
    for (row_index, row) in rows.iter().enumerate() {
        let actual_names: BTreeSet<&str> = row.keys().map(String::as_str).collect();
        if actual_names != expected_names {
            let field = expected_names
                .symmetric_difference(&actual_names)
                .next()
                .copied()
                .unwrap_or("<unknown>")
                .to_owned();
            return Err(ArtifactArrowError::FieldSetMismatch {
                row: row_index,
                field,
            });
        }
        validate_artifact_semantics(record_name, row_index, row)?;
    }
    let mut columns: Vec<ArrayRef> = Vec::with_capacity(schema.fields().len());
    for field in schema.fields() {
        let values: Vec<&ArtifactScalar> = rows
            .iter()
            .map(|row| row.get(field.name()).expect("field set validated"))
            .collect();
        columns.push(artifact_column(field, &values)?);
    }
    validate_artifact_batch_semantics(record_name, rows)?;
    RecordBatch::try_new(Arc::new(schema), columns)
        .map_err(|error| ArtifactArrowError::Arrow(error.to_string()))
}

fn artifact_rows_from_parquet(
    record_name: &str,
    bytes: &[u8],
) -> Result<Vec<ArtifactRow>, ArtifactArrowError> {
    let expected = run_artifact_schemas()
        .remove(record_name)
        .ok_or_else(|| ArtifactArrowError::UnknownRecord(record_name.to_owned()))?;
    let builder = ParquetRecordBatchReaderBuilder::try_new(Bytes::copy_from_slice(bytes))
        .map_err(|error| ArtifactArrowError::Arrow(error.to_string()))?;
    if builder.schema().as_ref() != &expected {
        return Err(ArtifactArrowError::Arrow(format!(
            "Parquet schema mismatch for {record_name}"
        )));
    }
    let reader = builder
        .build()
        .map_err(|error| ArtifactArrowError::Arrow(error.to_string()))?;
    let mut rows = Vec::new();
    for batch in reader {
        let batch = batch.map_err(|error| ArtifactArrowError::Arrow(error.to_string()))?;
        for row_index in 0..batch.num_rows() {
            let mut row = BTreeMap::new();
            for (column_index, field) in expected.fields().iter().enumerate() {
                let column = batch.column(column_index);
                if column.is_null(row_index) {
                    return Err(ArtifactArrowError::Semantic {
                        row: row_index,
                        field: field.name().clone(),
                    });
                }
                let value = match field.data_type() {
                    DataType::Utf8 => ArtifactScalar::Utf8(
                        column
                            .as_any()
                            .downcast_ref::<StringArray>()
                            .expect("schema checked")
                            .value(row_index)
                            .to_owned(),
                    ),
                    DataType::Timestamp(TimeUnit::Microsecond, timezone)
                        if timezone.as_deref() == Some("UTC") =>
                    {
                        ArtifactScalar::TimestampUsUtc(
                            column
                                .as_any()
                                .downcast_ref::<TimestampMicrosecondArray>()
                                .expect("schema checked")
                                .value(row_index),
                        )
                    }
                    DataType::Decimal128(_, _) => ArtifactScalar::Decimal128(
                        column
                            .as_any()
                            .downcast_ref::<Decimal128Array>()
                            .expect("schema checked")
                            .value(row_index),
                    ),
                    DataType::UInt8 => ArtifactScalar::UInt8(
                        column
                            .as_any()
                            .downcast_ref::<UInt8Array>()
                            .expect("schema checked")
                            .value(row_index),
                    ),
                    other => {
                        return Err(ArtifactArrowError::TypeMismatch {
                            row: row_index,
                            field: field.name().clone(),
                            expected: other.clone(),
                        });
                    }
                };
                row.insert(field.name().clone(), value);
            }
            rows.push(row);
        }
    }
    Ok(rows)
}

/// Validate all eleven tables as one completed run, including cross-table lifecycle rules.
pub fn validate_run_artifact_rows(
    artifacts: &BTreeMap<String, Vec<ArtifactRow>>,
) -> Result<(), ArtifactArrowError> {
    let required = run_artifact_schemas()
        .keys()
        .cloned()
        .collect::<BTreeSet<_>>();
    if artifacts.keys().cloned().collect::<BTreeSet<_>>() != required {
        return Err(run_semantic("all eleven artifact tables"));
    }
    for (name, rows) in artifacts {
        artifact_record_batch(name, rows)?;
    }
    let run_ids = artifacts
        .values()
        .flatten()
        .map(|row| row_utf8(row, "run_id"))
        .collect::<BTreeSet<_>>();
    if run_ids.len() > 1 {
        return Err(run_semantic("single run_id"));
    }

    let indexed = |name: &str, id: &str| {
        artifacts[name]
            .iter()
            .map(|row| (row_utf8(row, id), row))
            .collect::<BTreeMap<_, _>>()
    };
    let signals = indexed("signals", "signal_id");
    let orders = indexed("orders", "order_id");
    let accepted = indexed("accepted_orders", "order_id");
    let rejected = indexed("rejections", "order_id");
    let expiries = indexed("expiries", "order_id");

    if orders.values().any(|order| {
        let Some(signal) = signals.get(row_utf8(order, "signal_id")) else {
            return true;
        };
        row_utf8(order, "instrument_id") != row_utf8(signal, "instrument_id")
            || row_time(order, "eligible_at") != row_time(signal, "eligible_execution_time")
            || row_time(order, "submitted_at") < row_time(signal, "eligible_execution_time")
    }) || orders
        .keys()
        .any(|id| accepted.contains_key(id) == rejected.contains_key(id))
        || accepted
            .keys()
            .chain(rejected.keys())
            .any(|id| !orders.contains_key(id))
    {
        return Err(run_semantic("order admission lifecycle"));
    }
    for (order_id, admission) in &accepted {
        if row_time(admission, "accepted_at") < row_time(orders[order_id], "submitted_at")
            || row_decimal(admission, "event_seq") != row_decimal(orders[order_id], "event_seq")
            || row_decimal(admission, "order_seq") != row_decimal(orders[order_id], "order_seq")
        {
            return Err(run_semantic("accepted order chronology"));
        }
    }
    for (order_id, rejection) in &rejected {
        if row_time(rejection, "event_time") < row_time(orders[order_id], "submitted_at")
            || row_decimal(rejection, "event_seq") != row_decimal(orders[order_id], "event_seq")
            || row_decimal(rejection, "order_seq") != row_decimal(orders[order_id], "order_seq")
        {
            return Err(run_semantic("rejection chronology"));
        }
    }

    let mut fills_by_order: BTreeMap<&str, Vec<&ArtifactRow>> = BTreeMap::new();
    for fill in &artifacts["fills"] {
        let order_id = row_utf8(fill, "order_id");
        let Some(admission) = accepted.get(order_id) else {
            return Err(run_semantic("fill accepted-order foreign key"));
        };
        if row_time(fill, "event_time") < row_time(admission, "accepted_at") {
            return Err(run_semantic("fill chronology"));
        }
        if row_decimal(fill, "event_seq") != row_decimal(orders[order_id], "event_seq")
            || row_decimal(fill, "order_seq") != row_decimal(orders[order_id], "order_seq")
        {
            return Err(run_semantic("fill sequence"));
        }
        fills_by_order.entry(order_id).or_default().push(fill);
    }
    if expiries.keys().any(|id| !accepted.contains_key(id)) {
        return Err(run_semantic("expiry accepted-order foreign key"));
    }
    if expiries.iter().any(|(order_id, expiry)| {
        row_decimal(expiry, "event_seq") != row_decimal(orders[order_id], "event_seq")
            || row_decimal(expiry, "order_seq") != row_decimal(orders[order_id], "order_seq")
    }) {
        return Err(run_semantic("expiry sequence"));
    }
    for (order_id, admission) in &accepted {
        let requested = row_decimal(orders[order_id], "requested_quantity");
        let fills = fills_by_order
            .get(order_id)
            .map(Vec::as_slice)
            .unwrap_or(&[]);
        if fills
            .windows(2)
            .any(|pair| row_time(pair[1], "event_time") < row_time(pair[0], "event_time"))
        {
            return Err(run_semantic("fill event-time monotonicity"));
        }
        let filled = fills.iter().try_fold(0_i128, |total, fill| {
            total.checked_add(row_decimal(fill, "quantity"))
        });
        let Some(filled) = filled else {
            return Err(run_semantic("fill quantity overflow"));
        };
        match (filled.cmp(&requested), expiries.get(order_id)) {
            (std::cmp::Ordering::Equal, None) => {}
            (std::cmp::Ordering::Less, Some(expiry))
                if row_decimal(expiry, "remaining_quantity") == requested - filled
                    && row_time(expiry, "event_time") >= row_time(admission, "accepted_at")
                    && fills.last().is_none_or(|fill| {
                        row_time(expiry, "event_time") >= row_time(fill, "event_time")
                    }) => {}
            _ => return Err(run_semantic("accepted order terminal conservation")),
        }
    }
    let fill_rows = artifacts["fills"]
        .iter()
        .map(|row| (row_utf8(row, "fill_id"), row))
        .collect::<BTreeMap<_, _>>();
    let mut posting_counts: BTreeMap<&str, usize> = BTreeMap::new();
    if artifacts["ledger_postings"].iter().any(|row| {
        let source = row_utf8(row, "source_id");
        let Some(fill) = fill_rows.get(source) else {
            return true;
        };
        *posting_counts.entry(source).or_default() += 1;
        row_decimal(row, "event_seq") != row_decimal(fill, "event_seq")
            || row_time(row, "event_time") != row_time(fill, "event_time")
    }) {
        return Err(run_semantic("ledger source foreign key"));
    }
    if fill_rows
        .keys()
        .any(|fill_id| posting_counts.get(fill_id).copied().unwrap_or(0) < 2)
    {
        return Err(run_semantic("fill ledger postings"));
    }
    let nav_events = artifacts["nav"]
        .iter()
        .map(|row| row_decimal(row, "event_seq"))
        .collect::<BTreeSet<_>>();
    if fill_rows
        .values()
        .any(|fill| !nav_events.contains(&row_decimal(fill, "event_seq")))
    {
        return Err(run_semantic("fill NAV snapshot"));
    }
    let mut transitions_by_source: BTreeMap<&str, Vec<&ArtifactRow>> = BTreeMap::new();
    for transition in &artifacts["account_states"] {
        let source = row_utf8(transition, "source_id");
        if !accepted.contains_key(source) && !source.starts_with("settlement:") {
            return Err(run_semantic("account transition source"));
        }
        transitions_by_source
            .entry(source)
            .or_default()
            .push(transition);
    }
    for (order_id, fills) in &fills_by_order {
        let Some(transitions) = transitions_by_source.get(order_id) else {
            return Err(run_semantic("executed order account transition"));
        };
        let last_fill_time = fills
            .iter()
            .map(|fill| row_time(fill, "event_time"))
            .max()
            .expect("executed order has fills");
        if transitions.len() != 1
            || row_decimal(transitions[0], "event_seq")
                != row_decimal(orders[order_id], "event_seq")
            || row_time(transitions[0], "event_time") != last_fill_time
            || row_utf8(transitions[0], "before_state_hash")
                == row_utf8(transitions[0], "after_state_hash")
        {
            return Err(run_semantic("executed order account transition"));
        }
    }
    if artifacts["account_states"].windows(2).any(|pair| {
        row_utf8(&pair[0], "after_state_hash") != row_utf8(&pair[1], "before_state_hash")
            || row_time(&pair[1], "event_time") < row_time(&pair[0], "event_time")
    }) {
        return Err(run_semantic("account state hash chain"));
    }
    let event_times = fill_rows
        .values()
        .map(|fill| (row_decimal(fill, "event_seq"), row_time(fill, "event_time")))
        .chain(artifacts["account_states"].iter().map(|transition| {
            (
                row_decimal(transition, "event_seq"),
                row_time(transition, "event_time"),
            )
        }))
        .collect::<BTreeSet<_>>()
        .into_iter()
        .collect::<Vec<_>>();
    if event_times.windows(2).any(|pair| pair[1].1 < pair[0].1) {
        return Err(run_semantic("global event-time monotonicity"));
    }
    let mut lots_by_event: BTreeMap<Vec<ArtifactScalar>, Vec<&ArtifactRow>> = BTreeMap::new();
    for lot in &artifacts["position_lots"] {
        let key = vec![
            lot["run_id"].clone(),
            lot["event_seq"].clone(),
            lot["instrument_id"].clone(),
        ];
        lots_by_event.entry(key).or_default().push(lot);
    }
    for position in &artifacts["positions"] {
        let key = vec![
            position["run_id"].clone(),
            position["event_seq"].clone(),
            position["instrument_id"].clone(),
        ];
        let lots = lots_by_event.remove(&key).unwrap_or_default();
        let event_time = row_time(position, "event_time");
        if lots
            .iter()
            .any(|lot| row_time(lot, "acquired_at") > event_time)
        {
            return Err(run_semantic("future position lot"));
        }
        let quantity = lots.iter().try_fold(0_i128, |total, lot| {
            total.checked_add(row_decimal(lot, "quantity"))
        });
        let sellable = lots
            .iter()
            .filter(|lot| row_time(lot, "sellable_at") <= event_time)
            .try_fold(0_i128, |total, lot| {
                total.checked_add(row_decimal(lot, "quantity"))
            });
        if quantity != Some(row_decimal(position, "quantity"))
            || sellable != Some(row_decimal(position, "sellable_quantity"))
        {
            return Err(run_semantic("position lot quantities"));
        }
    }
    if !lots_by_event.is_empty() {
        return Err(run_semantic("orphan position lot snapshot"));
    }
    let nav_by_event = artifacts["nav"]
        .iter()
        .map(|row| (row_decimal(row, "event_seq"), row))
        .collect::<BTreeMap<_, _>>();
    let mut transitions_by_event: BTreeMap<i128, Vec<&ArtifactRow>> = BTreeMap::new();
    for transition in &artifacts["account_states"] {
        transitions_by_event
            .entry(row_decimal(transition, "event_seq"))
            .or_default()
            .push(transition);
    }
    for (event_seq, transitions) in transitions_by_event {
        let Some(nav) = nav_by_event.get(&event_seq) else {
            return Err(run_semantic("transition NAV snapshot"));
        };
        if row_decimal(nav, "cash").checked_add(row_decimal(nav, "market_value"))
            != Some(row_decimal(nav, "net_asset_value"))
        {
            return Err(run_semantic("NAV identity"));
        }
        let positions = artifacts["positions"]
            .iter()
            .filter(|row| row_decimal(row, "event_seq") == event_seq)
            .collect::<Vec<_>>();
        let lots = artifacts["position_lots"]
            .iter()
            .filter(|row| row_decimal(row, "event_seq") == event_seq)
            .collect::<Vec<_>>();
        let position_market_value = positions.iter().try_fold(0_i128, |total, row| {
            total.checked_add(row_decimal(row, "market_value"))
        });
        if position_market_value != Some(row_decimal(nav, "market_value"))
            || positions
                .iter()
                .any(|row| row_time(row, "event_time") != row_time(nav, "event_time"))
            || lots
                .iter()
                .any(|row| row_time(row, "acquired_at") > row_time(nav, "event_time"))
        {
            return Err(run_semantic("account artifact valuation time"));
        }
        let actual_hash = artifact_account_state_hash(nav, &positions, &lots)?;
        if transitions
            .last()
            .is_none_or(|transition| row_utf8(transition, "after_state_hash") != actual_hash)
        {
            return Err(run_semantic("account state content hash"));
        }
    }
    Ok(())
}

fn artifact_account_state_hash(
    nav: &ArtifactRow,
    positions: &[&ArtifactRow],
    lots: &[&ArtifactRow],
) -> Result<String, ArtifactArrowError> {
    let position_payload = positions
        .iter()
        .map(|row| {
            serde_json::json!([
                row_utf8(row, "instrument_id"),
                row_decimal(row, "quantity"),
                row_decimal(row, "sellable_quantity"),
                row_decimal(row, "market_value")
            ])
        })
        .collect::<Vec<_>>();
    let lot_payload = lots
        .iter()
        .map(|row| {
            serde_json::json!([
                row_utf8(row, "lot_id"),
                row_utf8(row, "instrument_id"),
                row_decimal(row, "quantity"),
                row_time(row, "acquired_at"),
                row_time(row, "sellable_at"),
                row_decimal(row, "unit_cost")
            ])
        })
        .collect::<Vec<_>>();
    let payload = serde_json::json!([
        "account-state/v1",
        row_time(nav, "event_time"),
        row_decimal(nav, "cash"),
        row_decimal(nav, "market_value"),
        row_decimal(nav, "net_asset_value"),
        position_payload,
        lot_payload
    ]);
    let encoded = serde_json::to_vec(&payload)
        .map_err(|error| ArtifactArrowError::Arrow(error.to_string()))?;
    Ok(format!("{:x}", Sha256::digest(encoded)))
}

fn row_utf8<'a>(row: &'a ArtifactRow, field: &str) -> &'a str {
    match &row[field] {
        ArtifactScalar::Utf8(value) => value,
        _ => unreachable!("artifact schema validated"),
    }
}

fn row_time(row: &ArtifactRow, field: &str) -> i64 {
    match row[field] {
        ArtifactScalar::TimestampUsUtc(value) => value,
        _ => unreachable!("artifact schema validated"),
    }
}

fn row_decimal(row: &ArtifactRow, field: &str) -> i128 {
    match row[field] {
        ArtifactScalar::Decimal128(value) => value,
        _ => unreachable!("artifact schema validated"),
    }
}

fn run_semantic(field: &str) -> ArtifactArrowError {
    ArtifactArrowError::Semantic {
        row: 0,
        field: field.to_owned(),
    }
}

fn validate_artifact_semantics(
    record_name: &str,
    row_index: usize,
    row: &ArtifactRow,
) -> Result<(), ArtifactArrowError> {
    let golden: serde_json::Value =
        serde_json::from_str(RUN_ARTIFACT_SCHEMA_JSON).expect("embedded schema must be valid");
    for field in golden["records"][record_name]
        .as_array()
        .expect("known record fields")
    {
        let Some(enum_name) = field.get("enum").and_then(serde_json::Value::as_str) else {
            continue;
        };
        let field_name = field["name"].as_str().expect("field name");
        let allowed = golden["enums"][enum_name].as_array().expect("enum values");
        let valid = match row.get(field_name) {
            Some(ArtifactScalar::Utf8(value)) => {
                allowed.iter().any(|item| item.as_str() == Some(value))
            }
            _ => false,
        };
        if !valid {
            return Err(ArtifactArrowError::Semantic {
                row: row_index,
                field: field_name.to_owned(),
            });
        }
    }
    let constraints = &golden["constraints"][record_name];
    for field_name in constraints["positive"].as_array().into_iter().flatten() {
        let field_name = field_name.as_str().expect("positive field name");
        if artifact_integer(row.get(field_name)) <= Some(0) {
            return Err(ArtifactArrowError::Semantic {
                row: row_index,
                field: field_name.to_owned(),
            });
        }
    }
    for field_name in constraints["non_negative"].as_array().into_iter().flatten() {
        let field_name = field_name.as_str().expect("non-negative field name");
        if artifact_integer(row.get(field_name)) < Some(0) {
            return Err(ArtifactArrowError::Semantic {
                row: row_index,
                field: field_name.to_owned(),
            });
        }
    }
    if let Some(fixed) = constraints["fixed"].as_object() {
        for (field_name, expected) in fixed {
            let matches = match row.get(field_name) {
                Some(ArtifactScalar::Utf8(value)) => expected.as_str() == Some(value),
                Some(ArtifactScalar::UInt8(value)) => expected.as_u64() == Some(u64::from(*value)),
                Some(ArtifactScalar::Decimal128(value)) => {
                    expected.as_i64().map(i128::from) == Some(*value)
                }
                _ => false,
            };
            if !matches {
                return Err(ArtifactArrowError::Semantic {
                    row: row_index,
                    field: field_name.clone(),
                });
            }
        }
    }
    for field_name in constraints["sha256"].as_array().into_iter().flatten() {
        let field_name = field_name.as_str().expect("sha256 field name");
        let valid =
            matches!(row.get(field_name), Some(ArtifactScalar::Utf8(value)) if is_sha256(value));
        if !valid {
            return Err(ArtifactArrowError::Semantic {
                row: row_index,
                field: field_name.to_owned(),
            });
        }
    }
    validate_artifact_time_pairs(row, row_index, &constraints["time_order"], false)?;
    validate_artifact_time_pairs(row, row_index, &constraints["strict_time_order"], true)?;
    if record_name == "nav"
        && artifact_integer(row.get("cash")).and_then(|cash| {
            artifact_integer(row.get("market_value")).and_then(|market| cash.checked_add(market))
        }) != artifact_integer(row.get("net_asset_value"))
    {
        return Err(ArtifactArrowError::Semantic {
            row: row_index,
            field: "NAV identity".to_owned(),
        });
    }
    if record_name == "positions"
        && artifact_integer(row.get("sellable_quantity")) > artifact_integer(row.get("quantity"))
    {
        return Err(ArtifactArrowError::Semantic {
            row: row_index,
            field: "sellable quantity".to_owned(),
        });
    }
    for (field_name, value) in row {
        if field_name.ends_with("_seq")
            && !matches!(value, ArtifactScalar::Decimal128(sequence) if *sequence >= 0)
        {
            return Err(ArtifactArrowError::Semantic {
                row: row_index,
                field: field_name.clone(),
            });
        }
    }
    Ok(())
}

fn artifact_integer(value: Option<&ArtifactScalar>) -> Option<i128> {
    match value {
        Some(ArtifactScalar::Decimal128(value)) => Some(*value),
        Some(ArtifactScalar::UInt8(value)) => Some(i128::from(*value)),
        _ => None,
    }
}

fn validate_artifact_time_pairs(
    row: &ArtifactRow,
    row_index: usize,
    pairs: &serde_json::Value,
    strict: bool,
) -> Result<(), ArtifactArrowError> {
    for pair in pairs.as_array().into_iter().flatten() {
        let pair = pair.as_array().expect("time relation pair");
        let earlier_name = pair[0].as_str().expect("earlier field");
        let later_name = pair[1].as_str().expect("later field");
        let earlier = match row.get(earlier_name) {
            Some(ArtifactScalar::TimestampUsUtc(value)) => *value,
            _ => {
                return Err(ArtifactArrowError::Semantic {
                    row: row_index,
                    field: earlier_name.to_owned(),
                });
            }
        };
        let later = match row.get(later_name) {
            Some(ArtifactScalar::TimestampUsUtc(value)) => *value,
            _ => {
                return Err(ArtifactArrowError::Semantic {
                    row: row_index,
                    field: later_name.to_owned(),
                });
            }
        };
        if (strict && earlier >= later) || (!strict && earlier > later) {
            return Err(ArtifactArrowError::Semantic {
                row: row_index,
                field: format!("{earlier_name},{later_name}"),
            });
        }
    }
    Ok(())
}

fn validate_artifact_batch_semantics(
    record_name: &str,
    rows: &[ArtifactRow],
) -> Result<(), ArtifactArrowError> {
    let golden: serde_json::Value =
        serde_json::from_str(RUN_ARTIFACT_SCHEMA_JSON).expect("embedded schema must be valid");
    let sort_keys = golden["sort_keys"][record_name]
        .as_array()
        .ok_or_else(|| ArtifactArrowError::UnknownRecord(record_name.to_owned()))?
        .iter()
        .map(|value| value.as_str().expect("sort key must be a string"))
        .collect::<Vec<_>>();
    let keys = rows
        .iter()
        .map(|row| {
            sort_keys
                .iter()
                .map(|key| row.get(*key).expect("field set validated"))
                .collect::<Vec<_>>()
        })
        .collect::<Vec<_>>();
    if keys.windows(2).any(|pair| pair[0] >= pair[1]) {
        return Err(ArtifactArrowError::Semantic {
            row: 0,
            field: "canonical ordering".to_owned(),
        });
    }

    for field_name in golden["constraints"][record_name]["unique"]
        .as_array()
        .into_iter()
        .flatten()
    {
        let field_name = field_name.as_str().expect("unique field name");
        let values = rows
            .iter()
            .map(|row| row.get(field_name).expect("field set validated"))
            .collect::<BTreeSet<_>>();
        if values.len() != rows.len() {
            return Err(ArtifactArrowError::Semantic {
                row: 0,
                field: format!("unique {field_name}"),
            });
        }
    }

    if record_name == "ledger_postings" {
        let balance_fields = golden["constraints"][record_name]["balance_by"]
            .as_array()
            .expect("ledger balance fields");
        let mut balances: BTreeMap<Vec<ArtifactScalar>, (i128, i128)> = BTreeMap::new();
        for row in rows {
            let key = balance_fields
                .iter()
                .map(|field| row[field.as_str().expect("balance field name")].clone())
                .collect::<Vec<_>>();
            let ArtifactScalar::Decimal128(raw_units) = row["raw_units"] else {
                unreachable!("schema type checked later")
            };
            let totals = balances.entry(key).or_default();
            match &row["debit_credit"] {
                ArtifactScalar::Utf8(value) if value == "debit" => {
                    totals.0 = totals.0.checked_add(raw_units).ok_or_else(|| {
                        ArtifactArrowError::Semantic {
                            row: 0,
                            field: "ledger balance overflow".to_owned(),
                        }
                    })?;
                }
                ArtifactScalar::Utf8(value) if value == "credit" => {
                    totals.1 = totals.1.checked_add(raw_units).ok_or_else(|| {
                        ArtifactArrowError::Semantic {
                            row: 0,
                            field: "ledger balance overflow".to_owned(),
                        }
                    })?;
                }
                _ => unreachable!("enum checked"),
            }
        }
        if balances.values().any(|(debit, credit)| debit != credit) {
            return Err(ArtifactArrowError::Semantic {
                row: 0,
                field: "ledger balance".to_owned(),
            });
        }
    }
    Ok(())
}

fn artifact_column(
    field: &Field,
    values: &[&ArtifactScalar],
) -> Result<ArrayRef, ArtifactArrowError> {
    macro_rules! mismatch {
        ($row:expr) => {
            ArtifactArrowError::TypeMismatch {
                row: $row,
                field: field.name().clone(),
                expected: field.data_type().clone(),
            }
        };
    }

    match field.data_type() {
        DataType::Utf8 => {
            let data = values
                .iter()
                .enumerate()
                .map(|(index, value)| match value {
                    ArtifactScalar::Utf8(value) => Ok(value.as_str()),
                    _ => Err(mismatch!(index)),
                })
                .collect::<Result<Vec<_>, _>>()?;
            Ok(Arc::new(StringArray::from(data)))
        }
        DataType::Timestamp(TimeUnit::Microsecond, timezone)
            if timezone.as_deref() == Some("UTC") =>
        {
            let data = values
                .iter()
                .enumerate()
                .map(|(index, value)| match value {
                    ArtifactScalar::TimestampUsUtc(value) => Ok(*value),
                    _ => Err(mismatch!(index)),
                })
                .collect::<Result<Vec<_>, _>>()?;
            Ok(Arc::new(
                TimestampMicrosecondArray::from(data).with_timezone("UTC"),
            ))
        }
        DataType::Decimal128(precision, scale) => {
            let data = values
                .iter()
                .enumerate()
                .map(|(index, value)| match value {
                    ArtifactScalar::Decimal128(value) => Ok(*value),
                    _ => Err(mismatch!(index)),
                })
                .collect::<Result<Vec<_>, _>>()?;
            let array = Decimal128Array::from(data)
                .with_precision_and_scale(*precision, *scale)
                .map_err(|error| ArtifactArrowError::Arrow(error.to_string()))?;
            Ok(Arc::new(array))
        }
        DataType::UInt8 => {
            let data = values
                .iter()
                .enumerate()
                .map(|(index, value)| match value {
                    ArtifactScalar::UInt8(value) => Ok(*value),
                    _ => Err(mismatch!(index)),
                })
                .collect::<Result<Vec<_>, _>>()?;
            Ok(Arc::new(UInt8Array::from(data)))
        }
        _ => Err(ArtifactArrowError::TypeMismatch {
            row: 0,
            field: field.name().clone(),
            expected: field.data_type().clone(),
        }),
    }
}
