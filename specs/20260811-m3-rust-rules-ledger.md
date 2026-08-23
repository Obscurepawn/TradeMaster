# M3 Rust A股规则与账本实施规格

Status: completed
Owner: Codex

## 目标与边界

M3 在 M0 已冻结的 Rust contracts/traits 上实现可直接用于正式回测的 A股日线订单校验、成交、
费用和 lot-aware 账本。M3 不负责策略调度和完整 event loop（M4），也不负责指标与报告（M5）。

## 公共接口先行

- `CnAshareOrderValidator` 实现 `OrderValidator`，只读取 `SubmittedOrder + ValidationView`，
  拒绝过早提交、缺行情、停牌、涨停买、跌停卖、非买入整数手、现金不足和可卖数量不足。
- `DailyBarExecutionModel` 实现 `ExecutionModel`，在显式 market event 上以配置价格字段成交，
  不穿透订单数量；无法成交时生成可审计 expiry/rejection，而不是静默丢单。
- `CnFeeSchedule` 实现 `FeeSchedule`，精确计算佣金、最低佣金、卖出印花税和沪市过户费；
  所有费率及生效版本由配置显式给出，不硬编码为无版本常量。
- `LotLedger` 实现 `Ledger`，以 `Fixed8/i128` 原子更新现金、持仓和 FIFO lots；股票按
  `InstrumentSpec.settlement` 决定 T+0/T+1 可卖时点，settlement 只解锁 lot。
- 每个 fill 生成来源明确且平衡的 posting group；失败更新保持原子性并不改变 state hash。

## 交易语义

- 涨停判断：买单在 bar 可成交价格达到/超过 `up_limit` 时拒绝；跌停卖同理。
- 停牌：任何方向均拒绝。
- 买入数量必须是 `buy_lot_size` 的整数倍；卖出允许一次性清理不足一手的剩余持仓，其他卖单
  仍按配置的卖出手数策略执行。
- 资金量小且不造成市场冲击，因此 M3 默认全量成交，不实现 volume participation 或 impact；
  slippage 是显式可配置的确定性成本。
- 股票 T+1、可配置 T+0 ETF 都通过 `SettlementPolicy` 表达，不按代码后缀猜测。

## TDD 顺序

1. Red：校验器的 100 股、停牌、涨停买、跌停卖、现金和 sellable quantity 手算表。
2. Green：最小 `CnAshareOrderValidator`，每种拒绝码与时间精确一致。
3. Red：成交价、费用（最低佣金/印花税/过户费）、slippage 和 expiry 手算表。
4. Green：`CnFeeSchedule + DailyBarExecutionModel`。
5. Red：买入建 lot、T 日不可卖、下一 session 解锁、FIFO 卖出、现金/持仓/NAV/posting balance。
6. Green：原子 `LotLedger` 与 settlement。
7. 组合回归：买入→当日卖出拒绝→T+1 解锁→卖出，输出完整 checked effects；一次有界 review。

## 完成门禁

- 所有 A股限制均有正反例；费用与账本逐值手算一致。
- T+1 不依赖自然日加一天，而使用外部 `TradingCalendar` 的下一 session。
- 无浮点数进入交易成本、现金、持仓或账本。
- `cargo fmt --check`、Clippy `-D warnings`、workspace tests/doc-tests/package 通过。
- Python/shared schemas 回归不受破坏。
- 一次 fresh-context 有界 review；高强度全局 review 延后到 M6 E2E 后。

## 当前状态

- M0 Rust domain contracts 与 public traits 已冻结并通过实现性测试。
- M1/M2 已完成。
- Red 1：`a_share_rules.rs` 因缺少 `CnAshareOrderValidator/CnFeeSchedule` 无法编译；
  Green 后 3 tests 覆盖费用与六类 A股拒绝。
- Red 2：`daily_execution.rs` 因缺少 `DailyBarExecutionModel` 无法编译；Green 后 2 tests
  覆盖全量成交、精确费用/slippage 与 missing-bar expiry。
- Red 3：`lot_ledger.rs` 因缺少 `LotLedger` 无法编译；Green 后覆盖买入建 lot、T 日卖出
  原子失败、下一 session unlock、FIFO 卖出、cash/NAV/posting/state hash。
- 有界 fresh review：0 Critical、3 High；分别为 stale bar、view/order instrument
  错绑、现金校验漏计 slippage。三项已合并为一次 Red→Green 修复，没有启动第二轮审查。
- Review closure Green：validator/execution 新增 exact event bar 与 exact instrument
  绑定；validator 与 execution 共享相同 ppm slippage 手算。最终 Rust workspace
  34 tests、doc-tests、fmt、Clippy `-D warnings` 全绿；Python 122 passed、0 skipped，
  Ruff/strict mypy 与 `git diff --check` 通过。
- `tm-core cargo package` 成功；`tm-engine` 单独打包因 crates.io 尚无本地
  `tm-core 0.1.0` 而受发布顺序限制，本地 workspace build/test 不受影响。
