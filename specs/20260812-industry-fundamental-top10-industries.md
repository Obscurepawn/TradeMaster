# 基本面最强 Top10 行业 × 行业内 Top1 十年实验

Status: complete
Owner: Codex
Updated: 2026-08-12

## 目标

在已完成的真实 Tushare 十年行业基本面 Top1 回测上增加行业择优：每个半年调仓截面只保留基本面平均分最高的10个行业，再从每个行业选择原行业中性综合分最高的1只股票，全局等权，并与全行业 Top1 基线比较。

## 评分接口与不变量

- 股票选择分保持原规则：九项指标在行业内 winsorize、标准化、加权，行业内 Top1。
- 不能直接平均上述行业中性分，因为各行业均值按构造接近0。
- 行业选择分：对同一批合格候选，将同样九项指标改在全体候选之间 winsorize、标准化并按原权重合成全市场股票分；行业分为该行业合格股票全市场分的等权算术平均。
- 行业按 `(-industry_mean_score, industry_id)` 稳定排序，保留前10；最终通常持有10只，全局目标等权。
- 除行业筛选外，数据、PIT、20次半年调仓、Top1、费用、滑点、Rust交易限制、初始资金500,000 CNY和报告口径均与基线一致。

## 实现任务

| Task | Owner | Status | Evidence |
|---|---|---|---|
| 定义 Top行业公共接口 | Codex | complete | strategy/config regression checks |
| CLI、运行身份、产物和报告贯穿 | Codex | complete | config/selection/summary/report artifacts |
| 真实十年 E2E 与 clean replay | Codex | complete | provider 0 + four identical hashes |
| 与全行业 Top1 对照、门禁和 bounded review | Codex | complete | comparison + gates + bounded review PASS |

## 验收标准

- [x] `top_industries_by_mean_score=10` 可配置、校验且默认 `None` 保持原行为。
- [x] 回归检查证明行业按全市场基本面分均值选取、稳定处理并精确等权。
- [x] 20个真实截面均只含10个行业、每行业1只、逐期权重和精确为1。
- [x] 首轮和 clean replay 均 provider 0，关键产物哈希一致。
- [x] 输出 HTML、Markdown、JSON及与全行业 Top1 的对照结论。
- [x] Python/Rust门禁通过并完成一次范围受限 fresh review。

## Decisions

- “平均表现”定义为行业合格成分股的全市场九项基本面综合分均值，而非历史价格收益。
- 行业平均不按市值加权，避免大市值股票支配行业选择。

## Current state

- 策略已实现并完成真实十年 E2E：总收益54.16%、年化4.50%、波动17.92%、Sharpe 0.335、最大回撤-40.63%。
- 全行业Top1基线为总收益108.39%、年化7.76%、波动16.69%、Sharpe 0.531、最大回撤-27.64%；Top10行业筛选明显更差。
- 20个截面共200条选择，结构校验零违规；首轮/replay均provider 0且四个关键哈希一致。

## Changed files

- `specs/INDEX.md`
- `specs/20260812-industry-fundamental-top10-industries.md`
- `python/trademaster/strategies/industry_fundamental_top5/__init__.py`
- `python/trademaster/strategies/industry_fundamental_top5/real.py`
- `python/trademaster/strategies/industry_fundamental_top5/__main__.py`
- `python/trademaster/strategies/industry_fundamental_top5/README.md`
- `tests/test_industry_fundamental_top5.py`
- `artifacts/strategies/industry-fundamental-top10-industries-top1-10year-final/`
- `artifacts/strategies/industry-fundamental-top10-industries-top1-10year-final-replay/`

## Open questions

- none。

## Verification evidence

- 真实E2E及clean replay均provider 0；request `a076c7d6…abbe4`、result `b9e7b6dd…c0f56`、report input `0db6376b…dbe5d`、HTML `0cbaf2fd…3f1ae`逐项一致。
- 选择审计：20期、200行；每期10行业、每行业1只、行业排名1–10、单只权重0.1、逐期权重和精确为1，零违规。
- Python `144 passed`；Ruff check、mypy、Cargo fmt、Clippy、Rust workspace tests通过；本次4个Python改动文件的Ruff format check通过。
- 全仓Ruff format check另显示30个既有文件的历史格式漂移；未批量修改无关文件。
- bounded fresh-context review：PASS，限定范围无Critical/High；独立重算评分、选择、产物哈希、指标表和行业集中度均一致。
