use tm_core::{
    AccountSnapshot, InstrumentId, InstrumentSpec, MarketEvent, OrderIntent, RunManifest,
    SignalRecord,
};

#[test]
fn public_domain_contracts_are_constructible() {
    let _ = std::mem::size_of::<InstrumentId>();
    let _ = std::mem::size_of::<InstrumentSpec>();
    let _ = std::mem::size_of::<MarketEvent>();
    let _ = std::mem::size_of::<SignalRecord>();
    let _ = std::mem::size_of::<OrderIntent>();
    let _ = std::mem::size_of::<AccountSnapshot>();
    let _ = std::mem::size_of::<RunManifest>();
}
