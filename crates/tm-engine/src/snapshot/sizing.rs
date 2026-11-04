//! Only previous closing marks and the opening account are visible here.
use super::*;

pub(super) struct Plan {
    pub instrument: String,
    pub side: Side,
    pub quantity: i128,
    pub desired: i128,
    pub cap: Option<i128>,
    pub reserve: i128,
}
pub(super) struct Sizing {
    pub reserve: i128,
    pub buy_budget: i128,
    pub plans: Vec<Plan>,
    pub missing_mark: Option<String>,
}

pub(super) struct Charges {
    pub commission: i128,
    pub tax: i128,
    pub transfer_fee: i128,
    pub slippage: i128,
}
impl Charges {
    pub fn total(&self) -> Result<i128, SnapshotError> {
        [self.commission, self.tax, self.transfer_fee, self.slippage]
            .into_iter()
            .try_fold(0i128, |sum, v| {
                sum.checked_add(v).ok_or(SnapshotError::Overflow)
            })
    }
}

pub(super) struct Costs {
    pub fees: CnFeeSchedule,
    pub slippage_ppm: u32,
}
impl Costs {
    pub fn charges(
        &self,
        spec: &InstrumentSpec,
        side: Side,
        quantity: i128,
        price: i128,
    ) -> Result<Charges, SnapshotError> {
        let q = self.fees.quote_side(
            spec,
            side,
            Quantity::positive(quantity)?,
            Fixed8::from_scaled(price)?,
        )?;
        Ok(Charges {
            commission: q.commission().scaled(),
            tax: q.tax().scaled(),
            transfer_fee: q.transfer_fee().scaled(),
            slippage: ppm_cost(mul(quantity, price)?, self.slippage_ppm)
                .ok_or(SnapshotError::Overflow)?,
        })
    }
    fn reserve(&self, spec: &InstrumentSpec, qty: i128, cap: i128) -> Result<i128, SnapshotError> {
        if qty == 0 {
            return Ok(0);
        }
        add(
            mul(qty, cap)?,
            self.charges(spec, Side::Buy, qty, cap)?.total()?,
        )
    }
    fn affordable(
        &self,
        spec: &InstrumentSpec,
        cap: i128,
        desired: i128,
        budget: i128,
    ) -> Result<i128, SnapshotError> {
        let lot = i128::from(spec.buy_lot_size());
        if cap == 0 {
            return Ok(0);
        }
        let mut high = desired.min(budget / cap) / lot;
        let mut low = 0;
        while low < high {
            let mid = low + (high - low + 1) / 2;
            if self.reserve(spec, mid * lot, cap)? <= budget {
                low = mid;
            } else {
                high = mid - 1;
            }
        }
        Ok(low * lot)
    }
}
pub(super) fn size(
    batch: &TargetBatch,
    account: &SnapshotAccount,
    marks: &BTreeMap<String, i128>,
    specs: &BTreeMap<String, InstrumentSpec>,
    policy: &Policy,
    costs: &Costs,
) -> Result<Sizing, SnapshotError> {
    let equity = account.equity.scaled();
    let weight: i128 = batch.weights.values().copied().map(i128::from).sum();
    let reserve = mul(ceil_div(mul(equity, U - weight)?, U * CENT)?, CENT)?;
    let budget = (account.cash.scaled() - reserve).max(0);
    let positions: BTreeMap<_, _> = account
        .positions
        .iter()
        .map(|p| (p.instrument_id().0.as_str(), p))
        .collect();
    let universe: BTreeSet<_> = positions
        .keys()
        .copied()
        .chain(batch.weights.keys().map(String::as_str))
        .collect();
    let mut sells = vec![];
    let mut buys = vec![];
    let mut buy_demand = 0;
    for id in universe {
        let w = i128::from(batch.weights.get(id).copied().unwrap_or(0));
        let p = positions.get(id);
        let held = p.map_or(0, |p| p.quantity().units());
        if held == 0 && w == 0 {
            continue;
        }
        let Some(&mark) = marks.get(id) else {
            return Ok(Sizing {
                reserve,
                buy_budget: budget,
                plans: Vec::new(),
                missing_mark: Some(id.to_owned()),
            });
        };
        let spec = &specs[id];
        let lot = i128::from(spec.buy_lot_size());
        let target = (mul(equity, w)? / U / mark) / lot * lot;
        if target < held {
            let difference = held - target;
            let want = if target == 0 {
                difference
            } else {
                difference / lot * lot
            };
            if want == 0 {
                continue;
            }
            let sellable = p.map_or(0, |p| p.sellable_quantity().units());
            // An exit can sell the full remaining odd lot. Other sales use whole lots.
            let available = want.min(sellable);
            let qty = if target == 0 && available == held {
                available
            } else {
                available / lot * lot
            };
            sells.push(Plan {
                instrument: id.into(),
                side: Side::Sell,
                desired: want,
                quantity: qty,
                cap: None,
                reserve: 0,
            });
        } else if target > held {
            let want = target - held;
            let lots = want / lot * lot;
            if lots == 0 {
                continue;
            }
            let tick = spec.tick_size().scaled();
            let cap =
                (mul(mark, 1_000_000 + i128::from(policy.buy_cap_ppm))? / 1_000_000) / tick * tick;
            buy_demand = add(buy_demand, mul(lots, mark)?)?;
            buys.push(Plan {
                instrument: id.into(),
                side: Side::Buy,
                desired: lots,
                quantity: 0,
                cap: Some(cap),
                reserve: 0,
            });
        }
    }
    let mut left = budget;
    for plan in &mut buys {
        let spec = &specs[&plan.instrument];
        let cap = plan.cap.unwrap();
        let demand = mul(plan.desired, marks[&plan.instrument])?;
        let share = if demand == buy_demand {
            budget
        } else {
            mul(budget, demand)? / buy_demand
        };
        plan.quantity = costs.affordable(spec, cap, plan.desired, share)?;
        plan.reserve = costs.reserve(spec, plan.quantity, cap)?;
        left -= plan.reserve;
    }
    // Only indivisible lots remain in a typical fully invested allocation. Rank
    // each extra lot by the value still missing, with stable instrument ties.
    loop {
        let mut best: Option<(usize, i128, i128)> = None;
        for (i, plan) in buys.iter().enumerate() {
            let spec = &specs[&plan.instrument];
            let lot = i128::from(spec.buy_lot_size());
            if plan.quantity + lot > plan.desired || plan.cap == Some(0) {
                continue;
            }
            let next = costs.reserve(spec, plan.quantity + lot, plan.cap.unwrap())?;
            if next - plan.reserve > left {
                continue;
            }
            let deficit = mul(plan.desired - plan.quantity, marks[&plan.instrument])?;
            if best.is_none_or(|(_, highest, _)| deficit > highest) {
                best = Some((i, deficit, next));
            }
        }
        let Some((i, _, next)) = best else {
            break;
        };
        let plan = &mut buys[i];
        left -= next - plan.reserve;
        plan.reserve = next;
        plan.quantity += i128::from(specs[&plan.instrument].buy_lot_size());
    }
    sells.extend(buys);
    Ok(Sizing {
        reserve,
        buy_budget: budget,
        plans: sells,
        missing_mark: None,
    })
}
