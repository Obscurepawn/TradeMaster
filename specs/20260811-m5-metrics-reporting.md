# M5 指标与可视化报告实施规格

Status: completed
Owner: Codex

## 目标与边界

M5 读取冻结 run artifacts/benchmark/exposure inputs，生成可复算指标和单文件 Plotly HTML。
不在报告层修补缺失交易数据，不使用 benchmark proxy 冒充真实指数。

## 指标接口

- PerformanceSeries：UTC 时间、NAV、现金、市场价值；严格递增且 NAV 正数。
- BenchmarkSeries：稳定 benchmark ID、UTC 时间与收盘值；按共同 session 显式对齐。
- TradeSummary：成交额、commission/tax/transfer/slippage 与 turnover denominator。
- ExposureObservation：session、industry taxonomy/label、market-cap bucket、portfolio weight。
- StrategyMetrics：累计/年化收益、年化波动、Sharpe、Sortino、Calmar、最大回撤、峰值/谷值/
  修复时间、最长水下期、正收益比例、最好/最差 session、VaR/CVaR、tracking error、information
  ratio、beta/alpha、换手率、总成本和成本占成交额。

## 报告输出

- 策略与任意多个 benchmark 的归一化累计收益同图；不要求固定上证/美股指数集合。
- 独立 drawdown、水下天数、rolling volatility/Sharpe、月度收益热图。
- 行业和市值暴露堆叠图、turnover/transaction costs、关键指标表。
- HTML 使用 inline Plotly JS，可离线打开；标题、单位、空数据/不可计算值均清晰表达。
- 报告 manifest 绑定 run_id、snapshot_id、strategy_id、benchmark IDs 和输入摘要。

## TDD 顺序

1. Red：手算 NAV 的收益、波动、Sharpe、最大回撤、修复期与水下期。
2. Green：纯函数 performance metrics。
3. Red：benchmark 对齐、tracking error/IR/beta/alpha、turnover/成本。
4. Green：benchmark/trade metrics。
5. Red：行业/市值权重和为 1、未知桶、重复观测；HTML 必含多 benchmark/回撤/暴露和 manifest。
6. Green：exposure diagnostics 与 standalone Plotly report。
7. 真实 run-like Parquet inputs 集成、一次有界 fresh review。

## 完成门禁

- 手算结果逐值一致；零波动/未修复回撤返回显式 None，而非 NaN/inf。
- benchmark session 不一致 fail closed 或按显式配置交集，不静默 forward-fill。
- HTML 单文件离线可读，包含输入 identity 和单位。
- Python full tests、Ruff、strict mypy、wheel/sdist 及 Rust regressions 全绿。
- 一次有界 fresh review；最终全局 review 延后到 M6 E2E 后。

## 当前状态

- M0-M4 已完成。
- Red 1：`test_reporting_metrics.py` 因 reporting module 不存在而 collection fail；Green 后
  3 tests 覆盖手算 performance/drawdown/recovery、undefined ratios、turnover/costs。
- Red 2：`test_reporting_report.py` 因 BenchmarkSeries 等接口不存在而 collection fail；
  Green 后 2 tests 覆盖 strict benchmark alignment、tracking/回归指标、exposure 和 standalone HTML。
- M5 初始 focused 5 passed；一次有界 fresh review 结论为 0 Critical / 4 High，集中指出
  NAV 构成未绑定、零成交成本语义、短样本/零方差 benchmark 指标以及 HTML 指标/manifest
  可审计性缺口。
- 唯一 closure cycle 先新增失败测试，再要求 `cash + market_value = NAV`、逐项保留交易成本
  与 average NAV、不可定义 beta/alpha/correlation 返回 `None`、HTML 内部重算策略指标并绑定
  deterministic input SHA-256。未追加局部 review。
- 最终 focused 6 passed；全量 Python 128 passed 且真实 Tushare smoke 未跳过；Ruff、strict
  mypy（30 source files）、wheel/sdist、Rust fmt/Clippy/39 tests/doc-tests、diff 与 credential
  literal scan 全绿。M5 完成，最终全局 review 延后到 M6 E2E 后。
