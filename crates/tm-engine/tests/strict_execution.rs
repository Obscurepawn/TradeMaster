use time::OffsetDateTime;
use tm_core::{
    AcceptedOrder, ExecutionBatch, Expiry, Fill, OrderExecution, OrderIntent, OrderTerminal, Side,
    SubmittedOrder,
};

fn accepted(quantity: i128) -> AcceptedOrder {
    let now = OffsetDateTime::UNIX_EPOCH;
    let later = now + time::Duration::DAY;
    let intent =
        OrderIntent::try_new("o1", "s1", "000001.SZ", Side::Buy, quantity, now, later).unwrap();
    let submitted = SubmittedOrder::try_new(intent, later).unwrap();
    AcceptedOrder::try_new(submitted, later).unwrap()
}

fn fill(id: &str, order_id: &str, quantity: i128) -> Fill {
    Fill::try_new(
        id,
        order_id,
        OffsetDateTime::UNIX_EPOCH + time::Duration::DAY,
        quantity,
        1_000_000_000,
        0,
        0,
        0,
        0,
    )
    .unwrap()
}

#[test]
fn execution_batch_requires_ordered_quantity_conserving_outcomes() {
    let accepted = accepted(100);
    assert!(ExecutionBatch::try_new(std::slice::from_ref(&accepted), vec![]).is_err());
    assert!(OrderExecution::try_new(accepted.clone(), vec![], OrderTerminal::Filled).is_err());
    assert!(
        OrderExecution::try_new(
            accepted.clone(),
            vec![fill("f1", "wrong", 100)],
            OrderTerminal::Filled,
        )
        .is_err()
    );
    assert!(
        OrderExecution::try_new(
            accepted.clone(),
            vec![fill("f1", "o1", 101)],
            OrderTerminal::Filled,
        )
        .is_err()
    );

    let partial = fill("f1", "o1", 40);
    let expiry = Expiry::try_new("o1", partial.event_time(), 60, "day order expired").unwrap();
    let execution = OrderExecution::try_new(
        accepted.clone(),
        vec![partial],
        OrderTerminal::Expired(expiry),
    )
    .unwrap();
    let batch = ExecutionBatch::try_new(&[accepted], vec![execution]).unwrap();
    assert_eq!(batch.executions().len(), 1);
}

#[test]
fn execution_rejects_time_travel_zero_fills_and_duplicate_orders() {
    let accepted = accepted(100);
    assert!(Fill::try_new("zero", "o1", accepted.accepted_at(), 0, 1, 0, 0, 0, 0).is_err());
    assert!(
        Fill::try_new(
            "negative",
            "o1",
            accepted.accepted_at(),
            100,
            -1,
            0,
            0,
            0,
            0,
        )
        .is_err()
    );
    let past_fill = Fill::try_new(
        "past",
        "o1",
        accepted.accepted_at() - time::Duration::SECOND,
        100,
        1,
        0,
        0,
        0,
        0,
    )
    .unwrap();
    assert!(
        OrderExecution::try_new(accepted.clone(), vec![past_fill], OrderTerminal::Filled).is_err()
    );

    let first = OrderExecution::try_new(
        accepted.clone(),
        vec![fill("f1", "o1", 100)],
        OrderTerminal::Filled,
    )
    .unwrap();
    let second = OrderExecution::try_new(
        accepted.clone(),
        vec![fill("f2", "o1", 100)],
        OrderTerminal::Filled,
    )
    .unwrap();
    assert!(ExecutionBatch::try_new(&[accepted.clone(), accepted], vec![first, second]).is_err());
}

#[test]
fn execution_batch_rejects_reordering_and_duplicate_fill_ids() {
    let first = accepted(100);
    let second_intent = OrderIntent::try_new(
        "o2",
        "s2",
        "600000.SH",
        Side::Sell,
        100,
        OffsetDateTime::UNIX_EPOCH,
        OffsetDateTime::UNIX_EPOCH + time::Duration::DAY,
    )
    .unwrap();
    let submitted = SubmittedOrder::try_new(
        second_intent,
        OffsetDateTime::UNIX_EPOCH + time::Duration::DAY,
    )
    .unwrap();
    let second =
        AcceptedOrder::try_new(submitted, OffsetDateTime::UNIX_EPOCH + time::Duration::DAY)
            .unwrap();
    let first_result = OrderExecution::try_new(
        first.clone(),
        vec![fill("same", "o1", 100)],
        OrderTerminal::Filled,
    )
    .unwrap();
    let second_result = OrderExecution::try_new(
        second.clone(),
        vec![fill("same", "o2", 100)],
        OrderTerminal::Filled,
    )
    .unwrap();

    assert!(
        ExecutionBatch::try_new(
            &[first.clone(), second.clone()],
            vec![second_result.clone(), first_result.clone()],
        )
        .is_err()
    );
    assert!(ExecutionBatch::try_new(&[first, second], vec![first_result, second_result]).is_err());
}
