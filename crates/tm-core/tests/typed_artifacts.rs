use time::OffsetDateTime;
use tm_core::{
    AcceptedOrder, AccountSnapshot, ArtifactSequence, DebitCredit, Expiry, Fill, IntentType,
    LedgerEffect, LedgerPosting, NavSnapshot, OrderIntent, PositionLot, PositionSnapshot,
    PostingAccount, Rejection, RejectionCode, Side, SignalRecord, SubmittedOrder,
    typed_artifact_record_batch,
};

#[test]
fn every_typed_domain_record_materializes_its_frozen_artifact() {
    let now = OffsetDateTime::UNIX_EPOCH;
    let next = now + time::Duration::DAY;
    let seq = ArtifactSequence::try_new("r1", 1, 1).unwrap();
    let child = ArtifactSequence::try_new_child("r1", 1, 1, 1).unwrap();
    let signal = SignalRecord::try_new(
        "s1",
        "strategy",
        "000001.SZ",
        now,
        next,
        IntentType::Quantity,
        100,
        "test",
        "a".repeat(64),
    )
    .unwrap();
    assert_eq!(
        typed_artifact_record_batch(&[(seq.clone(), signal)])
            .unwrap()
            .num_rows(),
        1
    );

    let intent = OrderIntent::try_new("o1", "s1", "000001.SZ", Side::Buy, 100, now, next).unwrap();
    let submitted = SubmittedOrder::try_new(intent, next).unwrap();
    assert!(typed_artifact_record_batch(&[(seq.clone(), submitted.clone())]).is_ok());
    let submitted_for_rejection = submitted.clone();
    let accepted = AcceptedOrder::try_new(submitted, next).unwrap();
    assert!(typed_artifact_record_batch(&[(seq.clone(), accepted)]).is_ok());

    let fill = Fill::try_new("f1", "o1", next, 40, 1_000_000_000, 0, 0, 0, 0).unwrap();
    assert!(typed_artifact_record_batch(&[(child, fill)]).is_ok());
    let rejection = Rejection::try_new(
        &submitted_for_rejection,
        next,
        RejectionCode::LimitUp,
        "limit up",
    )
    .unwrap();
    assert!(typed_artifact_record_batch(&[(seq.clone(), rejection)]).is_ok());
    let expiry = Expiry::try_new("o1", next, 60, "expired").unwrap();
    assert!(typed_artifact_record_batch(&[(seq.clone(), expiry)]).is_ok());

    let posting = LedgerPosting::try_new(
        "p1",
        next,
        PostingAccount::Cash,
        DebitCredit::Debit,
        "CNY",
        100,
        "f1",
    )
    .unwrap();
    let counter_posting = LedgerPosting::try_new(
        "p2",
        next,
        PostingAccount::Position,
        DebitCredit::Credit,
        "CNY",
        100,
        "f1",
    )
    .unwrap();
    let next_posting_seq = ArtifactSequence::try_new("r1", 1, 2).unwrap();
    assert!(
        typed_artifact_record_batch(
            &[(seq.clone(), posting), (next_posting_seq, counter_posting),]
        )
        .is_ok()
    );
    let lot = PositionLot::try_new(
        "lot1",
        "000001.SZ",
        100,
        next,
        next + time::Duration::DAY,
        1_000_000_000,
    )
    .unwrap();
    assert!(typed_artifact_record_batch(&[(seq.clone(), lot)]).is_ok());
    let position = PositionSnapshot::try_new(next, "000001.SZ", 100, 0, 100_000_000_000).unwrap();
    assert!(typed_artifact_record_batch(&[(seq.clone(), position)]).is_ok());
    let nav = NavSnapshot::try_new(next, 100_000_000_000, 0, 100_000_000_000).unwrap();
    assert!(typed_artifact_record_batch(&[(seq.clone(), nav.clone())]).is_ok());
    let account = AccountSnapshot::try_new(nav, vec![], vec![]).unwrap();
    let effect = LedgerEffect::try_new(
        "transition-1",
        "settlement:20250102",
        next,
        vec![],
        vec![],
        &account,
        &account,
    )
    .unwrap();
    assert!(typed_artifact_record_batch(&[(seq, effect)]).is_ok());
}
