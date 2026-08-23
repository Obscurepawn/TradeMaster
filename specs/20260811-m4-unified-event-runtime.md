# M4 统一事件运行时实施规格

Status: completed
Owner: Codex

## 目标与边界

M4 实现唯一权威回测 event loop，使 Python 预计算的向量化 signals 与无法向量化的逐事件策略
最终都进入相同的 Rust OrderValidator → ExecutionModel → Ledger 链路。M4 生成内存中的完整
运行记录；Parquet run writer/manifest 与报告接线在 M5/M6 完成。

## 公共接口先行

- ScheduledSignalStrategy：消费按 eligible_execution_time 排序的 quantity signals，在事件
  时点转换为稳定 OrderIntent；target-weight 信号必须经显式 portfolio rebalancer，不在内核
  中猜测现金分配。
- EventLoop：按 event_time + phase 严格驱动 settlement、session open、bar close。
- RuntimeInstrumentRegistry：按 instrument ID 提供不可变 InstrumentSpec；未知标的 fail closed。
- RunTrace：按 event sequence 保存 signals/orders/accepted/rejections/executions/ledger effects/
  account snapshots，顺序可直接映射共享 Arrow artifacts。
- 在线 StrategyAdapter 与预计算 ScheduledSignalStrategy 都只实现同一个 StrategyAdapter
  seam；运行时不区分策略来源。

## 事件顺序与语义

1. settlement phase 解锁到期 lots；
2. session-open market snapshot 冻结 portfolio view；
3. strategy 读取同一 snapshot/portfolio，产生已到 eligible time 的 orders；
4. 每单构造 exact-instrument ValidationView，接受或记录 rejection；
5. accepted orders 同批执行，逐 execution 原子记账；
6. bar-close 只更新估值和供策略观察；收盘产生的新信号最早下一 session open eligible。

同一 event 内 instrument/order/fill 的顺序稳定；任何组件错误终止 run，不静默跳过。

## TDD 顺序

1. Red：scheduled quantity signal 的时间、一次消费、稳定 order ID 和未知标的。
2. Green：预计算 signal adapter。
3. Red：两 session 买入→当日卖出拒绝→settlement→卖出完整 event trace。
4. Green：统一 EventLoop/RunTrace。
5. Red：在线 strategy 与预计算 strategy 在等价信号下产生相同 executions/NAV。
6. Green：统一 adapter seam 与 deterministic replay。
7. 组合门禁与一次有界 fresh review。

## 完成门禁

- 两类策略共享同一 validator/execution/ledger 实例链。
- T-close 不可同 close 成交；每条 scheduled signal 仅消费一次。
- 重放相同 snapshot/config/signals 得到相同 trace/state hashes。
- Rust fmt/Clippy/workspace tests/doc-tests 与 Python/shared-schema regression 全绿。
- 一次 fresh-context 有界 review；最终全局 review 留到 M6 E2E 后。

## 当前状态

- M0-M3 已完成。
- Red 1：`scheduled_strategy.rs` 因缺少 `ScheduledSignalStrategy` 无法编译；Green 后
  2 tests 覆盖 T-close 等待、下一 open、一次消费、整数量与 target-weight fail closed。
- Red 2：`unified_event_loop.rs` 因缺少 `EventLoop` 无法编译；Green 后覆盖预计算信号、
  validator、execution、T+1 settlement、ledger 和 final NAV 的统一 round-trip。
- 有界 fresh review：0 Critical、4 High；settlement 缺失、同期开 target delta、trace
  完整性/尾部 signals、pre-batch 资金视图均已合并为一次 Red→Green 修复，未开启第二轮。
- Review closure Green：EventLoop 强制 settlement→open；scheduled targets 使用虚拟持仓；
  RunTrace 增加 signals 与 event_seq；策略暴露 pending count；订单逐笔重估 portfolio 并执行记账。
- 最终 Rust workspace 39 tests、doc-tests、fmt、Clippy `-D warnings` 全绿；Python
  122 passed、0 skipped，Ruff/strict mypy 与 `git diff --check` 通过。
