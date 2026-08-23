//! Event runtime interfaces.

use std::collections::{BTreeMap, BTreeSet};

use thiserror::Error;
use time::OffsetDateTime;
use tm_core::{
    AcceptedOrder, AccountSnapshot, ExecutionBatch, Expiry, Fill, Fixed8, InstrumentSpec,
    LedgerEffect, LedgerPosting, LedgerPostingGroup, MarketEvent, MarketEventKind, MarketSnapshot,
    NavSnapshot, OrderExecution, OrderIntent, OrderTerminal, PositionLot, PositionLotChange,
    PositionSnapshot, PostingAccount, Quantity, Rejection, RejectionCode, SettlementEvent,
    SettlementPolicy, Side, SignalRecord, StrategyView, SubmittedOrder, TradingStatus,
    ValidationView, Venue,
};

#[derive(Debug, Error)]
pub enum DataError {
    #[error("market data unavailable: {0}")]
    Unavailable(String),
}

#[derive(Debug, Error)]
pub enum StrategyError {
    #[error("strategy failed: {0}")]
    Failed(String),
}

#[derive(Debug, Error)]
pub enum LedgerError {
    #[error("ledger invariant violated: {0}")]
    Invariant(String),
}

#[derive(Debug, Error)]
pub enum ValuationError {
    #[error("valuation failed: {0}")]
    Failed(String),
}

#[derive(Debug, Error)]
pub enum ExecutionError {
    #[error("execution failed: {0}")]
    Failed(String),
}

#[derive(Debug, Error)]
pub enum RuntimeError {
    #[error(transparent)]
    Data(#[from] DataError),
    #[error(transparent)]
    Strategy(#[from] StrategyError),
    #[error(transparent)]
    Execution(#[from] ExecutionError),
    #[error(transparent)]
    Ledger(#[from] LedgerError),
    #[error(transparent)]
    Valuation(#[from] ValuationError),
    #[error("runtime invariant violated: {0}")]
    Invariant(String),
}

pub trait EventSource {
    fn next(&mut self) -> Result<Option<MarketEvent>, DataError>;
}

pub trait StrategyAdapter {
    fn on_event(&mut self, view: &StrategyView) -> Result<Vec<OrderIntent>, StrategyError>;

    fn drain_emitted_signals(&mut self) -> Vec<SignalRecord> {
        vec![]
    }

    fn pending_signal_count(&self) -> usize {
        0
    }
}

/// Adapter from vectorized, precomputed quantity signals to the event strategy seam.
#[derive(Clone, Debug)]
pub struct ScheduledSignalStrategy {
    signals: Vec<SignalRecord>,
    instruments: BTreeMap<String, InstrumentSpec>,
    next_index: usize,
    emitted: Vec<SignalRecord>,
}

impl ScheduledSignalStrategy {
    pub fn try_new(signals: Vec<SignalRecord>) -> Result<Self, StrategyError> {
        Self::try_new_inner(signals, vec![], false)
    }

    pub fn try_new_with_instruments(
        signals: Vec<SignalRecord>,
        instruments: Vec<InstrumentSpec>,
    ) -> Result<Self, StrategyError> {
        Self::try_new_inner(signals, instruments, true)
    }

    fn try_new_inner(
        mut signals: Vec<SignalRecord>,
        instruments: Vec<InstrumentSpec>,
        allow_target_weights: bool,
    ) -> Result<Self, StrategyError> {
        const QUANTITY_SCALE: i128 = 100_000_000;
        if signals.iter().any(|signal| {
            signal.value().scaled() < 0
                || match signal.intent_type() {
                    tm_core::IntentType::Quantity => signal.value().scaled() % QUANTITY_SCALE != 0,
                    tm_core::IntentType::TargetWeight => {
                        !allow_target_weights || signal.value().scaled() > QUANTITY_SCALE
                    }
                }
        }) {
            return Err(StrategyError::Failed(
                "scheduled strategy signal value is invalid".to_owned(),
            ));
        }
        let instrument_count = instruments.len();
        let instruments = instruments
            .into_iter()
            .map(|instrument| (instrument.instrument_id().0.clone(), instrument))
            .collect::<BTreeMap<_, _>>();
        if instruments.len() != instrument_count
            || signals.iter().any(|signal| {
                signal.intent_type() == tm_core::IntentType::TargetWeight
                    && !instruments.contains_key(&signal.instrument_id().0)
            })
        {
            return Err(StrategyError::Failed(
                "target-weight signals require unique instrument specs".to_owned(),
            ));
        }
        let unique_signal_ids = signals
            .iter()
            .map(|signal| signal.signal_id())
            .collect::<BTreeSet<_>>();
        if unique_signal_ids.len() != signals.len() {
            return Err(StrategyError::Failed(
                "scheduled signal IDs must be unique".to_owned(),
            ));
        }
        signals.sort_by(|left, right| {
            (
                left.eligible_execution_time(),
                &left.instrument_id().0,
                left.signal_id(),
            )
                .cmp(&(
                    right.eligible_execution_time(),
                    &right.instrument_id().0,
                    right.signal_id(),
                ))
        });
        let mut target_batches: BTreeMap<OffsetDateTime, Vec<&SignalRecord>> = BTreeMap::new();
        for signal in &signals {
            if signal.intent_type() == tm_core::IntentType::TargetWeight {
                target_batches
                    .entry(signal.eligible_execution_time())
                    .or_default()
                    .push(signal);
            }
        }
        for (eligible_time, batch) in target_batches {
            let unique_instruments = batch
                .iter()
                .map(|signal| signal.instrument_id().0.as_str())
                .collect::<BTreeSet<_>>();
            let weight_sum = batch.iter().try_fold(0_i128, |total, signal| {
                total.checked_add(signal.value().scaled())
            });
            if unique_instruments.len() != batch.len()
                || !matches!(weight_sum, Some(0 | QUANTITY_SCALE))
                || signals.iter().any(|signal| {
                    signal.eligible_execution_time() == eligible_time
                        && signal.intent_type() != tm_core::IntentType::TargetWeight
                })
            {
                return Err(StrategyError::Failed(
                    "target-weight batch must be unique, homogeneous, and sum to zero or one"
                        .to_owned(),
                ));
            }
        }
        Ok(Self {
            signals,
            instruments,
            next_index: 0,
            emitted: vec![],
        })
    }

    pub const fn remaining(&self) -> usize {
        self.signals.len() - self.next_index
    }
}

impl StrategyAdapter for ScheduledSignalStrategy {
    fn on_event(&mut self, view: &StrategyView) -> Result<Vec<OrderIntent>, StrategyError> {
        const QUANTITY_SCALE: i128 = 100_000_000;
        if view.event_kind() != MarketEventKind::SessionOpen {
            return Ok(vec![]);
        }
        let event_time = view.market().event_time();
        let mut orders = Vec::new();
        let mut virtual_positions = view
            .portfolio()
            .positions()
            .iter()
            .map(|position| {
                (
                    position.instrument_id().0.clone(),
                    position.quantity().units(),
                )
            })
            .collect::<BTreeMap<_, _>>();
        let nav_scaled = view
            .portfolio()
            .positions()
            .iter()
            .try_fold(view.portfolio().cash().scaled(), |total, position| {
                total.checked_add(position.market_value().scaled())
            });
        while self
            .signals
            .get(self.next_index)
            .is_some_and(|signal| signal.eligible_execution_time() <= event_time)
        {
            let signal = &self.signals[self.next_index];
            if !view.market().bars().iter().any(|bar| {
                bar.instrument_id == *signal.instrument_id() && bar.event_time == event_time
            }) {
                return Err(StrategyError::Failed(format!(
                    "scheduled signal has no exact event bar: {}",
                    signal.instrument_id().0
                )));
            }
            let target = match signal.intent_type() {
                tm_core::IntentType::Quantity => signal.value().scaled() / QUANTITY_SCALE,
                tm_core::IntentType::TargetWeight => {
                    if signal.eligible_execution_time() != event_time {
                        return Err(StrategyError::Failed(
                            "target-weight signal must execute at its exact event".to_owned(),
                        ));
                    }
                    let nav_scaled = nav_scaled.ok_or_else(|| {
                        StrategyError::Failed("target-weight NAV overflow".to_owned())
                    })?;
                    let bar = view
                        .market()
                        .bars()
                        .iter()
                        .find(|bar| bar.instrument_id == *signal.instrument_id())
                        .ok_or_else(|| {
                            StrategyError::Failed("target-weight bar is missing".to_owned())
                        })?;
                    let instrument =
                        self.instruments
                            .get(&signal.instrument_id().0)
                            .ok_or_else(|| {
                                StrategyError::Failed(
                                    "target-weight instrument is missing".to_owned(),
                                )
                            })?;
                    let lot_size = i128::from(instrument.buy_lot_size());
                    let raw_quantity = nav_scaled
                        .checked_mul(signal.value().scaled())
                        .and_then(|value| value.checked_div(QUANTITY_SCALE))
                        .and_then(|value| value.checked_div(bar.open.scaled()))
                        .ok_or_else(|| {
                            StrategyError::Failed(
                                "target-weight quantity calculation failed".to_owned(),
                            )
                        })?;
                    raw_quantity / lot_size * lot_size
                }
            };
            let current = virtual_positions
                .get(&signal.instrument_id().0)
                .copied()
                .unwrap_or(0);
            if target != current {
                let (side, quantity) = if target > current {
                    (Side::Buy, target - current)
                } else {
                    (Side::Sell, current - target)
                };
                let order_id = match signal.intent_type() {
                    tm_core::IntentType::Quantity => format!("order:{}", signal.signal_id()),
                    tm_core::IntentType::TargetWeight => format!(
                        "order:{}:{}:{}",
                        if side == Side::Sell { 0 } else { 1 },
                        signal.instrument_id().0,
                        signal.signal_id()
                    ),
                };
                orders.push(
                    OrderIntent::try_new(
                        order_id,
                        signal.signal_id(),
                        signal.instrument_id().0.clone(),
                        side,
                        quantity,
                        signal.signal_time(),
                        signal.eligible_execution_time(),
                    )
                    .map_err(|error| StrategyError::Failed(error.to_string()))?,
                );
            }
            virtual_positions.insert(signal.instrument_id().0.clone(), target);
            self.emitted.push(signal.clone());
            self.next_index += 1;
        }
        orders.sort_by(|left, right| left.order_id().cmp(right.order_id()));
        Ok(orders)
    }

    fn drain_emitted_signals(&mut self) -> Vec<SignalRecord> {
        std::mem::take(&mut self.emitted)
    }

    fn pending_signal_count(&self) -> usize {
        self.remaining()
    }
}

pub trait OrderValidator {
    fn validate(
        &self,
        order: &SubmittedOrder,
        view: &ValidationView,
    ) -> Result<AcceptedOrder, Rejection>;
}

pub trait ExecutionModel {
    fn execute(
        &mut self,
        event: &MarketEvent,
        orders: &[AcceptedOrder],
    ) -> Result<ExecutionBatch, ExecutionError>;
}

pub trait Ledger {
    fn apply(&mut self, result: &OrderExecution) -> Result<LedgerEffect, LedgerError>;

    fn settle(&mut self, event: &SettlementEvent) -> Result<LedgerEffect, LedgerError>;

    fn snapshot(&self, market: &MarketSnapshot) -> Result<AccountSnapshot, ValuationError>;
}

/// Market-specific calendar seam; implementations own holidays and session boundaries.
pub trait TradingCalendar {
    fn next_session_open(
        &self,
        venue: Venue,
        after: OffsetDateTime,
    ) -> Result<OffsetDateTime, DataError>;

    fn session_key(&self, venue: Venue, at: OffsetDateTime) -> Result<String, DataError>;
}

#[derive(Clone, Debug, PartialEq, Eq)]
pub struct TransactionCostQuote {
    currency: String,
    commission: Fixed8,
    tax: Fixed8,
    transfer_fee: Fixed8,
}

impl TransactionCostQuote {
    pub fn try_new(
        instrument: &InstrumentSpec,
        commission_scaled: i128,
        tax_scaled: i128,
        transfer_fee_scaled: i128,
    ) -> Result<Self, ExecutionError> {
        if commission_scaled < 0 || tax_scaled < 0 || transfer_fee_scaled < 0 {
            return Err(ExecutionError::Failed(
                "transaction costs must be non-negative".to_owned(),
            ));
        }
        Ok(Self {
            currency: instrument.currency().to_owned(),
            commission: Fixed8::from_scaled(commission_scaled)
                .map_err(|error| ExecutionError::Failed(error.to_string()))?,
            tax: Fixed8::from_scaled(tax_scaled)
                .map_err(|error| ExecutionError::Failed(error.to_string()))?,
            transfer_fee: Fixed8::from_scaled(transfer_fee_scaled)
                .map_err(|error| ExecutionError::Failed(error.to_string()))?,
        })
    }

    pub const fn commission(&self) -> Fixed8 {
        self.commission
    }

    pub fn currency(&self) -> &str {
        &self.currency
    }

    pub const fn tax(&self) -> Fixed8 {
        self.tax
    }

    pub const fn transfer_fee(&self) -> Fixed8 {
        self.transfer_fee
    }
}

pub trait FeeSchedule {
    fn quote(
        &self,
        instrument: &InstrumentSpec,
        order: &AcceptedOrder,
        quantity: Quantity,
        price: Fixed8,
    ) -> Result<TransactionCostQuote, ExecutionError>;
}

pub trait FxRateProvider {
    fn rate(
        &self,
        base_currency: &str,
        quote_currency: &str,
        at: OffsetDateTime,
    ) -> Result<Fixed8, DataError>;
}

/// Versioned deterministic A-share fee rules using integer parts-per-million rates.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct CnFeeSchedule {
    commission_ppm: u32,
    minimum_commission: Fixed8,
    sell_stamp_duty_ppm: u32,
    sse_transfer_fee_ppm: u32,
    rounding_unit: Fixed8,
    schedule_id: String,
}

impl CnFeeSchedule {
    #[allow(clippy::too_many_arguments)]
    pub fn try_new(
        commission_ppm: u32,
        minimum_commission_scaled: i128,
        sell_stamp_duty_ppm: u32,
        sse_transfer_fee_ppm: u32,
        rounding_unit_scaled: i128,
        schedule_id: impl Into<String>,
    ) -> Result<Self, ExecutionError> {
        let schedule_id = schedule_id.into();
        if commission_ppm == 0
            || minimum_commission_scaled < 0
            || rounding_unit_scaled <= 0
            || schedule_id.is_empty()
        {
            return Err(ExecutionError::Failed(
                "fee schedule parameters are invalid".to_owned(),
            ));
        }
        Ok(Self {
            commission_ppm,
            minimum_commission: Fixed8::from_scaled(minimum_commission_scaled)
                .map_err(|error| ExecutionError::Failed(error.to_string()))?,
            sell_stamp_duty_ppm,
            sse_transfer_fee_ppm,
            rounding_unit: Fixed8::from_scaled(rounding_unit_scaled)
                .map_err(|error| ExecutionError::Failed(error.to_string()))?,
            schedule_id,
        })
    }

    pub fn schedule_id(&self) -> &str {
        &self.schedule_id
    }

    fn rounded_fee(&self, notional_scaled: i128, rate_ppm: u32) -> Result<i128, ExecutionError> {
        if rate_ppm == 0 {
            return Ok(0);
        }
        let numerator = notional_scaled
            .checked_mul(i128::from(rate_ppm))
            .ok_or_else(|| ExecutionError::Failed("fee multiplication overflow".to_owned()))?;
        let raw = numerator
            .checked_add(999_999)
            .ok_or_else(|| ExecutionError::Failed("fee rounding overflow".to_owned()))?
            / 1_000_000;
        let unit = self.rounding_unit.scaled();
        raw.checked_add(unit - 1)
            .map(|value| value / unit * unit)
            .ok_or_else(|| ExecutionError::Failed("fee rounding overflow".to_owned()))
    }
}

impl FeeSchedule for CnFeeSchedule {
    fn quote(
        &self,
        instrument: &InstrumentSpec,
        order: &AcceptedOrder,
        quantity: Quantity,
        price: Fixed8,
    ) -> Result<TransactionCostQuote, ExecutionError> {
        let notional = price
            .scaled()
            .checked_mul(quantity.units())
            .filter(|value| *value > 0)
            .ok_or_else(|| ExecutionError::Failed("invalid fee notional".to_owned()))?;
        let commission = self
            .rounded_fee(notional, self.commission_ppm)?
            .max(self.minimum_commission.scaled());
        let side = order.submitted_order().intent().side();
        let tax = if side == Side::Sell && instrument.asset_class() == tm_core::AssetClass::Stock {
            self.rounded_fee(notional, self.sell_stamp_duty_ppm)?
        } else {
            0
        };
        let transfer_fee = if instrument.asset_class() == tm_core::AssetClass::Stock
            && instrument.venue() == Venue::Sse
        {
            self.rounded_fee(notional, self.sse_transfer_fee_ppm)?
        } else {
            0
        };
        TransactionCostQuote::try_new(instrument, commission, tax, transfer_fee)
    }
}

/// A-share daily-bar validator with explicit fees and no liquidity-impact model.
#[derive(Clone, Debug)]
pub struct CnAshareOrderValidator<F> {
    fee_schedule: F,
    slippage_ppm: u32,
}

impl<F> CnAshareOrderValidator<F> {
    pub const fn new(fee_schedule: F) -> Self {
        Self {
            fee_schedule,
            slippage_ppm: 0,
        }
    }

    pub fn try_new(fee_schedule: F, slippage_ppm: u32) -> Result<Self, ExecutionError> {
        if slippage_ppm > 1_000_000 {
            return Err(ExecutionError::Failed(
                "slippage rate cannot exceed 100 percent".to_owned(),
            ));
        }
        Ok(Self {
            fee_schedule,
            slippage_ppm,
        })
    }
}

impl<F: FeeSchedule> CnAshareOrderValidator<F> {
    fn reject(
        order: &SubmittedOrder,
        event_time: OffsetDateTime,
        code: RejectionCode,
        message: &'static str,
    ) -> Result<AcceptedOrder, Rejection> {
        Err(
            Rejection::try_new(order, event_time.max(order.submitted_at()), code, message)
                .expect("validator rejection time is never before submission"),
        )
    }
}

impl<F: FeeSchedule> OrderValidator for CnAshareOrderValidator<F> {
    fn validate(
        &self,
        order: &SubmittedOrder,
        view: &ValidationView,
    ) -> Result<AcceptedOrder, Rejection> {
        let event_time = view.market().event_time();
        if view.event_kind() != MarketEventKind::SessionOpen
            || order.submitted_at() > event_time
            || order.intent().eligible_execution_time() > event_time
        {
            return Self::reject(
                order,
                event_time,
                RejectionCode::InvalidTime,
                "invalid_time",
            );
        }
        if view.instrument().instrument_id() != order.intent().instrument_id() {
            return Self::reject(
                order,
                event_time,
                RejectionCode::MissingMarketData,
                "instrument_view_mismatch",
            );
        }
        let Some(bar) = view.market().bars().iter().find(|bar| {
            &bar.instrument_id == order.intent().instrument_id() && bar.event_time == event_time
        }) else {
            return Self::reject(
                order,
                event_time,
                RejectionCode::MissingMarketData,
                "missing_market_data",
            );
        };
        if bar.trading_status == TradingStatus::Suspended {
            return Self::reject(order, event_time, RejectionCode::Suspended, "suspended");
        }
        let quantity = order.intent().quantity().units();
        let lot_size = i128::from(view.instrument().buy_lot_size());
        let position = view
            .portfolio()
            .positions()
            .iter()
            .find(|position| position.instrument_id() == order.intent().instrument_id());
        let invalid_lot = match order.intent().side() {
            Side::Buy => quantity % lot_size != 0,
            Side::Sell => {
                quantity % lot_size != 0
                    && position.is_none_or(|holding| holding.quantity().units() != quantity)
            }
        };
        if invalid_lot {
            return Self::reject(
                order,
                event_time,
                RejectionCode::InvalidQuantity,
                "invalid_quantity",
            );
        }
        match order.intent().side() {
            Side::Buy if bar.open >= bar.up_limit => {
                return Self::reject(order, event_time, RejectionCode::LimitUp, "limit_up");
            }
            Side::Sell if bar.open <= bar.down_limit => {
                return Self::reject(order, event_time, RejectionCode::LimitDown, "limit_down");
            }
            _ => {}
        }
        let accepted = AcceptedOrder::try_new(order.clone(), event_time)
            .expect("validated event time is on or after submission");
        match order.intent().side() {
            Side::Buy => {
                let Some(notional) = bar.open.scaled().checked_mul(quantity) else {
                    return Self::reject(
                        order,
                        event_time,
                        RejectionCode::InvalidQuantity,
                        "invalid_quantity",
                    );
                };
                let Ok(costs) = self.fee_schedule.quote(
                    view.instrument(),
                    &accepted,
                    order.intent().quantity(),
                    bar.open,
                ) else {
                    return Self::reject(
                        order,
                        event_time,
                        RejectionCode::InvalidQuantity,
                        "invalid_quantity",
                    );
                };
                let required = notional
                    .checked_add(costs.commission().scaled())
                    .and_then(|value| value.checked_add(costs.tax().scaled()))
                    .and_then(|value| value.checked_add(costs.transfer_fee().scaled()))
                    .and_then(|value| {
                        ppm_cost(notional, self.slippage_ppm)
                            .and_then(|slippage| value.checked_add(slippage))
                    });
                if required.is_none_or(|value| value > view.portfolio().cash().scaled()) {
                    return Self::reject(
                        order,
                        event_time,
                        RejectionCode::InsufficientCash,
                        "insufficient_cash",
                    );
                }
            }
            Side::Sell => {
                if position.is_none_or(|holding| holding.sellable_quantity().units() < quantity) {
                    return Self::reject(
                        order,
                        event_time,
                        RejectionCode::InsufficientSellable,
                        "insufficient_sellable",
                    );
                }
            }
        }
        Ok(accepted)
    }
}

/// Deterministic full-fill daily-bar execution for small, non-impacting portfolios.
#[derive(Clone, Debug)]
pub struct DailyBarExecutionModel<F> {
    instruments: BTreeMap<String, InstrumentSpec>,
    fee_schedule: F,
    slippage_ppm: u32,
}

impl<F> DailyBarExecutionModel<F> {
    pub fn try_new(
        instruments: Vec<InstrumentSpec>,
        fee_schedule: F,
        slippage_ppm: u32,
    ) -> Result<Self, ExecutionError> {
        if slippage_ppm > 1_000_000 {
            return Err(ExecutionError::Failed(
                "slippage rate cannot exceed 100 percent".to_owned(),
            ));
        }
        let count = instruments.len();
        let instruments = instruments
            .into_iter()
            .map(|instrument| (instrument.instrument_id().0.clone(), instrument))
            .collect::<BTreeMap<_, _>>();
        if instruments.len() != count {
            return Err(ExecutionError::Failed(
                "execution instruments must be unique".to_owned(),
            ));
        }
        Ok(Self {
            instruments,
            fee_schedule,
            slippage_ppm,
        })
    }
}

impl<F: FeeSchedule> ExecutionModel for DailyBarExecutionModel<F> {
    fn execute(
        &mut self,
        event: &MarketEvent,
        orders: &[AcceptedOrder],
    ) -> Result<ExecutionBatch, ExecutionError> {
        if event.kind() != MarketEventKind::SessionOpen {
            return Err(ExecutionError::Failed(
                "daily execution requires a session-open event".to_owned(),
            ));
        }
        let mut executions = Vec::with_capacity(orders.len());
        for order in orders {
            let intent = order.submitted_order().intent();
            let Some(instrument) = self.instruments.get(&intent.instrument_id().0) else {
                return Err(ExecutionError::Failed(format!(
                    "missing instrument spec: {}",
                    intent.instrument_id().0
                )));
            };
            let Some(bar) = event.market().bars().iter().find(|bar| {
                bar.instrument_id == *intent.instrument_id() && bar.event_time == event.event_time()
            }) else {
                let expiry = Expiry::try_new(
                    order.order_id(),
                    event.event_time(),
                    intent.quantity().units(),
                    "missing market bar",
                )
                .map_err(|error| ExecutionError::Failed(error.to_string()))?;
                executions.push(
                    OrderExecution::try_new(order.clone(), vec![], OrderTerminal::Expired(expiry))
                        .map_err(|error| ExecutionError::Failed(error.to_string()))?,
                );
                continue;
            };
            let fees = self
                .fee_schedule
                .quote(instrument, order, intent.quantity(), bar.open)?;
            let notional = bar
                .open
                .scaled()
                .checked_mul(intent.quantity().units())
                .ok_or_else(|| ExecutionError::Failed("notional overflow".to_owned()))?;
            let slippage = ppm_cost(notional, self.slippage_ppm)
                .ok_or_else(|| ExecutionError::Failed("slippage overflow".to_owned()))?;
            let fill = Fill::try_new(
                format!("{}:fill:0", order.order_id()),
                order.order_id(),
                event.event_time(),
                intent.quantity().units(),
                bar.open.scaled(),
                fees.commission().scaled(),
                fees.tax().scaled(),
                fees.transfer_fee().scaled(),
                slippage,
            )
            .map_err(|error| ExecutionError::Failed(error.to_string()))?;
            executions.push(
                OrderExecution::try_new(order.clone(), vec![fill], OrderTerminal::Filled)
                    .map_err(|error| ExecutionError::Failed(error.to_string()))?,
            );
        }
        ExecutionBatch::try_new(orders, executions)
            .map_err(|error| ExecutionError::Failed(error.to_string()))
    }
}

fn ppm_cost(notional_scaled: i128, rate_ppm: u32) -> Option<i128> {
    notional_scaled
        .checked_mul(i128::from(rate_ppm))
        .and_then(|value| value.checked_add(999_999))
        .map(|value| value / 1_000_000)
}

#[derive(Clone, Debug)]
struct LedgerLot {
    lot_id: String,
    instrument_id: String,
    quantity: i128,
    acquired_at: OffsetDateTime,
    sellable_at: OffsetDateTime,
    unit_cost_scaled: i128,
}

/// Exact-cash, FIFO, lot-aware ledger for the daily event runtime.
#[derive(Clone, Debug)]
pub struct LotLedger<C> {
    cash_scaled: i128,
    current_time: OffsetDateTime,
    instruments: BTreeMap<String, InstrumentSpec>,
    calendar: C,
    lots: BTreeMap<String, LedgerLot>,
    last_prices: BTreeMap<String, i128>,
    transition_seq: u64,
}

impl<C: TradingCalendar + Clone> LotLedger<C> {
    pub fn try_new(
        initial_cash_scaled: i128,
        initial_time: OffsetDateTime,
        instruments: Vec<InstrumentSpec>,
        calendar: C,
    ) -> Result<Self, LedgerError> {
        if initial_cash_scaled <= 0 {
            return Err(LedgerError::Invariant(
                "initial cash must be positive".to_owned(),
            ));
        }
        Fixed8::from_scaled(initial_cash_scaled)
            .map_err(|error| LedgerError::Invariant(error.to_string()))?;
        let count = instruments.len();
        let instruments = instruments
            .into_iter()
            .map(|instrument| (instrument.instrument_id().0.clone(), instrument))
            .collect::<BTreeMap<_, _>>();
        if instruments.len() != count {
            return Err(LedgerError::Invariant(
                "ledger instruments must be unique".to_owned(),
            ));
        }
        Ok(Self {
            cash_scaled: initial_cash_scaled,
            current_time: initial_time,
            instruments,
            calendar,
            lots: BTreeMap::new(),
            last_prices: BTreeMap::new(),
            transition_seq: 0,
        })
    }

    fn snapshot_with_marks(
        &self,
        event_time: OffsetDateTime,
        marks: &BTreeMap<String, i128>,
    ) -> Result<AccountSnapshot, LedgerError> {
        let mut quantities: BTreeMap<String, (i128, i128)> = BTreeMap::new();
        let mut lots = Vec::with_capacity(self.lots.len());
        for lot in self.lots.values() {
            let totals = quantities
                .entry(lot.instrument_id.clone())
                .or_insert((0, 0));
            totals.0 = totals
                .0
                .checked_add(lot.quantity)
                .ok_or_else(|| LedgerError::Invariant("position overflow".to_owned()))?;
            if lot.sellable_at <= event_time {
                totals.1 = totals
                    .1
                    .checked_add(lot.quantity)
                    .ok_or_else(|| LedgerError::Invariant("sellable overflow".to_owned()))?;
            }
            lots.push(
                PositionLot::try_new(
                    &lot.lot_id,
                    &lot.instrument_id,
                    lot.quantity,
                    lot.acquired_at,
                    lot.sellable_at,
                    lot.unit_cost_scaled,
                )
                .map_err(|error| LedgerError::Invariant(error.to_string()))?,
            );
        }
        let mut market_value = 0_i128;
        let mut positions = Vec::with_capacity(quantities.len());
        for (instrument_id, (quantity, sellable)) in quantities {
            let mark = marks.get(&instrument_id).copied().ok_or_else(|| {
                LedgerError::Invariant(format!("missing mark price: {instrument_id}"))
            })?;
            let value = mark
                .checked_mul(quantity)
                .ok_or_else(|| LedgerError::Invariant("market value overflow".to_owned()))?;
            market_value = market_value
                .checked_add(value)
                .ok_or_else(|| LedgerError::Invariant("NAV overflow".to_owned()))?;
            positions.push(
                PositionSnapshot::try_new(event_time, instrument_id, quantity, sellable, value)
                    .map_err(|error| LedgerError::Invariant(error.to_string()))?,
            );
        }
        let nav = NavSnapshot::try_new(
            event_time,
            self.cash_scaled,
            market_value,
            self.cash_scaled
                .checked_add(market_value)
                .ok_or_else(|| LedgerError::Invariant("NAV overflow".to_owned()))?,
        )
        .map_err(|error| LedgerError::Invariant(error.to_string()))?;
        AccountSnapshot::try_new(nav, positions, lots)
            .map_err(|error| LedgerError::Invariant(error.to_string()))
    }

    fn internal_snapshot(&self) -> Result<AccountSnapshot, LedgerError> {
        self.snapshot_with_marks(self.current_time, &self.last_prices)
    }

    fn posting(
        fill: &Fill,
        suffix: &str,
        account: PostingAccount,
        debit_credit: tm_core::DebitCredit,
        currency: &str,
        raw_units: i128,
    ) -> Result<LedgerPosting, LedgerError> {
        LedgerPosting::try_new(
            format!("{}:{suffix}", fill.fill_id()),
            fill.event_time(),
            account,
            debit_credit,
            currency,
            raw_units,
            fill.fill_id(),
        )
        .map_err(|error| LedgerError::Invariant(error.to_string()))
    }
}

impl<C: TradingCalendar + Clone> Ledger for LotLedger<C> {
    fn apply(&mut self, result: &OrderExecution) -> Result<LedgerEffect, LedgerError> {
        let before = self.internal_snapshot()?;
        if result.fills().is_empty() {
            return LedgerEffect::try_new(
                format!("ledger:{}", self.transition_seq),
                result.accepted_order().order_id(),
                self.current_time,
                vec![],
                vec![],
                &before,
                &before,
            )
            .map_err(|error| LedgerError::Invariant(error.to_string()));
        }
        let mut next = self.clone();
        let intent = result.accepted_order().submitted_order().intent();
        let instrument = self
            .instruments
            .get(&intent.instrument_id().0)
            .ok_or_else(|| LedgerError::Invariant("missing instrument spec".to_owned()))?;
        let mut groups = Vec::with_capacity(result.fills().len());
        let mut lot_changes = Vec::new();
        for fill in result.fills() {
            if fill.event_time() < next.current_time {
                return Err(LedgerError::Invariant(
                    "fill time moves backwards".to_owned(),
                ));
            }
            let quantity = fill.quantity().units();
            let notional = fill
                .price()
                .scaled()
                .checked_mul(quantity)
                .ok_or_else(|| LedgerError::Invariant("notional overflow".to_owned()))?;
            let costs = fill
                .commission()
                .scaled()
                .checked_add(fill.tax().scaled())
                .and_then(|value| value.checked_add(fill.transfer_fee().scaled()))
                .and_then(|value| value.checked_add(fill.slippage().scaled()))
                .ok_or_else(|| LedgerError::Invariant("cost overflow".to_owned()))?;
            let mut postings = Vec::new();
            match intent.side() {
                Side::Buy => {
                    let cash_out = notional
                        .checked_add(costs)
                        .ok_or_else(|| LedgerError::Invariant("cash overflow".to_owned()))?;
                    if next.cash_scaled < cash_out {
                        return Err(LedgerError::Invariant("insufficient cash".to_owned()));
                    }
                    next.cash_scaled -= cash_out;
                    postings.push(Self::posting(
                        fill,
                        "position",
                        PostingAccount::Position,
                        tm_core::DebitCredit::Debit,
                        instrument.currency(),
                        notional,
                    )?);
                    let sellable_at = match instrument.settlement() {
                        SettlementPolicy::T0 => fill.event_time(),
                        SettlementPolicy::T1 => self
                            .calendar
                            .next_session_open(instrument.venue(), fill.event_time())
                            .map_err(|error| LedgerError::Invariant(error.to_string()))?,
                    };
                    let lot = LedgerLot {
                        lot_id: fill.fill_id().to_owned(),
                        instrument_id: intent.instrument_id().0.clone(),
                        quantity,
                        acquired_at: fill.event_time(),
                        sellable_at,
                        unit_cost_scaled: cash_out / quantity,
                    };
                    if next.lots.insert(lot.lot_id.clone(), lot.clone()).is_some() {
                        return Err(LedgerError::Invariant("duplicate lot id".to_owned()));
                    }
                    lot_changes.push(PositionLotChange::added(
                        PositionLot::try_new(
                            &lot.lot_id,
                            &lot.instrument_id,
                            lot.quantity,
                            lot.acquired_at,
                            lot.sellable_at,
                            lot.unit_cost_scaled,
                        )
                        .map_err(|error| LedgerError::Invariant(error.to_string()))?,
                    ));
                    postings.push(Self::posting(
                        fill,
                        "cash",
                        PostingAccount::Cash,
                        tm_core::DebitCredit::Credit,
                        instrument.currency(),
                        cash_out,
                    )?);
                }
                Side::Sell => {
                    let cash_in = notional
                        .checked_sub(costs)
                        .filter(|value| *value > 0)
                        .ok_or_else(|| {
                            LedgerError::Invariant("sell costs exceed proceeds".to_owned())
                        })?;
                    let mut remaining = quantity;
                    let mut sellable_ids = next
                        .lots
                        .values()
                        .filter(|lot| {
                            lot.instrument_id == intent.instrument_id().0
                                && lot.sellable_at <= fill.event_time()
                        })
                        .map(|lot| (lot.acquired_at, lot.lot_id.clone()))
                        .collect::<Vec<_>>();
                    sellable_ids.sort();
                    for (_, lot_id) in sellable_ids {
                        if remaining == 0 {
                            break;
                        }
                        let available = next.lots[&lot_id].quantity;
                        let reduced = available.min(remaining);
                        remaining -= reduced;
                        lot_changes.push(
                            PositionLotChange::reduced(&lot_id, reduced)
                                .map_err(|error| LedgerError::Invariant(error.to_string()))?,
                        );
                        if reduced == available {
                            next.lots.remove(&lot_id);
                        } else {
                            next.lots.get_mut(&lot_id).expect("lot exists").quantity -= reduced;
                        }
                    }
                    if remaining != 0 {
                        return Err(LedgerError::Invariant(
                            "insufficient sellable lots".to_owned(),
                        ));
                    }
                    next.cash_scaled = next
                        .cash_scaled
                        .checked_add(cash_in)
                        .ok_or_else(|| LedgerError::Invariant("cash overflow".to_owned()))?;
                    postings.push(Self::posting(
                        fill,
                        "cash",
                        PostingAccount::Cash,
                        tm_core::DebitCredit::Debit,
                        instrument.currency(),
                        cash_in,
                    )?);
                    postings.push(Self::posting(
                        fill,
                        "position",
                        PostingAccount::Position,
                        tm_core::DebitCredit::Credit,
                        instrument.currency(),
                        notional,
                    )?);
                }
            }
            for (suffix, account, amount) in [
                (
                    "commission",
                    PostingAccount::CommissionExpense,
                    fill.commission().scaled(),
                ),
                ("tax", PostingAccount::TaxExpense, fill.tax().scaled()),
                (
                    "transfer",
                    PostingAccount::TransferFeeExpense,
                    fill.transfer_fee().scaled(),
                ),
                (
                    "slippage",
                    PostingAccount::SlippageExpense,
                    fill.slippage().scaled(),
                ),
            ] {
                if amount > 0 {
                    postings.push(Self::posting(
                        fill,
                        suffix,
                        account,
                        tm_core::DebitCredit::Debit,
                        instrument.currency(),
                        amount,
                    )?);
                }
            }
            groups.push(
                LedgerPostingGroup::try_new(postings)
                    .map_err(|error| LedgerError::Invariant(error.to_string()))?,
            );
            next.current_time = fill.event_time();
            next.last_prices
                .insert(intent.instrument_id().0.clone(), fill.price().scaled());
        }
        next.transition_seq = next
            .transition_seq
            .checked_add(1)
            .ok_or_else(|| LedgerError::Invariant("transition overflow".to_owned()))?;
        let after = next.internal_snapshot()?;
        let effect = LedgerEffect::try_new(
            format!("ledger:{}", self.transition_seq),
            result.accepted_order().order_id(),
            after.nav().event_time(),
            groups,
            lot_changes,
            &before,
            &after,
        )
        .map_err(|error| LedgerError::Invariant(error.to_string()))?;
        *self = next;
        Ok(effect)
    }

    fn settle(&mut self, event: &SettlementEvent) -> Result<LedgerEffect, LedgerError> {
        if event.event_time < self.current_time {
            return Err(LedgerError::Invariant(
                "settlement time moves backwards".to_owned(),
            ));
        }
        let before = self.internal_snapshot()?;
        let unlocks = self
            .lots
            .values()
            .filter(|lot| {
                lot.sellable_at > self.current_time && lot.sellable_at <= event.event_time
            })
            .map(|lot| {
                PositionLotChange::unlocked(&lot.lot_id, lot.quantity)
                    .map_err(|error| LedgerError::Invariant(error.to_string()))
            })
            .collect::<Result<Vec<_>, _>>()?;
        if unlocks.is_empty() {
            return LedgerEffect::try_new(
                format!("settlement:{}", self.transition_seq),
                format!("settlement:{}", event.event_time.unix_timestamp_nanos()),
                self.current_time,
                vec![],
                vec![],
                &before,
                &before,
            )
            .map_err(|error| LedgerError::Invariant(error.to_string()));
        }
        let mut next = self.clone();
        next.current_time = event.event_time;
        next.transition_seq = next
            .transition_seq
            .checked_add(1)
            .ok_or_else(|| LedgerError::Invariant("transition overflow".to_owned()))?;
        let after = next.internal_snapshot()?;
        let effect = LedgerEffect::try_new(
            format!("settlement:{}", self.transition_seq),
            format!("settlement:{}", event.event_time.unix_timestamp_nanos()),
            event.event_time,
            vec![],
            unlocks,
            &before,
            &after,
        )
        .map_err(|error| LedgerError::Invariant(error.to_string()))?;
        *self = next;
        Ok(effect)
    }

    fn snapshot(&self, market: &MarketSnapshot) -> Result<AccountSnapshot, ValuationError> {
        if market.event_time() < self.current_time {
            return Err(ValuationError::Failed(
                "market snapshot precedes ledger state".to_owned(),
            ));
        }
        let mut marks = self.last_prices.clone();
        for bar in market.bars() {
            marks.insert(bar.instrument_id.0.clone(), bar.close.scaled());
        }
        self.snapshot_with_marks(market.event_time(), &marks)
            .map_err(|error| ValuationError::Failed(error.to_string()))
    }
}

/// Ordered in-memory result of the authoritative runtime chain.
#[derive(Clone, Debug, Default)]
pub struct RunTrace {
    events: Vec<TraceEvent>,
    signals: Vec<SignalRecord>,
    orders: Vec<OrderIntent>,
    accepted_orders: Vec<AcceptedOrder>,
    rejections: Vec<Rejection>,
    executions: Vec<OrderExecution>,
    ledger_effects: Vec<LedgerEffect>,
    account_snapshots: Vec<AccountSnapshot>,
}

impl RunTrace {
    pub fn events(&self) -> &[TraceEvent] {
        &self.events
    }

    pub fn signals(&self) -> &[SignalRecord] {
        &self.signals
    }

    pub fn orders(&self) -> &[OrderIntent] {
        &self.orders
    }

    pub fn accepted_orders(&self) -> &[AcceptedOrder] {
        &self.accepted_orders
    }

    pub fn rejections(&self) -> &[Rejection] {
        &self.rejections
    }

    pub fn executions(&self) -> &[OrderExecution] {
        &self.executions
    }

    pub fn ledger_effects(&self) -> &[LedgerEffect] {
        &self.ledger_effects
    }

    pub fn account_snapshots(&self) -> &[AccountSnapshot] {
        &self.account_snapshots
    }
}

#[derive(Clone, Debug, PartialEq, Eq)]
pub struct TraceEvent {
    event_seq: u64,
    event_time: OffsetDateTime,
    kind: MarketEventKind,
}

impl TraceEvent {
    pub const fn event_seq(&self) -> u64 {
        self.event_seq
    }

    pub const fn event_time(&self) -> OffsetDateTime {
        self.event_time
    }

    pub const fn kind(&self) -> MarketEventKind {
        self.kind
    }
}

/// One deterministic event loop shared by scheduled and online strategies.
pub struct EventLoop<S, A, V, X, L> {
    source: S,
    strategy: A,
    validator: V,
    execution: X,
    ledger: L,
    instruments: BTreeMap<String, InstrumentSpec>,
    pending_orders: Vec<OrderIntent>,
    seen_order_ids: BTreeSet<String>,
    history: Vec<MarketSnapshot>,
}

impl<S, A, V, X, L> EventLoop<S, A, V, X, L> {
    pub fn try_new(
        source: S,
        strategy: A,
        validator: V,
        execution: X,
        ledger: L,
        instruments: Vec<InstrumentSpec>,
    ) -> Result<Self, RuntimeError> {
        let count = instruments.len();
        let instruments = instruments
            .into_iter()
            .map(|instrument| (instrument.instrument_id().0.clone(), instrument))
            .collect::<BTreeMap<_, _>>();
        if instruments.len() != count {
            return Err(RuntimeError::Invariant(
                "runtime instruments must be unique".to_owned(),
            ));
        }
        Ok(Self {
            source,
            strategy,
            validator,
            execution,
            ledger,
            instruments,
            pending_orders: vec![],
            seen_order_ids: BTreeSet::new(),
            history: vec![],
        })
    }
}

impl<S, A, V, X, L> EventLoop<S, A, V, X, L>
where
    S: EventSource,
    A: StrategyAdapter,
    V: OrderValidator,
    X: ExecutionModel,
    L: Ledger,
{
    pub fn run(&mut self) -> Result<RunTrace, RuntimeError> {
        let mut trace = RunTrace::default();
        let mut previous_key: Option<(OffsetDateTime, u8)> = None;
        let mut opened_since_settlement = false;
        while let Some(event) = self.source.next()? {
            let phase = match event.kind() {
                MarketEventKind::Settlement => 0,
                MarketEventKind::SessionOpen => 1,
                MarketEventKind::BarClose => 2,
            };
            let key = (event.event_time(), phase);
            if previous_key.is_some_and(|previous| previous >= key) {
                return Err(RuntimeError::Invariant(
                    "events must be strictly ordered by time and phase".to_owned(),
                ));
            }
            previous_key = Some(key);
            trace.events.push(TraceEvent {
                event_seq: trace
                    .events
                    .len()
                    .try_into()
                    .map_err(|_| RuntimeError::Invariant("event sequence overflow".to_owned()))?,
                event_time: event.event_time(),
                kind: event.kind(),
            });

            if event.kind() == MarketEventKind::Settlement {
                let effect = self.ledger.settle(&SettlementEvent {
                    event_time: event.event_time(),
                })?;
                trace.ledger_effects.push(effect);
                trace
                    .account_snapshots
                    .push(self.ledger.snapshot(event.market())?);
                opened_since_settlement = false;
                continue;
            }
            if event.kind() == MarketEventKind::SessionOpen {
                if opened_since_settlement {
                    return Err(RuntimeError::Invariant(
                        "session open requires a preceding settlement phase".to_owned(),
                    ));
                }
                opened_since_settlement = true;
            }

            let before = self.ledger.snapshot(event.market())?;
            let portfolio = portfolio_view(&before)?;
            let strategy_view = StrategyView::try_new(
                event.kind(),
                event.market().clone(),
                self.history.clone(),
                portfolio.clone(),
            )
            .map_err(|error| RuntimeError::Invariant(error.to_string()))?;
            let generated = self.strategy.on_event(&strategy_view)?;
            for order in &generated {
                if !self.seen_order_ids.insert(order.order_id().to_owned()) {
                    return Err(RuntimeError::Invariant(format!(
                        "duplicate runtime order id: {}",
                        order.order_id()
                    )));
                }
            }
            self.pending_orders.extend(generated);
            trace.signals.extend(self.strategy.drain_emitted_signals());
            self.pending_orders.sort_by(|left, right| {
                (
                    left.eligible_execution_time(),
                    left.order_id(),
                    &left.instrument_id().0,
                )
                    .cmp(&(
                        right.eligible_execution_time(),
                        right.order_id(),
                        &right.instrument_id().0,
                    ))
            });

            if event.kind() == MarketEventKind::SessionOpen {
                let split = self
                    .pending_orders
                    .partition_point(|order| order.eligible_execution_time() <= event.event_time());
                let due = self.pending_orders.drain(..split).collect::<Vec<_>>();
                for intent in due {
                    let submitted = SubmittedOrder::try_new(intent.clone(), event.event_time())
                        .map_err(|error| RuntimeError::Invariant(error.to_string()))?;
                    trace.orders.push(intent);
                    let instrument = self
                        .instruments
                        .get(&submitted.intent().instrument_id().0)
                        .ok_or_else(|| {
                            RuntimeError::Invariant(format!(
                                "unknown runtime instrument: {}",
                                submitted.intent().instrument_id().0
                            ))
                        })?;
                    let current_account = self.ledger.snapshot(event.market())?;
                    let current_portfolio = portfolio_view(&current_account)?;
                    let validation_view = ValidationView::try_new(
                        event.kind(),
                        instrument.clone(),
                        event.market().clone(),
                        current_portfolio,
                    )
                    .map_err(|error| RuntimeError::Invariant(error.to_string()))?;
                    match self.validator.validate(&submitted, &validation_view) {
                        Ok(order) => {
                            let batch = self
                                .execution
                                .execute(&event, std::slice::from_ref(&order))?;
                            let execution =
                                batch.executions().first().cloned().ok_or_else(|| {
                                    RuntimeError::Invariant(
                                        "single-order execution returned no result".to_owned(),
                                    )
                                })?;
                            trace.ledger_effects.push(self.ledger.apply(&execution)?);
                            trace.executions.push(execution);
                            trace.accepted_orders.push(order);
                        }
                        Err(rejection) => trace.rejections.push(rejection),
                    }
                }
            }
            trace
                .account_snapshots
                .push(self.ledger.snapshot(event.market())?);
            self.history.push(event.market().clone());
        }
        if !self.pending_orders.is_empty() || self.strategy.pending_signal_count() != 0 {
            return Err(RuntimeError::Invariant(
                "run ended with unconsumed signals or pending orders".to_owned(),
            ));
        }
        Ok(trace)
    }
}

fn portfolio_view(account: &AccountSnapshot) -> Result<tm_core::PortfolioView, RuntimeError> {
    let positions = account
        .positions()
        .iter()
        .map(|position| {
            tm_core::PortfolioPosition::try_new(
                position.instrument_id().0.clone(),
                position.quantity().units(),
                position.sellable_quantity().units(),
                position.market_value().scaled(),
            )
            .map_err(|error| RuntimeError::Invariant(error.to_string()))
        })
        .collect::<Result<Vec<_>, _>>()?;
    tm_core::PortfolioView::try_new(
        account.nav().event_time(),
        account.state_hash(),
        account.nav().cash().scaled(),
        positions,
    )
    .map_err(|error| RuntimeError::Invariant(error.to_string()))
}
