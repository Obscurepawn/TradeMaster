use std::collections::BTreeMap;

use tm_core::{
    ArtifactArrowError, ArtifactScalar, artifact_record_batch, run_artifact_schemas,
    validate_run_artifact_rows,
};

#[test]
fn rust_materializes_the_shared_schema_as_a_real_arrow_batch() {
    let debit = BTreeMap::from([
        ("run_id".to_owned(), ArtifactScalar::Utf8("r1".to_owned())),
        ("event_seq".to_owned(), ArtifactScalar::Decimal128(1)),
        ("posting_seq".to_owned(), ArtifactScalar::Decimal128(1)),
        (
            "posting_id".to_owned(),
            ArtifactScalar::Utf8("p1".to_owned()),
        ),
        ("event_time".to_owned(), ArtifactScalar::TimestampUsUtc(1)),
        (
            "account".to_owned(),
            ArtifactScalar::Utf8("cash".to_owned()),
        ),
        (
            "debit_credit".to_owned(),
            ArtifactScalar::Utf8("debit".to_owned()),
        ),
        (
            "unit_type".to_owned(),
            ArtifactScalar::Utf8("currency".to_owned()),
        ),
        ("unit_id".to_owned(), ArtifactScalar::Utf8("CNY".to_owned())),
        (
            "raw_units".to_owned(),
            ArtifactScalar::Decimal128(123_000_000),
        ),
        ("scale".to_owned(), ArtifactScalar::UInt8(8)),
        (
            "source_id".to_owned(),
            ArtifactScalar::Utf8("f1".to_owned()),
        ),
    ]);

    let mut credit = debit.clone();
    credit.insert("posting_seq".to_owned(), ArtifactScalar::Decimal128(2));
    credit.insert(
        "posting_id".to_owned(),
        ArtifactScalar::Utf8("p2".to_owned()),
    );
    credit.insert(
        "debit_credit".to_owned(),
        ArtifactScalar::Utf8("credit".to_owned()),
    );
    let batch = artifact_record_batch("ledger_postings", &[debit, credit]).unwrap();
    assert_eq!(batch.num_rows(), 2);
    assert_eq!(batch.num_columns(), 12);
    assert!(
        batch
            .columns()
            .iter()
            .all(|column| column.null_count() == 0)
    );
}

#[test]
fn rust_arrow_mapping_fails_closed_on_missing_or_wrong_fields() {
    let missing = BTreeMap::new();
    assert!(artifact_record_batch("nav", &[missing]).is_err());

    let wrong = BTreeMap::from([
        ("run_id".to_owned(), ArtifactScalar::Utf8("r1".to_owned())),
        ("event_seq".to_owned(), ArtifactScalar::Decimal128(1)),
        ("event_time".to_owned(), ArtifactScalar::TimestampUsUtc(1)),
        (
            "cash".to_owned(),
            ArtifactScalar::Utf8("not-decimal".to_owned()),
        ),
        ("market_value".to_owned(), ArtifactScalar::Decimal128(0)),
        ("net_asset_value".to_owned(), ArtifactScalar::Decimal128(0)),
    ]);
    assert!(artifact_record_batch("nav", &[wrong]).is_err());
}

#[test]
fn rust_arrow_mapping_rejects_values_outside_shared_enums() {
    let row = BTreeMap::from([
        ("run_id".to_owned(), ArtifactScalar::Utf8("r1".to_owned())),
        ("event_seq".to_owned(), ArtifactScalar::Decimal128(1)),
        ("order_seq".to_owned(), ArtifactScalar::Decimal128(1)),
        ("order_id".to_owned(), ArtifactScalar::Utf8("o1".to_owned())),
        (
            "signal_id".to_owned(),
            ArtifactScalar::Utf8("s1".to_owned()),
        ),
        (
            "instrument_id".to_owned(),
            ArtifactScalar::Utf8("000001.SZ".to_owned()),
        ),
        (
            "side".to_owned(),
            ArtifactScalar::Utf8("sideways".to_owned()),
        ),
        (
            "requested_quantity".to_owned(),
            ArtifactScalar::Decimal128(100),
        ),
        ("submitted_at".to_owned(), ArtifactScalar::TimestampUsUtc(1)),
        ("eligible_at".to_owned(), ArtifactScalar::TimestampUsUtc(2)),
    ]);
    assert!(artifact_record_batch("orders", &[row]).is_err());
}

#[test]
fn rust_arrow_mapping_consumes_shared_positive_and_time_constraints() {
    let row = BTreeMap::from([
        ("run_id".to_owned(), ArtifactScalar::Utf8("r1".to_owned())),
        ("event_seq".to_owned(), ArtifactScalar::Decimal128(1)),
        ("signal_seq".to_owned(), ArtifactScalar::Decimal128(1)),
        (
            "signal_id".to_owned(),
            ArtifactScalar::Utf8("s1".to_owned()),
        ),
        (
            "strategy_id".to_owned(),
            ArtifactScalar::Utf8("strategy".to_owned()),
        ),
        (
            "instrument_id".to_owned(),
            ArtifactScalar::Utf8("000001.SZ".to_owned()),
        ),
        ("signal_time".to_owned(), ArtifactScalar::TimestampUsUtc(1)),
        (
            "eligible_execution_time".to_owned(),
            ArtifactScalar::TimestampUsUtc(1),
        ),
        (
            "intent_type".to_owned(),
            ArtifactScalar::Utf8("quantity".to_owned()),
        ),
        ("value".to_owned(), ArtifactScalar::Decimal128(100)),
        ("reason".to_owned(), ArtifactScalar::Utf8("test".to_owned())),
        (
            "snapshot_id".to_owned(),
            ArtifactScalar::Utf8("a".repeat(64)),
        ),
    ]);
    assert!(artifact_record_batch("signals", &[row]).is_err());
}

#[test]
fn rust_validates_completed_run_scope_and_global_ids() {
    let mut empty_run = run_artifact_schemas()
        .keys()
        .map(|name| (name.clone(), vec![]))
        .collect::<BTreeMap<_, _>>();
    assert!(validate_run_artifact_rows(&empty_run).is_ok());
    empty_run.remove("nav");
    assert!(validate_run_artifact_rows(&empty_run).is_err());

    let signal = BTreeMap::from([
        ("run_id".to_owned(), ArtifactScalar::Utf8("r1".to_owned())),
        ("event_seq".to_owned(), ArtifactScalar::Decimal128(1)),
        ("signal_seq".to_owned(), ArtifactScalar::Decimal128(1)),
        (
            "signal_id".to_owned(),
            ArtifactScalar::Utf8("s1".to_owned()),
        ),
        (
            "strategy_id".to_owned(),
            ArtifactScalar::Utf8("strategy".to_owned()),
        ),
        (
            "instrument_id".to_owned(),
            ArtifactScalar::Utf8("000001.SZ".to_owned()),
        ),
        ("signal_time".to_owned(), ArtifactScalar::TimestampUsUtc(1)),
        (
            "eligible_execution_time".to_owned(),
            ArtifactScalar::TimestampUsUtc(2),
        ),
        (
            "intent_type".to_owned(),
            ArtifactScalar::Utf8("quantity".to_owned()),
        ),
        ("value".to_owned(), ArtifactScalar::Decimal128(100)),
        ("reason".to_owned(), ArtifactScalar::Utf8("test".to_owned())),
        (
            "snapshot_id".to_owned(),
            ArtifactScalar::Utf8("a".repeat(64)),
        ),
    ]);
    let mut duplicate = signal.clone();
    duplicate.insert("event_seq".to_owned(), ArtifactScalar::Decimal128(2));
    assert!(artifact_record_batch("signals", &[signal, duplicate]).is_err());
}

#[test]
fn rust_rejects_backward_account_transition_time_within_one_event() {
    let mut artifacts = run_artifact_schemas()
        .keys()
        .map(|name| (name.clone(), vec![]))
        .collect::<BTreeMap<_, _>>();
    artifacts.insert(
        "nav".to_owned(),
        vec![BTreeMap::from([
            ("run_id".to_owned(), ArtifactScalar::Utf8("r1".to_owned())),
            ("event_seq".to_owned(), ArtifactScalar::Decimal128(7)),
            ("event_time".to_owned(), ArtifactScalar::TimestampUsUtc(0)),
            ("cash".to_owned(), ArtifactScalar::Decimal128(0)),
            ("market_value".to_owned(), ArtifactScalar::Decimal128(0)),
            ("net_asset_value".to_owned(), ArtifactScalar::Decimal128(0)),
        ])],
    );
    let state_hash = "d31a9cd04af06584f3a93024241d486db6df930de6818ab51c347f375c6780de";
    artifacts.insert(
        "account_states".to_owned(),
        vec![
            BTreeMap::from([
                ("run_id".to_owned(), ArtifactScalar::Utf8("r1".to_owned())),
                ("event_seq".to_owned(), ArtifactScalar::Decimal128(7)),
                ("transition_seq".to_owned(), ArtifactScalar::Decimal128(1)),
                (
                    "transition_id".to_owned(),
                    ArtifactScalar::Utf8("t1".to_owned()),
                ),
                (
                    "source_id".to_owned(),
                    ArtifactScalar::Utf8("settlement:first".to_owned()),
                ),
                ("event_time".to_owned(), ArtifactScalar::TimestampUsUtc(2)),
                (
                    "before_state_hash".to_owned(),
                    ArtifactScalar::Utf8("a".repeat(64)),
                ),
                (
                    "after_state_hash".to_owned(),
                    ArtifactScalar::Utf8("b".repeat(64)),
                ),
            ]),
            BTreeMap::from([
                ("run_id".to_owned(), ArtifactScalar::Utf8("r1".to_owned())),
                ("event_seq".to_owned(), ArtifactScalar::Decimal128(7)),
                ("transition_seq".to_owned(), ArtifactScalar::Decimal128(2)),
                (
                    "transition_id".to_owned(),
                    ArtifactScalar::Utf8("t2".to_owned()),
                ),
                (
                    "source_id".to_owned(),
                    ArtifactScalar::Utf8("settlement:second".to_owned()),
                ),
                ("event_time".to_owned(), ArtifactScalar::TimestampUsUtc(1)),
                (
                    "before_state_hash".to_owned(),
                    ArtifactScalar::Utf8("b".repeat(64)),
                ),
                (
                    "after_state_hash".to_owned(),
                    ArtifactScalar::Utf8(state_hash.to_owned()),
                ),
            ]),
        ],
    );

    assert!(matches!(
        validate_run_artifact_rows(&artifacts),
        Err(ArtifactArrowError::Semantic { field, .. }) if field == "account state hash chain"
    ));
}
