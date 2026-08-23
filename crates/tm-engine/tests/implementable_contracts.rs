use tm_core::{
    AcceptedOrder, AccountSnapshot, ExecutionBatch, LedgerEffect, MarketEvent, MarketSnapshot,
    OrderExecution, Rejection, SettlementEvent, SubmittedOrder, ValidationView,
};
use tm_engine::{
    ExecutionError, ExecutionModel, Ledger, LedgerError, OrderValidator, ValuationError,
};

struct ReadableValidator;

impl OrderValidator for ReadableValidator {
    fn validate(
        &self,
        order: &SubmittedOrder,
        view: &ValidationView,
    ) -> Result<AcceptedOrder, Rejection> {
        let _execution_inputs = (
            order.intent().instrument_id(),
            order.intent().side(),
            order.intent().quantity(),
            order.intent().signal_time(),
            order.intent().eligible_execution_time(),
        );
        let _rule_inputs = (
            view.instrument().buy_lot_size(),
            view.market().bars(),
            view.portfolio().cash(),
            view.portfolio()
                .positions()
                .iter()
                .map(|position| {
                    (
                        position.instrument_id(),
                        position.quantity(),
                        position.sellable_quantity(),
                        position.market_value(),
                    )
                })
                .collect::<Vec<_>>(),
        );
        unimplemented!()
    }
}

struct ReadableExecution;

impl ExecutionModel for ReadableExecution {
    fn execute(
        &mut self,
        _event: &MarketEvent,
        orders: &[AcceptedOrder],
    ) -> Result<ExecutionBatch, ExecutionError> {
        for order in orders {
            let _ = (
                order.submitted_order().intent().side(),
                order.submitted_order().intent().instrument_id(),
            );
        }
        let _phase = _event.kind();
        unimplemented!()
    }
}

struct AuditableLedger;

impl Ledger for AuditableLedger {
    fn apply(&mut self, result: &OrderExecution) -> Result<LedgerEffect, LedgerError> {
        let _authoritative_order = (
            result
                .accepted_order()
                .submitted_order()
                .intent()
                .instrument_id(),
            result.accepted_order().submitted_order().intent().side(),
        );
        for fill in result.fills() {
            let _money = (
                fill.price(),
                fill.quantity(),
                fill.commission(),
                fill.tax(),
                fill.transfer_fee(),
                fill.slippage(),
            );
        }
        unimplemented!()
    }

    fn settle(&mut self, _event: &SettlementEvent) -> Result<LedgerEffect, LedgerError> {
        unimplemented!()
    }

    fn snapshot(&self, _market: &MarketSnapshot) -> Result<AccountSnapshot, ValuationError> {
        unimplemented!()
    }
}

#[test]
fn external_crate_can_implement_runtime_contracts_without_side_channels() {
    fn strategy_can_observe_phase(view: &tm_core::StrategyView) {
        let _ = view.event_kind();
    }
    let _ = ReadableValidator;
    let _ = ReadableExecution;
    let _ = AuditableLedger;
    let _ = strategy_can_observe_phase;
}
