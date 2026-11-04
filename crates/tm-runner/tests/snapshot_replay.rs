use std::path::Path;
use tm_runner::replay_request;

#[test]
fn saved_event_request_uses_event_accounting() {
    let result = replay_request(
        include_bytes!("../../../docs/examples/round-trip.json"),
        Path::new("."),
    )
    .unwrap();
    let result: serde_json::Value = serde_json::from_slice(&result).unwrap();
    assert_eq!(
        result["nav"].as_array().unwrap().last().unwrap()["net_asset_value_scaled"],
        "208732000000"
    );
}

#[test]
fn a_changed_market_file_is_rejected_before_reading_prices() {
    let request = serde_json::json!({
        "schema_id":"trademaster.snapshot-run/v1",
        "snapshot_path":concat!(env!("CARGO_MANIFEST_DIR"), "/../../docs/examples/round-trip.json"),
        "snapshot_sha256":"0".repeat(64),
        "run":{
            "initial_cash":1000000000, "targets":[], "detail":true,
            "policy":{"commission_ppm":0,"minimum_commission":0,"sell_stamp_duty_ppm":0,
                "sse_transfer_fee_ppm":0,"slippage_ppm":0,"buy_cap_ppm":0,"max_attempts":1}
        }
    });
    let error = replay_request(&serde_json::to_vec(&request).unwrap(), Path::new(".")).unwrap_err();
    assert!(error.to_string().contains("SHA-256 mismatch"));
}
