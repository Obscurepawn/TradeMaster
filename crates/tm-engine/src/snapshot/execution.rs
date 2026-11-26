use super::*;

enum OpenOutcome {
    FillAt(i128),
    Reject(String),
    Missing(String),
}
fn check_open(
    row: Option<Opening<'_>>,
    cap: Option<i128>,
    enforce_price_limits: bool,
) -> OpenOutcome {
    let Some(row) = row else {
        return OpenOutcome::Missing("missing_open_row".into());
    };
    if !row.documented {
        return OpenOutcome::Missing("missing_open_evidence".into());
    }
    if row.corporate_action_gap {
        return OpenOutcome::Missing("corporate_action_gap".into());
    }
    match row.permission {
        Permission::Unknown => {
            return OpenOutcome::Missing(row.reason.unwrap_or("unknown_open_status").into());
        }
        Permission::No if enforce_price_limits || !row.blocked_by_price_limit => {
            return OpenOutcome::Reject(row.reason.unwrap_or("blocked_at_open").into());
        }
        Permission::Yes | Permission::No => (),
    }
    if row.suspended {
        return OpenOutcome::Reject("suspended".into());
    }
    let Some(price) = row.price.map(i128::from) else {
        return OpenOutcome::Missing("missing_open".into());
    };
    if cap.is_some_and(|cap| price > cap) {
        return OpenOutcome::Reject("above_buy_cap".into());
    }
    OpenOutcome::FillAt(price)
}

#[allow(clippy::too_many_arguments)]
pub(super) fn execute_plan<'a>(
    ledger: &mut LotLedger<Sessions>,
    costs: &Costs,
    at_open: impl Fn(&str, Side) -> Option<Opening<'a>>,
    session: &str,
    open: OffsetDateTime,
    batch: &TargetBatch,
    batch_index: usize,
    sizing: Sizing,
    out: &mut SnapshotResult,
    enforce_price_limits: bool,
    detail: bool,
    feedback: bool,
) -> Result<bool, SnapshotError> {
    let attempt_id = format!(
        "attempt-{batch_index}-{}",
        out.batches[batch_index].attempts
    );
    let mut attempt = Attempt {
        session: session.into(),
        batch_id: batch.batch_id.clone(),
        attempt_id: attempt_id.clone(),
        reserve: sizing.reserve,
        buy_budget: sizing.buy_budget,
        shortfalls: vec![],
    };
    let decision = open_time(&batch.decision_session)? + Duration::hours(1);
    if let Some(id) = sizing.missing_mark {
        let reason = "missing_previous_close";
        if detail {
            attempt.shortfalls.push(Shortfall {
                instrument_id: id.clone(),
                reason: reason.into(),
                remaining_quantity: None,
                order_id: None,
            });
            out.attempts.push(attempt);
        }
        return Err(SnapshotError::Gap(id, reason.into(), "sizing"));
    }
    // Check every planned order before posting fills. An unknown opening
    // cannot produce a partly executed portfolio with unreported exposure.
    let outcomes: Vec<_> = sizing
        .plans
        .iter()
        .map(|plan| {
            (plan.quantity > 0).then(|| {
                check_open(
                    at_open(&plan.instrument, plan.side),
                    plan.cap,
                    enforce_price_limits,
                )
            })
        })
        .collect();
    let missing = sizing
        .plans
        .iter()
        .zip(&outcomes)
        .find_map(|(p, o)| match o {
            Some(OpenOutcome::Missing(reason)) => Some(SnapshotError::Gap(
                p.instrument.clone(),
                reason.clone(),
                "open",
            )),
            _ => None,
        });
    let mut complete = true;
    for (i, (plan, outcome)) in sizing.plans.iter().zip(outcomes).enumerate() {
        if plan.quantity < plan.desired {
            complete = false;
            let reason = match plan.side {
                Side::Sell => "unsettled_shares",
                Side::Buy if plan.cap == Some(0) => "cap_below_tick",
                Side::Buy => "cash_budget",
            };
            if detail {
                attempt.shortfalls.push(Shortfall {
                    instrument_id: plan.instrument.clone(),
                    reason: reason.into(),
                    remaining_quantity: Some(plan.desired - plan.quantity),
                    order_id: None,
                });
            }
        }
        let Some(outcome) = outcome else {
            continue;
        };
        let order_id = format!("{attempt_id}-order-{i}");
        let mut reason = None;
        let mut status = "rejected";
        match outcome {
            OpenOutcome::Reject(r) => reason = Some(r),
            OpenOutcome::Missing(r) => {
                status = "unknown";
                reason = Some(r);
            }
            OpenOutcome::FillAt(_) if missing.is_some() => {
                status = "not_executed";
                reason = Some("incomplete_open".into());
            }
            OpenOutcome::FillAt(price) => {
                let fees = costs.charges(
                    &ledger.instruments[&plan.instrument],
                    plan.side,
                    plan.quantity,
                    price,
                )?;
                let total = fees.total()?;
                let notional = mul(plan.quantity, price)?;
                if plan.side == Side::Sell && total >= notional {
                    reason = Some("costs_exceed_proceeds".into());
                } else if plan.side == Side::Buy && add(notional, total)? > plan.reserve {
                    reason = Some("reserved_cash_exceeded".into());
                } else {
                    let intent = OrderIntent::try_new(
                        &order_id,
                        &batch.batch_id,
                        &plan.instrument,
                        plan.side,
                        plan.quantity,
                        decision,
                        open,
                    )?;
                    let accepted =
                        AcceptedOrder::try_new(SubmittedOrder::try_new(intent, open)?, open)?;
                    let fill_id = format!("{order_id}-fill");
                    let fill = Fill::try_new(
                        &fill_id,
                        &order_id,
                        open,
                        plan.quantity,
                        price,
                        fees.commission,
                        fees.tax,
                        fees.transfer_fee,
                        fees.slippage,
                    )?;
                    ledger.apply_unaudited(&OrderExecution::try_new(
                        accepted,
                        vec![fill],
                        OrderTerminal::Filled,
                    )?)?;
                    status = "filled";
                    out.fill_count += 1;
                    out.total_costs = add(out.total_costs, total)?;
                    out.turnover_notional = add(out.turnover_notional, notional)?;
                    if detail || feedback {
                        out.fills.push(Trade {
                            session: session.into(),
                            batch_id: batch.batch_id.clone(),
                            attempt_id: attempt_id.clone(),
                            order_id: order_id.clone(),
                            fill_id,
                            instrument_id: plan.instrument.clone(),
                            side: plan.side,
                            quantity: plan.quantity,
                            price,
                            commission: fees.commission,
                            tax: fees.tax,
                            transfer_fee: fees.transfer_fee,
                            slippage: fees.slippage,
                        });
                    }
                }
            }
        }
        out.order_count += 1;
        if let Some(r) = &reason {
            out.rejected_order_count += u64::from(status == "rejected");
            complete = false;
            if detail {
                attempt.shortfalls.push(Shortfall {
                    instrument_id: plan.instrument.clone(),
                    reason: r.clone(),
                    remaining_quantity: Some(plan.quantity),
                    order_id: Some(order_id.clone()),
                });
            }
        }
        if detail {
            out.orders.push(Order {
                session: session.into(),
                batch_id: batch.batch_id.clone(),
                attempt_id: attempt_id.clone(),
                order_id,
                instrument_id: plan.instrument.clone(),
                side: plan.side,
                quantity: plan.quantity,
                buy_cap: plan.cap,
                reserved_cash: plan.reserve,
                status,
                reason,
            });
        }
    }
    if detail {
        out.attempts.push(attempt);
    }
    if let Some(error) = missing {
        return Err(error);
    }
    Ok(complete)
}
