use time::OffsetDateTime;
use tm_core::{
    AccountSnapshot, AssetClass, DebitCredit, Fixed8, InstrumentSpec, LedgerEffect, LedgerPosting,
    LedgerPostingGroup, MarketEventKind, MarketSnapshot, NavSnapshot, OrderIntent,
    PortfolioPosition, PortfolioView, PositionLot, PositionLotChange, PositionSnapshot,
    PostingAccount, RunArtifactObject, RunManifest, SettlementPolicy, Side, StrategyView, Venue,
    run_artifact_schema_sha256, run_artifact_schemas,
};

fn artifact_objects() -> Vec<RunArtifactObject> {
    run_artifact_schemas()
        .keys()
        .map(|name| {
            RunArtifactObject::try_new(name, format!("{name}.parquet"), "c".repeat(64), 0).unwrap()
        })
        .collect()
}

#[test]
fn fixed8_supports_the_decimal128_38_value_domain() {
    let max = 10_i128.pow(38) - 1;
    assert_eq!(Fixed8::from_scaled(max).unwrap().scaled(), max);
    assert!(Fixed8::from_scaled(max + 1).is_err());
}

#[test]
fn run_manifest_binds_the_exact_artifact_schema() {
    let manifest = RunManifest::try_new(
        "r1",
        OffsetDateTime::UNIX_EPOCH,
        "a".repeat(64),
        "strategy",
        "engine",
        "b".repeat(64),
        artifact_objects(),
    )
    .unwrap();
    assert_eq!(
        manifest.artifact_schema_id(),
        "trademaster.run-artifacts/v1"
    );
    assert_eq!(
        manifest.artifact_schema_sha256(),
        run_artifact_schema_sha256()
    );
}

#[test]
fn order_intent_rejects_non_forward_execution_time() {
    let now = OffsetDateTime::UNIX_EPOCH;
    let result = OrderIntent::try_new("o1", "s1", "000001.SZ", Side::Buy, 100, now, now);
    assert!(result.is_err());
}

#[test]
fn enums_have_stable_snake_case_wire_values() {
    assert_eq!(serde_json::to_string(&Side::Buy).unwrap(), "\"buy\"");
    assert_eq!(serde_json::to_string(&Side::Sell).unwrap(), "\"sell\"");
}

#[test]
fn posting_groups_are_currency_typed_positive_and_balanced() {
    let now = OffsetDateTime::UNIX_EPOCH;
    let debit = LedgerPosting::try_new(
        "p1",
        now,
        PostingAccount::Position,
        DebitCredit::Debit,
        "CNY",
        100_000_000,
        "fill-1",
    )
    .unwrap();
    let credit = LedgerPosting::try_new(
        "p2",
        now,
        PostingAccount::Cash,
        DebitCredit::Credit,
        "CNY",
        100_000_000,
        "fill-1",
    )
    .unwrap();
    assert!(LedgerPostingGroup::try_new(vec![debit, credit]).is_ok());
    assert!(
        LedgerPosting::try_new(
            "bad",
            now,
            PostingAccount::Cash,
            DebitCredit::Debit,
            "CNY",
            -1,
            "fill-1",
        )
        .is_err()
    );
}

#[test]
fn ledger_effect_can_represent_no_op_and_multiple_atomic_groups() {
    let before = AccountSnapshot::try_new(
        NavSnapshot::try_new(OffsetDateTime::UNIX_EPOCH, 100, 0, 100).unwrap(),
        vec![],
        vec![],
    )
    .unwrap();
    let after = AccountSnapshot::try_new(
        NavSnapshot::try_new(OffsetDateTime::UNIX_EPOCH, 98, 0, 98).unwrap(),
        vec![],
        vec![],
    )
    .unwrap();
    let noop = LedgerEffect::try_new(
        "t0",
        "settlement:20250101",
        OffsetDateTime::UNIX_EPOCH,
        vec![],
        vec![],
        &before,
        &before,
    )
    .unwrap();
    assert!(noop.posting_groups().is_empty());
    assert_eq!(noop.before_state_hash(), before.state_hash());

    let now = OffsetDateTime::UNIX_EPOCH;
    let group = |source: &str| {
        LedgerPostingGroup::try_new(vec![
            LedgerPosting::try_new(
                format!("{source}-d"),
                now,
                PostingAccount::CommissionExpense,
                DebitCredit::Debit,
                "CNY",
                1,
                source,
            )
            .unwrap(),
            LedgerPosting::try_new(
                format!("{source}-c"),
                now,
                PostingAccount::Cash,
                DebitCredit::Credit,
                "CNY",
                1,
                source,
            )
            .unwrap(),
        ])
        .unwrap()
    };
    assert!(
        LedgerEffect::try_new(
            "t1",
            "o1",
            now,
            vec![group("fill-1"), group("fill-2")],
            vec![],
            &before,
            &after,
        )
        .is_ok()
    );
    let wrong_after = AccountSnapshot::try_new(
        NavSnapshot::try_new(OffsetDateTime::UNIX_EPOCH, 99, 0, 99).unwrap(),
        vec![],
        vec![],
    )
    .unwrap();
    assert!(
        LedgerEffect::try_new(
            "t1",
            "o1",
            now,
            vec![group("fill-1"), group("fill-2")],
            vec![],
            &before,
            &wrong_after,
        )
        .is_err()
    );
    assert!(PositionLotChange::reduced("lot-1", 0).is_err());
}

#[test]
fn account_snapshot_has_a_canonical_state_hash() {
    let nav =
        NavSnapshot::try_new(OffsetDateTime::UNIX_EPOCH, 100_000_000, 0, 100_000_000).unwrap();
    let snapshot = AccountSnapshot::try_new(nav, vec![], vec![]).unwrap();
    assert_eq!(snapshot.state_hash().len(), 64);
    let zero = AccountSnapshot::try_new(
        NavSnapshot::try_new(OffsetDateTime::UNIX_EPOCH, 0, 0, 0).unwrap(),
        vec![],
        vec![],
    )
    .unwrap();
    assert_eq!(
        zero.state_hash(),
        "d31a9cd04af06584f3a93024241d486db6df930de6818ab51c347f375c6780de"
    );
}

#[test]
fn account_snapshot_derives_sellable_quantity_from_unlocked_lots() {
    let now = OffsetDateTime::UNIX_EPOCH;
    let position = PositionSnapshot::try_new(now, "000001.SZ", 200, 100, 20_000).unwrap();
    let unlocked = PositionLot::try_new("l1", "000001.SZ", 100, now, now, 100).unwrap();
    let locked = PositionLot::try_new(
        "l2",
        "000001.SZ",
        100,
        now,
        now + time::Duration::days(1),
        100,
    )
    .unwrap();
    let nav = NavSnapshot::try_new(now, 0, 20_000, 20_000).unwrap();
    assert!(AccountSnapshot::try_new(nav, vec![position], vec![unlocked, locked]).is_ok());

    let wrong = PositionSnapshot::try_new(now, "000001.SZ", 200, 200, 20_000).unwrap();
    let future = PositionLot::try_new(
        "l2",
        "000001.SZ",
        100,
        now + time::Duration::days(1),
        now + time::Duration::days(1),
        100,
    )
    .unwrap();
    let unlocked = PositionLot::try_new("l1", "000001.SZ", 100, now, now, 100).unwrap();
    let nav = NavSnapshot::try_new(now, 0, 20_000, 20_000).unwrap();
    assert!(AccountSnapshot::try_new(nav, vec![wrong], vec![unlocked, future]).is_err());
}

#[test]
fn strategy_views_are_checked_and_frozen_at_the_event_cutoff() {
    assert!(PortfolioPosition::try_new("000001.SZ", 100, 101, 1_000).is_err());
    let now = OffsetDateTime::UNIX_EPOCH;
    let portfolio = PortfolioView::try_new(now, "b".repeat(64), 100, vec![]).unwrap();
    let market = MarketSnapshot::try_new(now, "a".repeat(64), vec![]).unwrap();
    let future =
        MarketSnapshot::try_new(now + time::Duration::days(1), "a".repeat(64), vec![]).unwrap();
    assert!(
        StrategyView::try_new(MarketEventKind::BarClose, market, vec![future], portfolio).is_err()
    );
}

#[test]
fn instrument_spec_deserialization_cannot_bypass_checked_construction() {
    let spec = InstrumentSpec::try_new(
        "000001.SZ",
        AssetClass::Stock,
        Venue::Szse,
        "CNY",
        100,
        1_000_000,
        SettlementPolicy::T1,
    )
    .unwrap();
    let mut wire = serde_json::to_value(spec).unwrap();
    wire["buy_lot_size"] = serde_json::json!(0);
    assert!(serde_json::from_value::<InstrumentSpec>(wire).is_err());
}
