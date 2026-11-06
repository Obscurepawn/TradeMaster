use serde_json::{Value, json};
use tm_runner::run_json;

fn request() -> Value {
    json!({
        "schema_id": "trademaster.e2e-runner/v1",
        "run_id": "run-wire",
        "strategy_id": "single-asset",
        "snapshot_id": "a".repeat(64),
        "initial_cash_scaled": "200000000000",
        "initial_time": "2025-01-01T00:00:00Z",
        "instruments": [{
            "instrument_id": "600000.SH", "asset_class": "stock", "venue": "sse",
            "currency": "CNY", "buy_lot_size": 100, "tick_size_scaled": "1000000",
            "settlement": "t1"
        }],
        "fee_schedule": {
            "schedule_id": "cn-a-share-v1", "commission_ppm": 300,
            "minimum_commission_scaled": "500000000", "sell_stamp_duty_ppm": 500,
            "sse_transfer_fee_ppm": 10, "rounding_unit_scaled": "1000000"
        },
        "slippage_ppm": 1000,
        "session_opens": ["2025-01-02T01:30:00Z", "2025-01-03T01:30:00Z"],
        "events": [{
            "event_time": "2025-01-02T01:30:00Z", "kind": "session_open",
            "bars": [{
                "instrument_id": "600000.SH", "open_scaled": "1000000000",
                "high_scaled": "1010000000", "low_scaled": "990000000",
                "close_scaled": "1000000000", "volume_units": "10000",
                "up_limit_scaled": "1100000000", "down_limit_scaled": "900000000",
                "trading_status": "tradable", "status_evidence_id": "status:1"
            }]
        }],
        "signals": [{
            "signal_id": "buy", "instrument_id": "600000.SH",
            "signal_time": "2025-01-01T07:00:00Z",
            "eligible_execution_time": "2025-01-02T01:30:00Z",
            "intent_type": "quantity", "value_scaled": "10000000000", "reason": "buy",
            "snapshot_id": "b".repeat(64)
        }]
    })
}

#[test]
fn strict_wire_runs_the_authoritative_runtime() {
    let encoded = serde_json::to_vec(&request()).unwrap();
    let output = run_json(&encoded).unwrap();
    let result: Value = serde_json::from_slice(&output).unwrap();
    assert_eq!(result["schema_id"], "trademaster.e2e-result/v1");
    assert_eq!(result["execution_count"], 1);
    assert_eq!(result["fills"][0]["quantity"], "100");
    assert_eq!(result["final_positions"][0]["instrument_id"], "600000.SH");
    assert_eq!(result["orders"].as_array().unwrap().len(), 1);
    assert_eq!(result["accepted_orders"].as_array().unwrap().len(), 1);
    assert_eq!(result["executions"].as_array().unwrap().len(), 1);
    assert_eq!(result["ledger_effects"].as_array().unwrap().len(), 1);
    assert_eq!(result["account_snapshots"].as_array().unwrap().len(), 1);
}

#[test]
fn wire_rejects_unknown_fields_noncanonical_integers_and_identity_drift() {
    let mut unknown = request();
    unknown["unexpected"] = json!(true);
    assert!(run_json(&serde_json::to_vec(&unknown).unwrap()).is_err());

    let mut noncanonical = request();
    noncanonical["initial_cash_scaled"] = json!("0200000000000");
    assert!(run_json(&serde_json::to_vec(&noncanonical).unwrap()).is_err());

    let mut drift = request();
    drift["signals"][0]["instrument_id"] = json!("000001.SZ");
    assert!(run_json(&serde_json::to_vec(&drift).unwrap()).is_err());
}

#[test]
fn wire_accepts_target_weight_and_sizes_at_the_authoritative_open_nav() {
    let mut target = request();
    target["initial_cash_scaled"] = json!("205000000000");
    target["signals"][0]["intent_type"] = json!("target_weight");
    target["signals"][0]["value_scaled"] = json!("100000000");

    let output = run_json(&serde_json::to_vec(&target).unwrap()).unwrap();
    let result: Value = serde_json::from_slice(&output).unwrap();

    assert_eq!(result["execution_count"], 1);
    assert_eq!(result["fills"][0]["quantity"], "200");
}

#[test]
fn optional_limit_policy_preserves_default_request_serialization() {
    let original = request();
    let parsed: tm_runner::RunnerRequest = serde_json::from_value(original.clone()).unwrap();
    assert_eq!(serde_json::to_value(parsed).unwrap(), original);
    let mut explicit = original.clone();
    explicit["enforce_price_limits"] = json!(true);
    let parsed: tm_runner::RunnerRequest = serde_json::from_value(explicit).unwrap();
    assert_eq!(serde_json::to_value(parsed).unwrap(), original);
}

#[test]
fn whole_share_t0_zero_fee_run_needs_no_price_limits_when_disabled() {
    let mut input = request();
    input["enforce_price_limits"] = json!(false);
    input["instruments"][0]["venue"] = json!("nasdaq");
    input["instruments"][0]["currency"] = json!("USD");
    input["instruments"][0]["buy_lot_size"] = json!(1);
    input["instruments"][0]["settlement"] = json!("t0");
    input["signals"][0]["value_scaled"] = json!("100000000");
    input["slippage_ppm"] = json!(0);
    for rate in [
        "commission_ppm",
        "sell_stamp_duty_ppm",
        "sse_transfer_fee_ppm",
    ] {
        input["fee_schedule"][rate] = json!(0);
    }
    input["fee_schedule"]["minimum_commission_scaled"] = json!("0");
    let bar = input["events"][0]["bars"][0].as_object_mut().unwrap();
    bar.remove("up_limit_scaled");
    bar.remove("down_limit_scaled");
    let encoded = serde_json::to_vec(&input).unwrap();
    let first = run_json(&encoded).unwrap();
    let result: Value = serde_json::from_slice(&first).unwrap();
    assert_eq!(result["fills"][0]["quantity"], "1");
    assert_eq!(result["fills"][0]["commission_scaled"], "0");
    assert_eq!(result["final_positions"][0]["sellable_quantity"], "1");
    assert_eq!(result["nav"][0]["cash_scaled"], "199000000000");
    assert_eq!(first, run_json(&encoded).unwrap());
}

#[test]
fn price_limit_switch_never_accepts_missing_required_or_malformed_bands() {
    let mut absent = request();
    let bar = absent["events"][0]["bars"][0].as_object_mut().unwrap();
    bar.remove("up_limit_scaled");
    bar.remove("down_limit_scaled");
    let error = run_json(&serde_json::to_vec(&absent).unwrap())
        .unwrap_err()
        .to_string();
    assert!(error.contains("price limits are required"), "{error}");

    for (up, down) in [
        (Some("1100000000"), None),
        (None, Some("900000000")),
        (Some("0"), Some("900000000")),
        (Some("1100000000"), Some("0")),
        (Some("800000000"), Some("900000000")),
    ] {
        let mut input = absent.clone();
        input["enforce_price_limits"] = json!(false);
        let bar = input["events"][0]["bars"][0].as_object_mut().unwrap();
        if let Some(value) = up {
            bar.insert("up_limit_scaled".into(), json!(value));
        }
        if let Some(value) = down {
            bar.insert("down_limit_scaled".into(), json!(value));
        }
        let error = run_json(&serde_json::to_vec(&input).unwrap())
            .unwrap_err()
            .to_string();
        assert!(
            error.contains("price limits must be a positive ordered pair"),
            "{error}"
        );
    }
}
