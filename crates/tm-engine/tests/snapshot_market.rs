//! Characterization of how a daily market snapshot is parsed and prepared: the exact
//! error reported for each malformed input and independence from the input row order.
use serde_json::{Value, json};
use tm_engine::snapshot::{PreparedMarket, SnapshotData, SnapshotRun, run_snapshot};

const U: i64 = 100_000_000;
const DAYS: [&str; 3] = ["2024-01-05", "2024-01-08", "2024-01-09"];

fn market() -> Value {
    let mut rows = vec![];
    for day in DAYS {
        for (id, price) in [("A", 10), ("B", 20)] {
            rows.push(
                json!({"session":day,"instrument_id":id,"open":price*U,"close":price*U,
                "buy_at_open":"yes","sell_at_open":"yes","buy_reason":null,"sell_reason":null,
                "evidence":"fixture","suspended":false,"corporate_action_gap":false}),
            );
        }
    }
    let instruments: Vec<_> = ["A", "B"]
        .iter()
        .map(|id| {
            json!({"instrument_id":id,"asset_class":"stock","venue":"other","currency":"CNY",
                "buy_lot_size":100,"tick_size":U/100,"settlement":"t1"})
        })
        .collect();
    json!({"cash_dividends":[],"instruments":instruments,"next_session":"2024-01-10",
        "rows":rows,"sessions":DAYS})
}

/// Parse and prepare compact JSON text, returning the first error as the caller sees it.
fn prepare(text: &str) -> Result<(), String> {
    let data: SnapshotData =
        serde_json::from_str(text).map_err(|err| format!("invalid market snapshot: {err}"))?;
    PreparedMarket::new(&data)
        .map(|_| ())
        .map_err(|err| err.to_string())
}

fn prepare_value(value: &Value) -> Result<(), String> {
    prepare(&serde_json::to_string(value).unwrap())
}

#[test]
fn malformed_rows_report_the_same_error_text() {
    let base = market();
    let mut cases: Vec<(&str, Value, &str)> = vec![];
    let mut edit = |name, f: &dyn Fn(&mut Value), expected| {
        let mut value = base.clone();
        f(&mut value);
        cases.push((name, value, expected));
    };
    edit(
        "unknown row field",
        &|v| v["rows"][1]["volume"] = json!(1),
        "invalid market snapshot: unknown field `volume`, expected one of `session`, \
         `instrument_id`, `open`, `close`, `buy_at_open`, `sell_at_open`, `buy_reason`, \
         `sell_reason`, `evidence`, `suspended`, `corporate_action_gap`, \
         `buy_blocked_by_price_limit`, `sell_blocked_by_price_limit` at line 1 column 804",
    );
    edit(
        "unknown permission",
        &|v| v["rows"][2]["sell_at_open"] = json!("maybe"),
        "invalid market snapshot: unknown variant `maybe`, expected one of `yes`, `no`, \
         `unknown` at line 1 column 965",
    );
    edit(
        "missing permission",
        &|v| {
            v["rows"][0].as_object_mut().unwrap().remove("buy_at_open");
        },
        "invalid market snapshot: missing field `buy_at_open` at line 1 column 548",
    );
    edit(
        "fractional price",
        &|v| v["rows"][3]["close"] = json!(1.5),
        "invalid market snapshot: invalid type: floating point `1.5`, expected i64 at line 1 \
         column 1075",
    );
    edit(
        "rows not a sequence",
        &|v| v["rows"] = json!({}),
        "invalid market snapshot: invalid type: map, expected a sequence at line 1 column 340",
    );
    edit(
        "row not an object",
        &|v| v["rows"][4] = json!(7),
        "invalid market snapshot: invalid type: integer `7`, expected struct DailyRow at line 1 \
         column 1254",
    );
    edit(
        "unknown top-level field",
        &|v| v["volume"] = json!([]),
        "invalid market snapshot: unknown field `volume`, expected one of `sessions`, \
         `next_session`, `instruments`, `rows`, `cash_dividends` at line 1 column 1770",
    );
    edit(
        "missing sessions",
        &|v| {
            v.as_object_mut().unwrap().remove("sessions");
        },
        "invalid market snapshot: missing field `sessions` at line 1 column 1710",
    );
    edit(
        "duplicate row",
        &|v| v["rows"][3]["instrument_id"] = json!("A"),
        "invalid snapshot run: invalid or duplicate row: 2024-01-08 A",
    );
    edit(
        "row on the next session",
        &|v| v["rows"][5]["session"] = json!("2024-01-10"),
        "invalid snapshot run: row outside sampled sessions",
    );
    edit(
        "row between sessions",
        &|v| v["rows"][0]["session"] = json!("2024-01-06"),
        "invalid snapshot run: row outside sampled sessions",
    );
    edit(
        "unknown instrument",
        &|v| v["rows"][4]["instrument_id"] = json!("Z"),
        "invalid snapshot run: invalid or duplicate row: 2024-01-09 Z",
    );
    edit(
        "zero open",
        &|v| v["rows"][1]["open"] = json!(0),
        "invalid snapshot run: invalid or duplicate row: 2024-01-05 B",
    );
    edit(
        "negative close",
        &|v| v["rows"][2]["close"] = json!(-1),
        "invalid snapshot run: invalid or duplicate row: 2024-01-08 A",
    );
    edit(
        "price-limit marker on an open permission",
        &|v| v["rows"][0]["buy_blocked_by_price_limit"] = json!(true),
        "invalid snapshot run: a price-limit marker requires a known blocked permission",
    );
    edit(
        "earliest row error wins",
        &|v| {
            v["rows"][5]["instrument_id"] = json!("Z");
            v["rows"][3]["open"] = json!(-5);
            v["rows"][4]["sell_blocked_by_price_limit"] = json!(true);
        },
        "invalid snapshot run: invalid or duplicate row: 2024-01-08 B",
    );
    edit(
        "marker is checked before the session",
        &|v| {
            v["rows"][0]["session"] = json!("2023-12-29");
            v["rows"][0]["sell_blocked_by_price_limit"] = json!(true);
        },
        "invalid snapshot run: a price-limit marker requires a known blocked permission",
    );
    edit(
        "session is checked before the instrument",
        &|v| {
            v["rows"][0]["session"] = json!("2023-12-29");
            v["rows"][0]["instrument_id"] = json!("Z");
        },
        "invalid snapshot run: row outside sampled sessions",
    );
    edit(
        "unsorted calendar",
        &|v| v["sessions"] = json!([DAYS[1], DAYS[0], DAYS[2]]),
        "invalid snapshot run: sessions and next_session must increase",
    );
    edit(
        "dividend on an unknown instrument",
        &|v| {
            v["cash_dividends"] = json!([{"instrument_id":"Z","record_session":DAYS[0],
                "ex_session":DAYS[1],"pay_session":DAYS[2],"amount_per_share":U}]);
        },
        "invalid snapshot run: invalid or duplicate cash dividend",
    );
    edit(
        "row errors precede dividend errors",
        &|v| {
            v["cash_dividends"] = json!([{"instrument_id":"Z","record_session":DAYS[0],
                "ex_session":DAYS[1],"pay_session":DAYS[2],"amount_per_share":U}]);
            v["rows"][5]["close"] = json!(0);
        },
        "invalid snapshot run: invalid or duplicate row: 2024-01-09 B",
    );
    for (name, value, expected) in &cases {
        assert_eq!(prepare_value(value).unwrap_err(), *expected, "{name}");
    }
}

#[test]
fn malformed_text_reports_the_same_error_text() {
    let text = serde_json::to_string(&market()).unwrap();
    let rows = text.find("\"rows\"").unwrap();
    for (name, input, expected) in [
        (
            "truncated inside rows",
            text[..rows + 400].to_owned(),
            "invalid market snapshot: EOF while parsing a string at line 1 column 733",
        ),
        (
            "truncated before the last brace",
            text[..text.len() - 1].to_owned(),
            "invalid market snapshot: EOF while parsing an object at line 1 column 1761",
        ),
        (
            "trailing characters",
            format!("{text} x"),
            "invalid market snapshot: trailing characters at line 1 column 1764",
        ),
        (
            "duplicate row key",
            text.replacen("\"close\":", "\"session\":\"2024-01-05\",\"close\":", 1),
            "invalid market snapshot: duplicate field `session` at line 1 column 559",
        ),
        (
            "line breaks move the reported position",
            text.replacen("\"rows\":[", "\"rows\":[\n\n", 1).replacen(
                "\"buy_at_open\":\"yes\"",
                "\"buy_at_open\":\"no \"",
                1,
            ),
            "invalid market snapshot: unknown variant `no `, expected one of `yes`, `no`, \
             `unknown` at line 3 column 20",
        ),
    ] {
        assert_eq!(prepare(&input).unwrap_err(), expected, "{name}");
    }
}

/// A small linear congruential generator keeps the shuffles reproducible without dependencies.
struct Lcg(u64);
impl Lcg {
    fn below(&mut self, n: usize) -> usize {
        self.0 = self
            .0
            .wrapping_mul(6_364_136_223_846_793_005)
            .wrapping_add(1_442_695_040_888_963_407);
        ((self.0 >> 33) % n as u64) as usize
    }
}

fn shuffled(value: &Value, seed: u64) -> Value {
    let mut value = value.clone();
    let rows = value["rows"].as_array_mut().unwrap();
    let mut rng = Lcg(seed);
    for i in (1..rows.len()).rev() {
        rows.swap(i, rng.below(i + 1));
    }
    value
}

fn rotation() -> (Value, SnapshotRun) {
    let mut data = market();
    data["rows"][2]["sell_at_open"] = json!("no");
    data["rows"][2]["sell_reason"] = json!("lower_limit");
    data["rows"][4]["suspended"] = json!(true);
    data["rows"][4]["close"] = Value::Null;
    data["cash_dividends"] = json!([{"instrument_id":"A","record_session":DAYS[0],
        "ex_session":DAYS[1],"pay_session":DAYS[2],"amount_per_share":U/10}]);
    let run = serde_json::from_value(json!({"initial_cash":100_000*U,
        "initial_lots":[{"instrument_id":"A","quantity":8000,"sellable_session":DAYS[0]}],
        "targets":[{"batch_id":"rotate","decision_session":DAYS[0],"eligible_session":DAYS[1],
            "weights":{"A":10_000_000,"B":70_000_000}}],
        "policy":{"commission_ppm":250,"minimum_commission":5*U,"sell_stamp_duty_ppm":1000,
            "sse_transfer_fee_ppm":0,"slippage_ppm":0,"buy_cap_ppm":20000,"max_attempts":3},
        "detail":true}))
    .unwrap();
    (data, run)
}

fn result(data: &Value, run: &SnapshotRun) -> String {
    let data: SnapshotData = serde_json::from_value(data.clone()).unwrap();
    serde_json::to_string(&run_snapshot(&data, run).unwrap()).unwrap()
}

#[test]
fn row_order_does_not_change_results_or_the_price_grid() {
    let (data, run) = rotation();
    let expected = result(&data, &run);
    assert!(expected.contains("\"status\":\"completed\""));
    for evidence in ["\"carried_mark\":true", "\"paid\":true", "\"side\":\"buy\""] {
        assert!(expected.contains(evidence), "{evidence}: {expected}");
    }
    let grid = |value: &Value| {
        let data: SnapshotData = serde_json::from_value(value.clone()).unwrap();
        PreparedMarket::new(&data).unwrap().price_grid()
    };
    let expected_grid = grid(&data);
    for seed in 0..16 {
        let value = shuffled(&data, seed);
        assert_eq!(result(&value, &run), expected, "seed {seed}");
        assert_eq!(grid(&value), expected_grid, "seed {seed}");
    }
}

#[test]
fn shuffled_rows_report_the_earliest_error_in_file_order() {
    let mut data = market();
    data["rows"][1]["close"] = json!(0);
    data["rows"][4]["instrument_id"] = json!("Z");
    for seed in 0..16 {
        let value = shuffled(&data, seed);
        let rows = value["rows"].as_array().unwrap();
        let first = rows
            .iter()
            .find(|row| row["close"] == json!(0) || row["instrument_id"] == json!("Z"))
            .unwrap();
        let expected = format!(
            "invalid snapshot run: invalid or duplicate row: {} {}",
            first["session"].as_str().unwrap(),
            first["instrument_id"].as_str().unwrap()
        );
        assert_eq!(prepare_value(&value).unwrap_err(), expected, "seed {seed}");
    }
}
