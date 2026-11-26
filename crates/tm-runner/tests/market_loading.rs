//! Characterization of saved snapshot requests whose market file is damaged, replaced, or
//! valid: the digest is checked before content errors, and every error keeps its text.
use serde_json::{Value, json};
use sha2::{Digest, Sha256};
use std::path::{Path, PathBuf};
use tm_engine::snapshot::{SnapshotData, SnapshotRun, run_snapshot};
use tm_runner::replay_request;

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

fn run() -> Value {
    json!({"initial_cash":100_000*U,"initial_lots":[],"detail":true,
        "targets":[{"batch_id":"buy","decision_session":DAYS[0],"eligible_session":DAYS[1],
            "weights":{"A":40_000_000,"B":50_000_000}}],
        "policy":{"commission_ppm":250,"minimum_commission":5*U,"sell_stamp_duty_ppm":1000,
            "sse_transfer_fee_ppm":0,"slippage_ppm":0,"buy_cap_ppm":20000,"max_attempts":2}})
}

struct Scratch(PathBuf);
impl Scratch {
    fn new(name: &str) -> Self {
        let dir = std::env::temp_dir().join(format!(
            "tm-runner-market-loading-{}-{name}",
            std::process::id()
        ));
        let _ = std::fs::remove_dir_all(&dir);
        std::fs::create_dir_all(&dir).unwrap();
        Self(dir)
    }
}
impl Drop for Scratch {
    fn drop(&mut self) {
        let _ = std::fs::remove_dir_all(&self.0);
    }
}

fn digest(bytes: &[u8]) -> String {
    format!("{:x}", Sha256::digest(bytes))
}

/// Replay `content` saved as `market.json` against the recorded digest.
fn replay(dir: &Path, content: Option<&[u8]>, recorded: &str) -> Result<Vec<u8>, String> {
    let market = dir.join("market.json");
    if let Some(content) = content {
        std::fs::write(&market, content).unwrap();
    }
    let request = json!({"schema_id":"trademaster.snapshot-run/v1","snapshot_path":"market.json",
        "snapshot_sha256":recorded,"run":run()});
    replay_request(&serde_json::to_vec(&request).unwrap(), dir).map_err(|err| err.to_string())
}

#[test]
fn the_digest_is_checked_before_content_errors() {
    let scratch = Scratch::new("errors");
    let valid = serde_json::to_vec(&market()).unwrap();
    let rows = valid.windows(6).position(|w| w == b"\"rows\"").unwrap();
    let mut invalid_utf8 = valid.clone();
    let evidence = invalid_utf8
        .windows(7)
        .position(|w| w == b"fixture")
        .unwrap();
    invalid_utf8[evidence + 2] = 0xff;
    let mut duplicate = market();
    duplicate["rows"][3]["instrument_id"] = json!("A");
    let duplicate = serde_json::to_vec(&duplicate).unwrap();
    let mut unknown = market();
    unknown["rows"][0]["volume"] = json!(1);
    let unknown = serde_json::to_vec(&unknown).unwrap();
    let mismatch = "invalid runner request: market snapshot SHA-256 mismatch";
    let cases: [(&str, Vec<u8>, &str); 9] = [
        ("valid bytes, wrong digest", valid.clone(), mismatch),
        (
            "truncated",
            valid[..rows + 400].to_vec(),
            "invalid runner request: invalid market snapshot: EOF while parsing a string at \
             line 1 column 733",
        ),
        (
            "trailing characters",
            [valid.as_slice(), b" x"].concat(),
            "invalid runner request: invalid market snapshot: trailing characters at line 1 \
             column 1764",
        ),
        (
            "invalid UTF-8",
            invalid_utf8,
            "invalid runner request: invalid market snapshot: invalid unicode code point at \
             line 1 column 448",
        ),
        (
            "unknown field",
            unknown,
            "invalid runner request: invalid market snapshot: unknown field `volume`, expected \
             one of `session`, `instrument_id`, `open`, `close`, `buy_at_open`, \
             `sell_at_open`, `buy_reason`, `sell_reason`, `evidence`, `suspended`, \
             `corporate_action_gap`, `buy_blocked_by_price_limit`, \
             `sell_blocked_by_price_limit` at line 1 column 576",
        ),
        (
            "duplicate row",
            duplicate,
            "runner execution failed: invalid snapshot run: invalid or duplicate row: \
             2024-01-08 A",
        ),
        (
            "empty file",
            vec![],
            "invalid runner request: invalid market snapshot: EOF while parsing a value at line \
             1 column 0",
        ),
        (
            "not JSON",
            b"\xef\xbb\xbf{}".to_vec(),
            "invalid runner request: invalid market snapshot: expected value at line 1 column 1",
        ),
        (
            "missing fields",
            b"{}".to_vec(),
            "invalid runner request: invalid market snapshot: missing field `sessions` at line 1 \
             column 2",
        ),
    ];
    for (name, content, expected) in &cases {
        let recorded = if *expected == mismatch {
            digest(b"another market")
        } else {
            digest(content)
        };
        assert_eq!(
            replay(&scratch.0, Some(content), &recorded).unwrap_err(),
            *expected,
            "{name}"
        );
        // Every damaged file is rejected as replaced when its digest no longer matches.
        if content == &valid {
            continue;
        }
        assert_eq!(
            replay(&scratch.0, Some(content), &digest(&valid)).unwrap_err(),
            mismatch,
            "{name} under the valid digest"
        );
    }
}

#[test]
fn unreadable_market_files_report_the_operating_system_error() {
    let scratch = Scratch::new("unreadable");
    let recorded = digest(b"");
    assert_eq!(
        replay(&scratch.0, None, &recorded).unwrap_err(),
        "invalid runner request: cannot read market snapshot: No such file or directory (os \
         error 2)"
    );
    std::fs::create_dir(scratch.0.join("market.json")).unwrap();
    assert_eq!(
        replay(&scratch.0, None, &recorded).unwrap_err(),
        "invalid runner request: cannot read market snapshot: Is a directory (os error 21)"
    );
}

#[test]
fn file_replay_matches_an_in_memory_run() {
    let scratch = Scratch::new("valid");
    let mut value = market();
    value["rows"][2]["sell_at_open"] = json!("no");
    value["rows"][2]["sell_reason"] = json!("lower_limit");
    value["rows"][5]["close"] = Value::Null;
    value["rows"][5]["suspended"] = json!(true);
    // Pretty-printed, non-canonical whitespace parses to the same market.
    let pretty = serde_json::to_vec_pretty(&value).unwrap();
    for content in [serde_json::to_vec(&value).unwrap(), pretty] {
        let replayed = replay(&scratch.0, Some(&content), &digest(&content)).unwrap();
        let data: SnapshotData = serde_json::from_slice(&content).unwrap();
        let run: SnapshotRun = serde_json::from_value(run()).unwrap();
        let in_memory = serde_json::to_vec(&run_snapshot(&data, &run).unwrap()).unwrap();
        assert_eq!(replayed, in_memory);
        let text = String::from_utf8(replayed).unwrap();
        assert!(text.contains("\"carried_mark\":true"), "{text}");
        assert!(!text.contains("\"fill_count\":0"), "{text}");
    }
}
