use serde_json::{Value, json};
use tm_engine::snapshot::{SnapshotData, SnapshotRun, run_snapshot};

const U: i64 = 100_000_000;
const DAYS: [&str; 4] = ["2024-01-05", "2024-01-08", "2024-01-09", "2024-01-10"];

fn scenario() -> (Value, Value) {
    let mut rows = vec![];
    let mut instruments = vec![];
    for (id, price) in [("A", 10), ("B", 20)] {
        instruments.push(
            json!({"instrument_id":id,"asset_class":"stock","venue":"other",
            "currency":"CNY","buy_lot_size":100,"tick_size":U/100,"settlement":"t1"}),
        );
        for (i, day) in DAYS.iter().enumerate() {
            let p = if id == "A" && i > 0 { 9 } else { price };
            rows.push(
                json!({"session":day,"instrument_id":id,"open":p*U,"close":p*U,
                "buy_at_open":"yes","sell_at_open":if id=="A" && i==1 {"no"} else {"yes"},
                "sell_reason":"lower_limit","evidence":"prices-and-status"}),
            );
        }
    }
    (
        json!({"sessions":DAYS,"next_session":"2024-01-11","instruments":instruments,"rows":rows}),
        json!({"initial_cash":20_000*U,"initial_lots":[{"instrument_id":"A","quantity":8000,"sellable_session":DAYS[0]}],
        "targets":[{"batch_id":"rotate","decision_session":DAYS[0],"eligible_session":DAYS[1],"weights":{"B":80_000_000}}],
        "policy":{"commission_ppm":0,"minimum_commission":5*U,"sell_stamp_duty_ppm":0,"sse_transfer_fee_ppm":0,
            "slippage_ppm":0,"buy_cap_ppm":20000,"max_attempts":3},"detail":true}),
    )
}

fn run(data: &Value, req: &Value) -> Value {
    let data: SnapshotData = serde_json::from_value(data.clone()).unwrap();
    let run: SnapshotRun = serde_json::from_value(req.clone()).unwrap();
    serde_json::to_value(run_snapshot(&data, &run).unwrap()).unwrap()
}

fn limit_scenario(side: &str) -> (Value, Value, usize) {
    let (mut data, mut req) = scenario();
    req["policy"]["max_attempts"] = json!(1);
    let row = if side == "buy" {
        req["initial_lots"] = json!([]);
        5 // B at the first eligible open.
    } else {
        req["targets"][0]["weights"] = json!({});
        1 // A at the first eligible open.
    };
    data["rows"][row][format!("{side}_at_open")] = json!("no");
    data["rows"][row][format!("{side}_reason")] = json!("documented_exchange_band");
    data["rows"][row][format!("{side}_blocked_by_price_limit")] = json!(true);
    (data, req, row)
}

#[test]
fn price_limit_enforcement_can_be_disabled_for_each_documented_side() {
    for side in ["buy", "sell"] {
        let (data, mut req, _) = limit_scenario(side);
        let enforced = run(&data, &req);
        assert_eq!(enforced["fill_count"], 0);
        assert_eq!(enforced["orders"][0]["reason"], "documented_exchange_band");

        req["policy"]["enforce_price_limits"] = json!(false);
        let relaxed = run(&data, &req);
        assert_eq!(relaxed["fill_count"], 1);
        assert_eq!(relaxed["fills"][0]["side"], side);
        assert_eq!(relaxed["orders"][0]["status"], "filled");
    }
}

#[test]
fn disabling_price_limits_retains_other_opening_restrictions() {
    let (data, mut req, row) = limit_scenario("buy");
    req["policy"]["enforce_price_limits"] = json!(false);
    for (field, value, expected) in [
        ("suspended", json!(true), "suspended"),
        ("evidence", Value::Null, "missing_open_evidence"),
        ("open", Value::Null, "missing_open"),
        ("open", json!(21 * U), "above_buy_cap"),
        ("corporate_action_gap", json!(true), "corporate_action_gap"),
        (
            "buy_blocked_by_price_limit",
            json!(false),
            "documented_exchange_band",
        ),
    ] {
        let mut restricted = data.clone();
        restricted["rows"][row][field] = value;
        let result = run(&restricted, &req);
        assert_eq!(result["fill_count"], 0, "{field}");
        let reason = if result["status"] == "insufficient_data" {
            &result["first_issue"]["reason"]
        } else {
            &result["orders"][0]["reason"]
        };
        assert_eq!(reason, expected, "{field}");
    }
    let mut unknown = data.clone();
    unknown["rows"][row]["buy_at_open"] = json!("unknown");
    unknown["rows"][row]["buy_blocked_by_price_limit"] = json!(false);
    let result = run(&unknown, &req);
    assert_eq!(result["status"], "insufficient_data");
    assert_eq!(result["fill_count"], 0);
}

#[test]
fn price_limit_markers_require_a_known_blocked_permission() {
    for side in ["buy", "sell"] {
        for permission in ["yes", "unknown"] {
            let (mut data, req, row) = limit_scenario(side);
            data["rows"][row][format!("{side}_at_open")] = json!(permission);
            let data: SnapshotData = serde_json::from_value(data).unwrap();
            let req: SnapshotRun = serde_json::from_value(req).unwrap();
            let error = run_snapshot(&data, &req).unwrap_err();
            assert!(error.to_string().contains("price-limit marker"));
        }
    }
}

#[test]
fn four_session_rotation_reserves_cash_and_charges_only_fills() {
    let (mut data, mut req) = scenario();
    let result = run(&data, &req);
    assert_eq!(result["status"], "completed");
    assert_eq!(
        result["batches"][0],
        json!({"batch_id":"rotate","status":"reached","attempts":3})
    );
    for (i, (cash, equity)) in [
        (20_000, 100_000),
        (20_000, 92_000),
        (91_995, 91_995),
        (19_990, 91_990),
    ]
    .into_iter()
    .enumerate()
    {
        assert_eq!(result["daily"][i]["cash"], cash * U);
        assert_eq!(result["daily"][i]["equity"], equity * U);
    }
    for (i, reserve) in [20_000, 18_400, 18_399].into_iter().enumerate() {
        assert_eq!(result["attempts"][i]["reserve"], reserve * U);
    }
    assert_eq!(result["positions"][2]["quantity"], 3600);
    assert_eq!(result["positions"][2]["sellable_quantity"], 0);
    assert_eq!(result["order_count"], 3);
    assert_eq!(result["rejected_order_count"], 1);
    assert_eq!(result["fill_count"], 2);
    assert_eq!(result["orders"][0]["reason"], "lower_limit");
    assert_eq!(result["orders"][2]["quantity"], 3600);
    assert_eq!(result["orders"][2]["reserved_cash"], 73_445 * U);
    assert_eq!(result["total_costs"], 10 * U);
    req["detail"] = json!(false);
    let summary = run(&data, &req);
    for key in [
        "daily",
        "batches",
        "total_costs",
        "order_count",
        "fill_count",
        "rejected_order_count",
        "turnover_notional",
    ] {
        assert_eq!(summary[key], result[key]);
    }
    assert_eq!(summary["orders"], json!([]));
    req["detail"] = json!(true);
    data["rows"][1]["close"] = json!(7 * U);
    let repriced = run(&data, &req);
    assert_eq!(repriced["orders"][0], result["orders"][0]);
    assert_eq!(repriced["attempts"][0], result["attempts"][0]);
}

#[test]
fn one_sided_status_market_gaps_and_confirmed_suspension_are_distinct() {
    let (mut data, req) = scenario();
    data["rows"][1]["buy_at_open"] = json!("yes");
    data["rows"][1]["sell_at_open"] = json!("unknown");
    data["rows"][1]["sell_reason"] = json!("limit_file_missing");
    let unknown = run(&data, &req);
    assert_eq!(
        unknown["first_issue"],
        json!({"session":DAYS[1],"instrument_id":"A","reason":"limit_file_missing","phase":"open"})
    );
    assert_eq!(unknown["status"], "insufficient_data");
    assert_eq!(unknown["fill_count"], 0);
    assert_eq!(unknown["batches"][0]["status"], "insufficient_data");
    data["rows"][1]["sell_at_open"] = json!("no");
    data["rows"][1]["sell_reason"] = json!("suspended");
    data["rows"][1]["suspended"] = json!(true);
    data["rows"][1]["close"] = json!(7 * U); // A stale or indicative quote during suspension.
    let suspended = run(&data, &req);
    assert_eq!(suspended["daily"][1]["equity"], 100_000 * U);
    assert_eq!(suspended["positions"][1]["carried_mark"], true);
    data["rows"][1]["suspended"] = json!(false);
    data["rows"][1]["close"] = Value::Null;
    assert_eq!(run(&data, &req)["first_issue"]["reason"], "missing_close");
    data["rows"][1]["corporate_action_gap"] = json!(true);
    assert_eq!(
        run(&data, &req)["first_issue"]["reason"],
        "corporate_action_gap"
    );
}

#[test]
fn replacement_all_cash_timeout_and_last_close_have_separate_outcomes() {
    let (data, mut req) = scenario();
    req["targets"].as_array_mut().unwrap().extend([
        json!({"batch_id":"sell","decision_session":DAYS[1],"eligible_session":DAYS[2],"weights":{}}),
        json!({"batch_id":"after","decision_session":DAYS[3],"eligible_session":"2024-01-11","weights":{}}),
    ]);
    let result = run(&data, &req);
    assert_eq!(
        result["batches"],
        json!([
        {"batch_id":"rotate","status":"replaced","attempts":1},
        {"batch_id":"sell","status":"reached","attempts":1},
        {"batch_id":"after","status":"end_of_run","attempts":0}])
    );
    assert_eq!(result["order_count"], 2);
    req["policy"]["max_attempts"] = json!(1);
    let expired = run(&data, &req);
    assert_eq!(expired["batches"][0]["status"], "expired");
    assert_eq!(expired["total_costs"], 5 * U);
    req["initial_lots"][0]["sellable_session"] = json!(DAYS[2]);
    let locked = run(&data, &req);
    assert_eq!(
        locked["attempts"][0]["shortfalls"][0]["reason"],
        "unsettled_shares"
    );
    assert_eq!(locked["order_count"], 1);
}

#[test]
fn fill_posts_all_costs_and_unlocks_on_the_next_snapshot_session() {
    let (mut data, mut req) = scenario();
    req["initial_lots"] = json!([]);
    req["initial_cash"] = json!(30_200 * U);
    req["targets"][0]["weights"] = json!({"B":70_000_000});
    req["targets"].as_array_mut().unwrap().push(json!({"batch_id":"exit","decision_session":DAYS[1],"eligible_session":DAYS[2],"weights":{}}));
    req["policy"]["commission_ppm"] = json!(300);
    req["policy"]["sell_stamp_duty_ppm"] = json!(500);
    req["policy"]["sse_transfer_fee_ppm"] = json!(10);
    req["policy"]["slippage_ppm"] = json!(100);
    data["instruments"][1]["venue"] = json!("sse");
    let r = run(&data, &req);
    assert_eq!(r["fill_count"], 2);
    assert_eq!(r["positions"][0]["sellable_quantity"], 0);
    assert_eq!(r["fills"][0]["quantity"], 1000);
    assert_eq!(r["fills"][0]["commission"], 6 * U);
    assert_eq!(r["fills"][0]["transfer_fee"], U / 5);
    assert_eq!(r["fills"][1]["tax"], 10 * U);
    assert_eq!(r["fills"][1]["slippage"], 2 * U);
    assert_eq!(r["daily"][2]["cash"], 30_173 * U + 6 * U / 10);
    data["rows"][5]["open"] = json!(21 * U);
    let capped = run(&data, &req);
    assert_eq!(capped["orders"][0]["reason"], "above_buy_cap");
    assert_eq!(capped["fill_count"], 0);
}

#[test]
fn proportional_sizing_spends_the_spare_lot_by_instrument_id() {
    let (mut data, mut req) = scenario();
    let mut c = data["instruments"][0].clone();
    c["instrument_id"] = json!("C");
    data["instruments"].as_array_mut().unwrap().push(c);
    for i in 0..4 {
        let mut row = data["rows"][i].clone();
        row["instrument_id"] = json!("C");
        row["close"] = json!(10 * U);
        data["rows"].as_array_mut().unwrap().push(row);
        data["rows"][i]["open"] = json!(20 * U);
        data["rows"][i]["close"] = json!(20 * U);
    }
    req["initial_lots"][0]["instrument_id"] = json!("C");
    req["initial_lots"][0]["quantity"] = json!(4000);
    req["initial_cash"] = json!(10_000 * U);
    req["targets"][0]["weights"] = json!({"B":50_000_000,"A":50_000_000});
    req["policy"]["buy_cap_ppm"] = json!(0);
    req["policy"]["minimum_commission"] = json!(0);
    let first = run(&data, &req);
    assert_eq!(first["orders"][0]["instrument_id"], "C");
    assert_eq!(first["orders"][1]["instrument_id"], "A");
    assert_eq!(first["orders"][1]["quantity"], 300);
    assert_eq!(first["orders"][2]["instrument_id"], "B");
    assert_eq!(first["orders"][2]["quantity"], 200);
    data["instruments"].as_array_mut().unwrap().reverse();
    data["rows"].as_array_mut().unwrap().reverse();
    assert_eq!(first, run(&data, &req));
}

#[test]
fn sell_a_remaining_odd_lot_only_on_full_exit() {
    let (data, mut req) = scenario();
    req["initial_lots"][0]["quantity"] = json!(1050);
    // 1050 held, 1000 whole-lot target. A 50-share adjustment cannot trade.
    req["targets"][0]["weights"] = json!({"A":33_000_000});
    let small = run(&data, &req);
    assert_eq!(small["batches"][0]["status"], "reached");
    assert_eq!(small["order_count"], 0);
    req["targets"][0]["weights"] = json!({});
    let exit = run(&data, &req);
    assert_eq!(exit["fills"][0]["quantity"], 1050);
}

#[test]
fn an_unknown_open_posts_no_partial_portfolio_but_a_missing_close_retains_fills() {
    let (mut data, mut req) = scenario();
    req["initial_lots"] = json!([]);
    req["initial_cash"] = json!(100_000 * U);
    req["targets"][0]["weights"] = json!({"A":30_000_000,"B":30_000_000});
    data["rows"][5]["buy_at_open"] = json!("unknown");
    data["rows"][5]["buy_reason"] = json!("missing_side_evidence");
    let r = run(&data, &req);
    assert_eq!(r["first_issue"]["phase"], "open");
    assert_eq!(r["order_count"], 2);
    assert_eq!(r["fill_count"], 0);
    assert_eq!(r["total_costs"], 0);
    assert_eq!(r["rejected_order_count"], 0);
    assert_eq!(r["orders"][0]["status"], "not_executed");
    assert_eq!(r["orders"][1]["status"], "unknown");
    assert_eq!(r["daily"].as_array().unwrap().len(), 1);
    data["rows"][5]["buy_at_open"] = json!("yes");
    data["rows"][5]["close"] = Value::Null;
    let missing_close = run(&data, &req);
    assert_eq!(missing_close["first_issue"]["phase"], "close");
    assert_eq!(missing_close["fill_count"], 2);
    assert_eq!(missing_close["total_costs"], 10 * U);
    assert_eq!(missing_close["daily"].as_array().unwrap().len(), 1);
    data["rows"][4]["close"] = Value::Null;
    let missing_mark = run(&data, &req);
    assert_eq!(missing_mark["first_issue"]["phase"], "sizing");
    assert_eq!(
        missing_mark["attempts"][0]["shortfalls"][0]["reason"],
        "missing_previous_close"
    );
    assert_eq!(
        missing_mark["attempts"][0]["shortfalls"][0]["remaining_quantity"],
        Value::Null
    );
    assert_eq!(missing_mark["fill_count"], 0);
}

#[test]
fn initial_lots_remain_locked_beyond_the_sample() {
    let (data, mut req) = scenario();
    req["targets"][0]["weights"] = json!({});
    for day in ["2024-01-11", "2024-01-15"] {
        req["initial_lots"][0]["sellable_session"] = json!(day);
        let result = run(&data, &req);
        assert_eq!(result["status"], "completed");
        assert_eq!(result["batches"][0]["status"], "expired");
        assert_eq!(result["fill_count"], 0);
        let positions = result["positions"].as_array().unwrap();
        assert_eq!(positions.len(), 4);
        assert!(
            positions
                .iter()
                .all(|p| p["quantity"] == 8000 && p["sellable_quantity"] == 0)
        );
    }
    // Saturday is within the supplied calendar and is known to be closed.
    req["initial_lots"][0]["sellable_session"] = json!("2024-01-06");
    let data: SnapshotData = serde_json::from_value(data).unwrap();
    let req: SnapshotRun = serde_json::from_value(req).unwrap();
    assert!(run_snapshot(&data, &req).is_err());
}

#[test]
fn decisions_can_wait_for_the_sale_using_the_actual_closing_account() {
    use tm_engine::snapshot::{PreparedMarket, SnapshotSession, TargetBatch};
    let (data, mut req) = scenario();
    req["targets"] = json!([]);
    req["detail"] = json!(false);
    let data: SnapshotData = serde_json::from_value(data).unwrap();
    let run: SnapshotRun = serde_json::from_value(req).unwrap();
    let market = PreparedMarket::new(&data).unwrap();
    let mut session = SnapshotSession::new(&market, &run, true).unwrap();
    let target = |index: usize, weights: Value| -> TargetBatch {
        serde_json::from_value(json!({
            "batch_id": DAYS[index], "decision_session": DAYS[index],
            "eligible_session": DAYS[index + 1], "weights": weights
        }))
        .unwrap()
    };
    let mut held = Vec::new();
    for i in 0..DAYS.len() {
        let close = session.advance().unwrap().unwrap();
        let shares = close
            .positions
            .iter()
            .find(|p| p.instrument_id().0 == "A")
            .map_or(0, |p| p.quantity().units());
        held.push(shares);
        if i == 0 {
            session.submit(target(i, json!({}))).unwrap();
        } else if i == 2 {
            assert_eq!(shares, 0);
            assert_eq!(close.cash.scaled(), 91_995 * i128::from(U));
            let fills = session.new_fills();
            assert_eq!(fills.len(), 1);
            assert_eq!(fills[0].instrument_id, "A");
            session.submit(target(i, json!({"B":80_000_000}))).unwrap();
        } else {
            let fills = session.new_fills();
            assert_eq!(fills.len(), usize::from(i == 3));
        }
    }
    assert_eq!(held, [8000, 8000, 0, 0]);
    assert!(session.advance().unwrap().is_none());
    let result = session.finish();
    assert_eq!(result.fill_count, 2);
    assert!(result.fills.is_empty());
    assert_eq!(result.daily.last().unwrap().equity, 91_990 * i128::from(U));
}

#[test]
fn suspended_marks_and_overlapping_dividends_keep_their_value_through_payment() {
    let (mut data, mut req) = scenario();
    req["targets"] = json!([]);
    for row in data["rows"].as_array_mut().unwrap() {
        if row["instrument_id"] == "A" && row["session"] != DAYS[0] {
            row["suspended"] = json!(true);
        }
    }
    data["cash_dividends"] = json!([
        {"instrument_id":"A", "record_session":DAYS[0], "ex_session":DAYS[1],
         "pay_session":DAYS[3], "amount_per_share": U/4},
        {"instrument_id":"A", "record_session":DAYS[1], "ex_session":DAYS[2],
         "pay_session":DAYS[3], "amount_per_share": 3*U/4}
    ]);
    let result = run(&data, &req);
    let daily = result["daily"].as_array().unwrap();
    assert_eq!(daily[1]["dividend_receivable"], 2000 * U);
    assert_eq!(daily[2]["dividend_receivable"], 8000 * U);
    assert_eq!(daily[2]["cash"], 20_000 * U);
    assert_eq!(daily[3]["cash"], 28_000 * U);
    assert!(daily[3].get("dividend_receivable").is_none());
    assert!(daily.iter().all(|r| r["equity"] == 100_000 * U));
    assert!(
        result["dividends"]
            .as_array()
            .unwrap()
            .iter()
            .all(|d| d["paid"] == true)
    );
}

#[test]
fn native_snapshot_rejects_ambiguous_record_dates_and_duplicate_dividends() {
    let (mut data, req) = scenario();
    let request: SnapshotRun = serde_json::from_value(req).unwrap();
    let action = json!({"instrument_id":"A", "record_session":DAYS[0], "ex_session":DAYS[1],
        "pay_session":DAYS[3], "amount_per_share": U/4});
    data["cash_dividends"] = json!([action, action]);
    let duplicate: SnapshotData = serde_json::from_value(data.clone()).unwrap();
    assert!(
        run_snapshot(&duplicate, &request)
            .unwrap_err()
            .to_string()
            .contains("duplicate cash dividend")
    );
    data["cash_dividends"] = json!([action]);
    data["cash_dividends"][0]["record_session"] = json!(DAYS[1]);
    let same_day: SnapshotData = serde_json::from_value(data).unwrap();
    assert!(
        run_snapshot(&same_day, &request)
            .unwrap_err()
            .to_string()
            .contains("record_session < ex_session")
    );
}

#[test]
fn price_grid_lays_out_closes_and_listing_evidence_instrument_major() {
    let (mut data, _) = scenario();
    // B has no close on the third session, and A has an unknown-only row on the last session.
    data["rows"][6]["close"] = Value::Null;
    data["rows"][3] = json!({"session":DAYS[3],"instrument_id":"A","open":null,"close":null,
        "buy_at_open":"unknown","sell_at_open":"unknown"});
    let data: SnapshotData = serde_json::from_value(data).unwrap();
    let grid = tm_engine::snapshot::PreparedMarket::new(&data)
        .unwrap()
        .price_grid();
    // Instrument-major: A's four sessions, then B's four sessions; 0 marks a missing close.
    assert_eq!(
        grid.closes,
        vec![10 * U, 9 * U, 9 * U, 0, 20 * U, 20 * U, 0, 20 * U]
    );
    assert_eq!(grid.observed, vec![1, 1, 1, 0, 1, 1, 1, 1]);
}
