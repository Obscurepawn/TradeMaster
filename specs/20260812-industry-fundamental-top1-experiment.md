# 行业基本面 Top1 十年对照实验

Status: complete
Owner: Codex
Updated: 2026-08-12

## 目标

保持 Top5 正式回测的真实数据、评分、PIT、调仓日、费用、Rust 交易限制和报告口径不变，只将
`top_per_industry` 从 5 改为 1，生成完整十年 E2E 报告并与 Top5 比较。

## 范围与不变量

- 区间仍为 2016-08-12 至 2026-08-11，20 次半年调仓，初始资金 500,000 CNY。
- 使用同一份真实 Tushare Parquet/DuckDB 缓存；Top1 首轮与复读均应为 provider 0。
- 每个有效 SW2021 一级行业恰好选择最高分的 1 只股票，全部入选标的全局等权。
- 不修改九项指标、权重、winsorize、标准化、缺失处理、财务原始版本和静态行业映射口径。
- Rust 仍权威执行100股、T+1、涨跌停、费用、滑点和退出重试。
- Top5 产物保留，只在独立 Top1 输出目录生成结果。

## 实现任务

| Task | Owner | Status | Evidence |
|---|---|---|---|
| 冻结可配置 TopN 运行接口 | Codex | complete | Red: unknown config argument; Green: 11 focused tests passed |
| 真实缓存 Top1 E2E 与复读 | Codex | complete | provider 0 + four identical artifact hashes |
| Top1/Top5 对照分析与报告 | Codex | complete | `top1-vs-top5-comparison.md` |
| 门禁与一次 bounded review | Codex | complete | 143 Python + Rust gates; bounded review PASS |

## 验收标准

- [x] `RealStrategyConfig(top_per_industry=1)` 驱动选择器、strategy/run identity 和报告标题。
- [x] 20 个调仓截面均满足每行业最多1只，逐期目标权重精确为1。
- [x] 使用真实缓存完成 Rust E2E，clean replay provider调用为0且关键哈希一致。
- [x] 比较收益、年化、波动、Sharpe、最大回撤、回撤修复、换手、成本、现金和残留。
- [x] 相关测试、Ruff、mypy、Rust gates通过；完成一次有边界 fresh-context review。

## Decisions

- 这是单变量对照实验，不在看到结果后调整因子权重或其他参数。
- HTML 报告内嵌中文指标与图表阅读指南；明确 52 期年化、零无风险利率、单期 VaR/CVaR、全期累计换手及实际账本暴露口径。

## Current state

- Top1 已完成 2016-08-12 至 2026-08-11 的真实缓存 E2E：20 个截面、560 条入选记录，每行业最多1只且逐期权重和为1。
- Top1 总收益 108.39%、年化 7.76%、波动 16.69%、Sharpe 0.531、最大回撤 -27.64%、换手 22.33x。
- Top5 基线总收益 81.04%、年化 6.23%、波动 13.64%、Sharpe 0.511、最大回撤 -27.43%、换手 12.44x。
- clean replay 的 provider 调用为0，runner request/result、report input、HTML 哈希均一致。
- 新版 HTML report SHA-256 为 `e2d68e8bc5b2d8c3af362ed51854e0743af4ac1d37d3b8ed693669ef7e0dd1b9`，首轮与 replay 一致。

## Changed files

- `specs/INDEX.md`
- `specs/20260812-industry-fundamental-top1-experiment.md`
- `python/trademaster/strategies/industry_fundamental_top5/real.py`
- `python/trademaster/strategies/industry_fundamental_top5/__main__.py`
- `python/trademaster/strategies/industry_fundamental_top5/README.md`
- `python/trademaster/reporting/__init__.py`
- `tests/test_industry_fundamental_top5.py`
- `tests/test_reporting_report.py`
- `artifacts/strategies/industry-fundamental-top1-10year-final/`
- `artifacts/strategies/industry-fundamental-top1-10year-final-replay/`

## Open questions

- none。

## Verification evidence

- TopN Red：`RealStrategyConfig(top_per_industry=1)` 初始触发 unexpected keyword；Green：focused 11 tests passed。
- 报告指南 Red：HTML 缺少 `指标与图表阅读指南`；Green：focused test、Ruff、mypy 通过。
- 全量门禁：Python `143 passed`；Ruff、mypy、Cargo fmt、Clippy、workspace tests 全部通过。
- E2E：真实 Tushare 缓存，首轮和 clean replay 均 provider 0；runner request/result、report input、新版 HTML 四个哈希逐项一致。
- bounded fresh-context review：PASS，限定范围内无 Critical/High；独立确认 20 个截面、560 条选择、行业 rank=1、逐期权重和精确为1、对照指标一致。
