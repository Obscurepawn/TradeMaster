# M2 因子与信号层实施规格

Status: completed
Owner: Codex

## 目标与边界

M2 在 M1 的不可变 PIT snapshot 之上实现基础因子管理、因子计算和两类策略信号：

1. 全市场横截面选股：按调仓时点计算因子，选择 Top-N，并产生目标权重信号；
2. 单股票/ETF 买卖点：按单标的时间序列规则产生目标数量或买卖方向信号。

M2 不维护现金、持仓、T+1 lot，也不判断涨跌停/停牌；这些由 M3 Rust 规则与账本负责。

## 公共接口先行

- `FactorDefinition`：稳定 `factor_id/version`、依赖 dataset/fields、lookback sessions、输出语义。
- `FactorRegistry`：注册、按 ID/version 查找、重复和未知依赖 fail closed。
- `FactorExecutor.compute()`：只接受带 snapshot provenance 且 event/known cutoff 合法的 Arrow 输入；
  输出固定 schema：`instrument_id,event_time,factor_id,factor_version,value,is_valid`，并继承 snapshot
  provenance。
- `CrossSectionalSignalConfig`：Top-N、升/降序、最少有效标的、等权/显式权重、下次可执行时间。
- `SingleAssetSignalConfig`：目标 instrument、买卖阈值/规则、目标 quantity、下次可执行时间。
- 两类 signal 均输出运行时可消费的稳定 schema：`signal_id,strategy_id,instrument_id,signal_time,
  eligible_execution_time,intent_type,value,reason,snapshot_id`。收盘因子信号必须满足 T+1 event timing，
  `eligible_execution_time > signal_time`。

## 基础实现

- `MomentumFactor`：以复权价格计算 N-session 动量，严格按 instrument/event time 排序，窗口不足为
  invalid，不跨标的填充。
- `ValueFactor`：基于 PIT `daily_basic` 的 `float_market_value` 或 `total_market_value` 产生可排序值。
- `CompositeFactor`：对输入因子做可配置 winsorize、z-score、方向和权重组合；缺失策略显式配置。
- `TopNTargetWeightGenerator`：有效样本数达标后稳定排序，tie-break 使用 `instrument_id`，等权之和
  精确为 1.00000000。
- `ThresholdQuantitySignalGenerator`：单标的因子上穿/下穿阈值产生 quantity intent；不直接读取未来行。

## TDD 顺序

1. Red：registry、固定 Arrow schema、snapshot provenance、cutoff 与重复 ID 接口测试失败。
2. Green：最小 registry/executor 和 schema validator。
3. Red：手算 3 标的动量、窗口不足、排序/tie、Top-N 权重、T+1 timing。
4. Green：Momentum/Value/Composite 与横截面信号。
5. Red：单标的阈值序列、ETF identity、重复时间/未来行、quantity intent。
6. Green：单标的 signal generator。
7. 集成：使用 M1 真实 Parquet/DuckDB snapshot 计算因子并生成两类信号；一次有界 fresh review。

## 完成门禁

- 手算因子与权重结果逐值一致；输出 schema/provenance 固定。
- T 日收盘数据产生的信号最早 T+1 执行。
- 横截面与单标的/ETF 两条集成链路均使用真实 Arrow + M1 snapshot。
- Python pytest、Ruff、strict mypy、wheel/sdist 通过。
- 一次 fresh-context 有界 review；高强度全局 review 延后到 M6 E2E 后。

## 当前状态

- M1 已完成。
- Registry、Executor、固定 Arrow schema 与 provenance/cutoff 边界已完成 Red→Green。
- Momentum、Value、Composite、Top-N 等权信号、单标的/ETF threshold quantity 信号与 T+1
  schedule 均已完成 Red→Green。
- 真实 Parquet 文件、DuckDB catalog 与 PIT snapshot 集成测试已覆盖横截面 Top-N 和 ETF
  买点信号；测试 provider 被设置为禁止回源，从而证明数据来自 M1 本地权威对象。

## TDD 与验证证据

- Red 1：`tests/test_factor_interfaces.py` 最初因 `trademaster.factors` 不存在而 collection
  fail；随后实现 registry/executor/schema/provenance 边界。
- Red 2：`tests/test_momentum_and_signals.py` 最初因 `MomentumFactor` 不存在而 collection
  fail；随后实现逐标的 session lag、稳定 Top-N、精确 Decimal 等权与严格下一时点执行。
- Red 3：`tests/test_value_composite_threshold.py` 最初因 `CompositeComponent` 不存在而
  collection fail；随后实现市值因子、横截面 z-score 组合与单标的 crossing 信号。
- Green focused：M2 四个测试文件共 11 passed；Ruff 与 strict mypy 通过。
- Green full：注入本地凭证后 Python 全量 118 passed、0 skipped，包含真实 Tushare
  `trade_cal` smoke；wheel/sdist 成功且 factors/signals、`py.typed` 与共享 schema 均打包。
- 有界 fresh review：0 Critical、7 High；High 均为主接口/主链路问题，已一次性定点关闭，
  未启动第二轮审查。
- Review closure Red：新增/调整测试后 focused 首次为 10 failed、2 passed，分别锁定
  execution timing、artifact enum、Protocol、stale crossing、factor identity、`known_at`
  与 dependency validation。
- Review closure Green：M2 focused 16 passed；注入本地凭证后的全量 Python 为
  122 passed、0 skipped；Ruff、strict mypy（27 source files）、wheel/sdist 与
  `git diff --check` 均通过，仓库内凭证精确匹配为 0。
