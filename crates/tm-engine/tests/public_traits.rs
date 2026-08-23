use tm_engine::{
    EventSource, ExecutionModel, FeeSchedule, FxRateProvider, Ledger, OrderValidator,
    StrategyAdapter, TradingCalendar,
};

#[test]
fn public_runtime_traits_are_object_safe() {
    fn event_source(_: &dyn EventSource) {}
    fn strategy(_: &mut dyn StrategyAdapter) {}
    fn validator(_: &dyn OrderValidator) {}
    fn execution(_: &mut dyn ExecutionModel) {}
    fn ledger(_: &mut dyn Ledger) {}
    fn calendar(_: &dyn TradingCalendar) {}
    fn fees(_: &dyn FeeSchedule) {}
    fn fx(_: &dyn FxRateProvider) {}

    let _ = (
        event_source,
        strategy,
        validator,
        execution,
        ledger,
        calendar,
        fees,
        fx,
    );
}
