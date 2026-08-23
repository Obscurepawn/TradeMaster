//! Strict JSON process boundary for the authoritative event runtime.

use serde::{Deserialize, Serialize};
use sha2::{Digest, Sha256};
use thiserror::Error;
use time::format_description::well_known::Rfc3339;
use time::{OffsetDateTime, UtcOffset};
use tm_core::{
    AssetClass, Fixed8, InstrumentId, InstrumentSpec, IntentType, MarketBar, MarketEvent,
    MarketEventKind, MarketSnapshot, Quantity, SettlementPolicy, Side, SignalRecord, TradingStatus,
    Venue,
};
use tm_engine::{
    CnAshareOrderValidator, CnFeeSchedule, DailyBarExecutionModel, DataError, EventLoop,
    EventSource, LotLedger, ScheduledSignalStrategy, TradingCalendar,
};

#[derive(Debug, Error)]
pub enum RunnerError {
    #[error("invalid runner request: {0}")]
    InvalidRequest(String),
    #[error("runner execution failed: {0}")]
    Execution(String),
    #[error("runner serialization failed: {0}")]
    Serialization(String),
}

#[derive(Clone, Debug, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct RunnerRequest {
    pub schema_id: String,
    pub run_id: String,
    pub strategy_id: String,
    pub snapshot_id: String,
    pub initial_cash_scaled: String,
    pub initial_time: String,
    pub instruments: Vec<InstrumentInput>,
    pub fee_schedule: FeeScheduleInput,
    pub slippage_ppm: u32,
    pub session_opens: Vec<String>,
    pub events: Vec<EventInput>,
    pub signals: Vec<SignalInput>,
}

#[derive(Clone, Debug, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct InstrumentInput {
    pub instrument_id: String,
    pub asset_class: String,
    pub venue: String,
    pub currency: String,
    pub buy_lot_size: u64,
    pub tick_size_scaled: String,
    pub settlement: String,
}

#[derive(Clone, Debug, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct FeeScheduleInput {
    pub schedule_id: String,
    pub commission_ppm: u32,
    pub minimum_commission_scaled: String,
    pub sell_stamp_duty_ppm: u32,
    pub sse_transfer_fee_ppm: u32,
    pub rounding_unit_scaled: String,
}

#[derive(Clone, Debug, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct EventInput {
    pub event_time: String,
    pub kind: String,
    pub bars: Vec<BarInput>,
}

#[derive(Clone, Debug, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct BarInput {
    pub instrument_id: String,
    pub open_scaled: String,
    pub high_scaled: String,
    pub low_scaled: String,
    pub close_scaled: String,
    pub volume_units: String,
    pub up_limit_scaled: String,
    pub down_limit_scaled: String,
    pub trading_status: String,
    pub status_evidence_id: String,
}

#[derive(Clone, Debug, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct SignalInput {
    pub signal_id: String,
    pub instrument_id: String,
    pub signal_time: String,
    pub eligible_execution_time: String,
    pub intent_type: String,
    pub value_scaled: String,
    pub reason: String,
    pub snapshot_id: String,
}

#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct RunnerResult {
    pub schema_id: String,
    pub run_id: String,
    pub strategy_id: String,
    pub snapshot_id: String,
    pub request_sha256: String,
    pub event_count: usize,
    pub signal_count: usize,
    pub order_count: usize,
    pub accepted_order_count: usize,
    pub rejection_count: usize,
    pub execution_count: usize,
    pub ledger_effect_count: usize,
    pub signals: Vec<serde_json::Value>,
    pub orders: Vec<serde_json::Value>,
    pub accepted_orders: Vec<serde_json::Value>,
    pub executions: Vec<serde_json::Value>,
    pub ledger_effects: Vec<serde_json::Value>,
    pub account_snapshots: Vec<serde_json::Value>,
    pub nav: Vec<NavOutput>,
    pub fills: Vec<FillOutput>,
    pub rejections: Vec<RejectionOutput>,
    pub final_positions: Vec<PositionOutput>,
}

#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct NavOutput {
    pub event_time: String,
    pub event_kind: String,
    pub cash_scaled: String,
    pub market_value_scaled: String,
    pub net_asset_value_scaled: String,
    pub state_hash: String,
    pub positions: Vec<PositionOutput>,
}

#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct FillOutput {
    pub fill_id: String,
    pub order_id: String,
    pub instrument_id: String,
    pub side: String,
    pub event_time: String,
    pub quantity: String,
    pub price_scaled: String,
    pub commission_scaled: String,
    pub tax_scaled: String,
    pub transfer_fee_scaled: String,
    pub slippage_scaled: String,
}

#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct RejectionOutput {
    pub order_id: String,
    pub event_time: String,
    pub code: String,
    pub message: String,
}

#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct PositionOutput {
    pub instrument_id: String,
    pub quantity: String,
    pub sellable_quantity: String,
    pub market_value_scaled: String,
}

struct Events(std::vec::IntoIter<MarketEvent>);

impl EventSource for Events {
    fn next(&mut self) -> Result<Option<MarketEvent>, DataError> {
        Ok(self.0.next())
    }
}

#[derive(Clone, Debug)]
struct ExplicitCalendar {
    opens: Vec<OffsetDateTime>,
}

impl TradingCalendar for ExplicitCalendar {
    fn next_session_open(
        &self,
        _venue: Venue,
        after: OffsetDateTime,
    ) -> Result<OffsetDateTime, DataError> {
        self.opens
            .iter()
            .copied()
            .find(|candidate| *candidate > after)
            .ok_or_else(|| DataError::Unavailable("next session open is absent".to_owned()))
    }

    fn session_key(&self, _venue: Venue, at: OffsetDateTime) -> Result<String, DataError> {
        Ok(at.unix_timestamp_nanos().to_string())
    }
}

fn invalid(message: impl Into<String>) -> RunnerError {
    RunnerError::InvalidRequest(message.into())
}

fn parse_integer(value: &str, label: &str) -> Result<i128, RunnerError> {
    let parsed = value
        .parse::<i128>()
        .map_err(|_| invalid(format!("{label} is not an integer")))?;
    if parsed.to_string() != value {
        return Err(invalid(format!("{label} is not canonical")));
    }
    Ok(parsed)
}

fn parse_time(value: &str, label: &str) -> Result<OffsetDateTime, RunnerError> {
    if !value.ends_with('Z') {
        return Err(invalid(format!("{label} must use UTC Z notation")));
    }
    let timestamp = OffsetDateTime::parse(value, &Rfc3339)
        .map_err(|_| invalid(format!("{label} is not RFC3339")))?;
    if timestamp.offset() != UtcOffset::UTC {
        return Err(invalid(format!("{label} must be UTC")));
    }
    Ok(timestamp)
}

fn format_time(value: OffsetDateTime) -> Result<String, RunnerError> {
    value
        .format(&Rfc3339)
        .map_err(|error| RunnerError::Serialization(error.to_string()))
}

fn asset_class(value: &str) -> Result<AssetClass, RunnerError> {
    match value {
        "stock" => Ok(AssetClass::Stock),
        "etf" => Ok(AssetClass::Etf),
        "index" => Ok(AssetClass::Index),
        _ => Err(invalid("unknown asset class")),
    }
}

fn venue(value: &str) -> Result<Venue, RunnerError> {
    match value {
        "sse" => Ok(Venue::Sse),
        "szse" => Ok(Venue::Szse),
        "bse" => Ok(Venue::Bse),
        "hkex" => Ok(Venue::Hkex),
        "nyse" => Ok(Venue::Nyse),
        "nasdaq" => Ok(Venue::Nasdaq),
        "other" => Ok(Venue::Other),
        _ => Err(invalid("unknown venue")),
    }
}

fn settlement(value: &str) -> Result<SettlementPolicy, RunnerError> {
    match value {
        "t0" => Ok(SettlementPolicy::T0),
        "t1" => Ok(SettlementPolicy::T1),
        _ => Err(invalid("unknown settlement policy")),
    }
}

fn event_kind(value: &str) -> Result<MarketEventKind, RunnerError> {
    match value {
        "session_open" => Ok(MarketEventKind::SessionOpen),
        "bar_close" => Ok(MarketEventKind::BarClose),
        "settlement" => Ok(MarketEventKind::Settlement),
        _ => Err(invalid("unknown market event kind")),
    }
}

fn event_kind_wire(value: MarketEventKind) -> &'static str {
    match value {
        MarketEventKind::SessionOpen => "session_open",
        MarketEventKind::BarClose => "bar_close",
        MarketEventKind::Settlement => "settlement",
    }
}

fn status(value: &str) -> Result<TradingStatus, RunnerError> {
    match value {
        "tradable" => Ok(TradingStatus::Tradable),
        "suspended" => Ok(TradingStatus::Suspended),
        _ => Err(invalid("unknown trading status")),
    }
}

fn intent_type(value: &str) -> Result<IntentType, RunnerError> {
    match value {
        "quantity" => Ok(IntentType::Quantity),
        "target_weight" => Ok(IntentType::TargetWeight),
        _ => Err(invalid("unknown signal intent type")),
    }
}

fn side_wire(value: Side) -> &'static str {
    match value {
        Side::Buy => "buy",
        Side::Sell => "sell",
    }
}

fn rejection_wire(value: tm_core::RejectionCode) -> &'static str {
    match value {
        tm_core::RejectionCode::InvalidTime => "invalid_time",
        tm_core::RejectionCode::InvalidQuantity => "invalid_quantity",
        tm_core::RejectionCode::Suspended => "suspended",
        tm_core::RejectionCode::LimitUp => "limit_up",
        tm_core::RejectionCode::LimitDown => "limit_down",
        tm_core::RejectionCode::InsufficientCash => "insufficient_cash",
        tm_core::RejectionCode::InsufficientSellable => "insufficient_sellable",
        tm_core::RejectionCode::MissingMarketData => "missing_market_data",
    }
}

fn is_sha256(value: &str) -> bool {
    value.len() == 64
        && value
            .bytes()
            .all(|byte| byte.is_ascii_hexdigit() && !byte.is_ascii_uppercase())
}

pub fn run_json(input: &[u8]) -> Result<Vec<u8>, RunnerError> {
    let request: RunnerRequest =
        serde_json::from_slice(input).map_err(|error| invalid(format!("invalid JSON: {error}")))?;
    if request.schema_id != "trademaster.e2e-runner/v1"
        || request.run_id.is_empty()
        || request.strategy_id.is_empty()
        || !is_sha256(&request.snapshot_id)
    {
        return Err(invalid("runner identity is invalid"));
    }
    let initial_cash = parse_integer(&request.initial_cash_scaled, "initial cash")?;
    if initial_cash <= 0 {
        return Err(invalid("initial cash must be positive"));
    }
    let initial_time = parse_time(&request.initial_time, "initial time")?;

    let instrument_ids = request
        .instruments
        .iter()
        .map(|item| item.instrument_id.as_str())
        .collect::<Vec<_>>();
    if instrument_ids.is_empty() || instrument_ids.windows(2).any(|pair| pair[0] >= pair[1]) {
        return Err(invalid("instruments must be nonempty, unique, and sorted"));
    }
    let instruments = request
        .instruments
        .iter()
        .map(|item| {
            InstrumentSpec::try_new(
                &item.instrument_id,
                asset_class(&item.asset_class)?,
                venue(&item.venue)?,
                &item.currency,
                item.buy_lot_size,
                parse_integer(&item.tick_size_scaled, "tick size")?,
                settlement(&item.settlement)?,
            )
            .map_err(|error| invalid(error.to_string()))
        })
        .collect::<Result<Vec<_>, _>>()?;

    let session_opens = request
        .session_opens
        .iter()
        .map(|value| parse_time(value, "session open"))
        .collect::<Result<Vec<_>, _>>()?;
    if session_opens.is_empty()
        || session_opens.windows(2).any(|pair| pair[0] >= pair[1])
        || session_opens[0] <= initial_time
    {
        return Err(invalid(
            "session opens must be strictly ordered after initial time",
        ));
    }
    let calendar = ExplicitCalendar {
        opens: session_opens.clone(),
    };

    let mut events = Vec::with_capacity(request.events.len());
    for item in &request.events {
        let at = parse_time(&item.event_time, "event time")?;
        let kind = event_kind(&item.kind)?;
        let bar_ids = item
            .bars
            .iter()
            .map(|bar| bar.instrument_id.as_str())
            .collect::<Vec<_>>();
        if bar_ids.windows(2).any(|pair| pair[0] >= pair[1]) {
            return Err(invalid("event bars must be unique and sorted"));
        }
        let bars = item
            .bars
            .iter()
            .map(|bar| {
                if !instrument_ids.contains(&bar.instrument_id.as_str())
                    || bar.status_evidence_id.is_empty()
                {
                    return Err(invalid("bar identity is invalid"));
                }
                let open = parse_integer(&bar.open_scaled, "bar open")?;
                let high = parse_integer(&bar.high_scaled, "bar high")?;
                let low = parse_integer(&bar.low_scaled, "bar low")?;
                let close = parse_integer(&bar.close_scaled, "bar close")?;
                let up_limit = parse_integer(&bar.up_limit_scaled, "up limit")?;
                let down_limit = parse_integer(&bar.down_limit_scaled, "down limit")?;
                if low <= 0
                    || high < low
                    || open < low
                    || open > high
                    || close < low
                    || close > high
                    || down_limit <= 0
                    || up_limit < down_limit
                {
                    return Err(invalid("bar price geometry is invalid"));
                }
                Ok(MarketBar {
                    instrument_id: InstrumentId(bar.instrument_id.clone()),
                    event_time: at,
                    open: Fixed8::from_scaled(open).map_err(|error| invalid(error.to_string()))?,
                    high: Fixed8::from_scaled(high).map_err(|error| invalid(error.to_string()))?,
                    low: Fixed8::from_scaled(low).map_err(|error| invalid(error.to_string()))?,
                    close: Fixed8::from_scaled(close)
                        .map_err(|error| invalid(error.to_string()))?,
                    volume: Quantity::positive(parse_integer(&bar.volume_units, "volume")?)
                        .map_err(|error| invalid(error.to_string()))?,
                    up_limit: Fixed8::from_scaled(up_limit)
                        .map_err(|error| invalid(error.to_string()))?,
                    down_limit: Fixed8::from_scaled(down_limit)
                        .map_err(|error| invalid(error.to_string()))?,
                    trading_status: status(&bar.trading_status)?,
                    status_evidence_id: bar.status_evidence_id.clone(),
                })
            })
            .collect::<Result<Vec<_>, RunnerError>>()?;
        if kind == MarketEventKind::SessionOpen && !session_opens.contains(&at) {
            return Err(invalid("session-open event is absent from calendar"));
        }
        let market = MarketSnapshot::try_new(at, &request.snapshot_id, bars)
            .map_err(|error| invalid(error.to_string()))?;
        events.push(
            MarketEvent::try_new(at, kind, market).map_err(|error| invalid(error.to_string()))?,
        );
    }
    if events.is_empty() {
        return Err(invalid("events cannot be empty"));
    }

    let signals = request
        .signals
        .iter()
        .map(|item| {
            if !instrument_ids.contains(&item.instrument_id.as_str())
                || item.signal_id.is_empty()
                || !is_sha256(&item.snapshot_id)
            {
                return Err(invalid("signal identity or intent is invalid"));
            }
            SignalRecord::try_new(
                &item.signal_id,
                &request.strategy_id,
                &item.instrument_id,
                parse_time(&item.signal_time, "signal time")?,
                parse_time(&item.eligible_execution_time, "eligible execution time")?,
                intent_type(&item.intent_type)?,
                parse_integer(&item.value_scaled, "signal value")?,
                &item.reason,
                &item.snapshot_id,
            )
            .map_err(|error| invalid(error.to_string()))
        })
        .collect::<Result<Vec<_>, _>>()?;

    let fees = CnFeeSchedule::try_new(
        request.fee_schedule.commission_ppm,
        parse_integer(
            &request.fee_schedule.minimum_commission_scaled,
            "minimum commission",
        )?,
        request.fee_schedule.sell_stamp_duty_ppm,
        request.fee_schedule.sse_transfer_fee_ppm,
        parse_integer(&request.fee_schedule.rounding_unit_scaled, "rounding unit")?,
        &request.fee_schedule.schedule_id,
    )
    .map_err(|error| invalid(error.to_string()))?;
    let strategy = ScheduledSignalStrategy::try_new_with_instruments(signals, instruments.clone())
        .map_err(|error| invalid(error.to_string()))?;
    let validator = CnAshareOrderValidator::try_new(fees.clone(), request.slippage_ppm)
        .map_err(|error| invalid(error.to_string()))?;
    let execution =
        DailyBarExecutionModel::try_new(instruments.clone(), fees, request.slippage_ppm)
            .map_err(|error| invalid(error.to_string()))?;
    let ledger = LotLedger::try_new(initial_cash, initial_time, instruments.clone(), calendar)
        .map_err(|error| invalid(error.to_string()))?;
    let mut runtime = EventLoop::try_new(
        Events(events.into_iter()),
        strategy,
        validator,
        execution,
        ledger,
        instruments,
    )
    .map_err(|error| invalid(error.to_string()))?;
    let trace = runtime
        .run()
        .map_err(|error| RunnerError::Execution(error.to_string()))?;

    if trace.events().len() != trace.account_snapshots().len() {
        return Err(RunnerError::Execution(
            "runtime event and account snapshot counts differ".to_owned(),
        ));
    }
    let nav = trace
        .events()
        .iter()
        .zip(trace.account_snapshots())
        .map(|(event, account)| {
            Ok(NavOutput {
                event_time: format_time(event.event_time())?,
                event_kind: event_kind_wire(event.kind()).to_owned(),
                cash_scaled: account.nav().cash().scaled().to_string(),
                market_value_scaled: account.nav().market_value().scaled().to_string(),
                net_asset_value_scaled: account.nav().net_asset_value().scaled().to_string(),
                state_hash: account.state_hash().to_owned(),
                positions: account
                    .positions()
                    .iter()
                    .map(|position| PositionOutput {
                        instrument_id: position.instrument_id().0.clone(),
                        quantity: position.quantity().units().to_string(),
                        sellable_quantity: position.sellable_quantity().units().to_string(),
                        market_value_scaled: position.market_value().scaled().to_string(),
                    })
                    .collect(),
            })
        })
        .collect::<Result<Vec<_>, RunnerError>>()?;
    let fills = trace
        .executions()
        .iter()
        .flat_map(|execution| {
            let intent = execution.accepted_order().submitted_order().intent();
            execution.fills().iter().map(move |fill| (intent, fill))
        })
        .map(|(intent, fill)| {
            Ok(FillOutput {
                fill_id: fill.fill_id().to_owned(),
                order_id: fill.order_id().to_owned(),
                instrument_id: intent.instrument_id().0.clone(),
                side: side_wire(intent.side()).to_owned(),
                event_time: format_time(fill.event_time())?,
                quantity: fill.quantity().units().to_string(),
                price_scaled: fill.price().scaled().to_string(),
                commission_scaled: fill.commission().scaled().to_string(),
                tax_scaled: fill.tax().scaled().to_string(),
                transfer_fee_scaled: fill.transfer_fee().scaled().to_string(),
                slippage_scaled: fill.slippage().scaled().to_string(),
            })
        })
        .collect::<Result<Vec<_>, RunnerError>>()?;
    let rejections = trace
        .rejections()
        .iter()
        .map(|rejection| {
            Ok(RejectionOutput {
                order_id: rejection.order_id().to_owned(),
                event_time: format_time(rejection.event_time())?,
                code: rejection_wire(rejection.code()).to_owned(),
                message: rejection.message().to_owned(),
            })
        })
        .collect::<Result<Vec<_>, RunnerError>>()?;
    let final_account = trace
        .account_snapshots()
        .last()
        .ok_or_else(|| RunnerError::Execution("runtime emitted no account snapshot".to_owned()))?;
    let final_positions = final_account
        .positions()
        .iter()
        .map(|position| PositionOutput {
            instrument_id: position.instrument_id().0.clone(),
            quantity: position.quantity().units().to_string(),
            sellable_quantity: position.sellable_quantity().units().to_string(),
            market_value_scaled: position.market_value().scaled().to_string(),
        })
        .collect();
    macro_rules! serialize_trace {
        ($values:expr) => {
            $values
                .iter()
                .map(|value| {
                    serde_json::to_value(value)
                        .map_err(|error| RunnerError::Serialization(error.to_string()))
                })
                .collect::<Result<Vec<_>, _>>()?
        };
    }
    let result = RunnerResult {
        schema_id: "trademaster.e2e-result/v1".to_owned(),
        run_id: request.run_id,
        strategy_id: request.strategy_id,
        snapshot_id: request.snapshot_id,
        request_sha256: format!("{:x}", Sha256::digest(input)),
        event_count: trace.events().len(),
        signal_count: trace.signals().len(),
        order_count: trace.orders().len(),
        accepted_order_count: trace.accepted_orders().len(),
        rejection_count: trace.rejections().len(),
        execution_count: trace.executions().len(),
        ledger_effect_count: trace.ledger_effects().len(),
        signals: serialize_trace!(trace.signals()),
        orders: serialize_trace!(trace.orders()),
        accepted_orders: serialize_trace!(trace.accepted_orders()),
        executions: serialize_trace!(trace.executions()),
        ledger_effects: serialize_trace!(trace.ledger_effects()),
        account_snapshots: serialize_trace!(trace.account_snapshots()),
        nav,
        fills,
        rejections,
        final_positions,
    };
    serde_json::to_vec(&result).map_err(|error| RunnerError::Serialization(error.to_string()))
}
