# 行业中性基本面 Top5 半年调仓策略

Status: complete
Owner: Codex
Updated: 2026-08-12

## 目标

在 TradeMaster 中沉淀一个独立、可配置、可复读的基本面策略：在每个行业内部计算综合基本面
分数，选择最多 5 只股票，对全部入选标的给出精确等权目标，每半年调仓一次，并使用真实
Tushare 数据、Rust 权威事件运行时和现有报告层生成策略报告。

## 本次报告口径

- 样本池：每个调仓时点最新可得的沪深300历史成分股；策略配置保留 `all_a_share` 扩展点。
- 行业：SW2021 一级行业；因接口只返回当前成员，本报告将当前映射静态应用于全部历史截面。
- 区间：2016-08-12 至 2026-08-11；5 月、11 月首个不早于 6 日的交易日收盘产生信号，下一交易日
  开盘执行，预期覆盖 20 次半年调仓。
- 初始资金：500,000 CNY。等权是 target-weight intent；Rust 在执行时按当期 NAV、开盘价和
  100 股整数手换算，无法买入一手的标的保持 0 持仓并在报告中体现现金，不伪造等权成交。
- benchmark：沪深300价格指数 `000300.SH`。
- 费用：佣金 3bp、最低 5 元、卖出印花税 5bp、上交所股票过户费 0.1bp、滑点 5bp。

## 评分模型

先在每个 SW2021 一级行业内对每个指标做 5%/95% winsorize，再做截面 z-score；指标缺失采用
该行业的中性分 0，但估值字段 `pe_ttm/pb` 必须为正，且至少 5 个指标有效。

| 维度 | 指标与方向 | 总权重 |
|---|---|---:|
| 价值 | earnings yield `1/pe_ttm` 15%，book yield `1/pb` 10%，`dv_ttm` 10% | 35% |
| 质量 | `roe` 15%，`grossprofit_margin` 10%，`ocf_to_or` 10% | 35% |
| 成长 | `q_sales_yoy` 10%，`q_profit_yoy` 10% | 20% |
| 安全 | `debt_to_assets` 反向 10% | 10% |

金融行业常见的 `grossprofit_margin/roa` 缺失不会跨行业惩罚；行业内缺失值为中性，实际有效字段
数量会写入 selection artifact。得分降序、`instrument_id` 升序作为稳定 tie-break，每行业
`min(5, eligible_count)`；所有入选标的 target weights 之和精确为 1.00000000。

## 数据与 PIT 不变量

- token 只从 `TUSHARE_TOKEN` 读取，不进入请求 key、Parquet、DuckDB、snapshot 或报告。
- 每个 Tushare 请求先检查内容寻址 Parquet 缓存；DuckDB 只记录/查询 immutable Parquet。
- 财务指标只允许 `ann_date` 严格早于 signal date；由于 Tushare 不提供修订实际可用时间，历史
  回测只使用 `update_flag=0` 原始版本，排除所有 `update_flag=1` 修订值。
- 估值使用 signal date 前一交易日的 `daily_basic`，规避当日收盘后才发布的截面数据；沪深300
  成分使用各调仓月最新历史 `index_weight`。
- Tushare `index_member_all` 当前只返回 `is_new=Y`，对 `is_new=N` 的真实探针返回空表，因此本次
  报告使用当前 SW2021 一级行业映射并明确标注该限制，不声称它是历史 PIT 行业分类。
- 股票 OHLC 与涨跌停价使用 `adj_factor` 归一到区间末端的前复权连续价格，以避免送转分红造成
  虚假收益；这是研究价格执行近似，不等同于逐笔现金分红/拆并股账本。
- 净值使用 2016-08-12 的现金账户作为基准，此后仅在每个自然周最后一个交易日采样，绩效按
  52 期年化；
  交易日历仍完整传给 Rust，用于下一交易日资格、退出重试和 T+1 结算。
- 每个调仓 selection 与完整 market input 均生成独立 Parquet 和 snapshot manifest；正式 bundle
  由 verifier 复读 Parquet、重放 Rust 并重建报告。

## 接口与实现任务

| Task | Owner | Status | Evidence |
|---|---|---|---|
| 冻结评分、选择、等权接口 | Codex | complete | 3 Python tests |
| Rust scheduled target-weight bridge | Codex | complete | 5 engine + 3 runner tests |
| strategy-local cache/PIT 数据装配 | Codex | complete | 1959 raw Parquet + DuckDB catalog |
| 真实半年调仓 E2E 与报告 | Codex | complete | 20 rebalances + identical cache replay hashes |
| 全量验证与文档 | Codex | complete | 142 pytest + Python/Rust/package gates |

## 验收标准

- [x] 手算样本验证 winsorize/z-score/方向/权重、行业 Top5、稳定 tie-break 与精确等权。
- [x] Rust 在 session open 用实时账户 NAV/开盘价/lot size 消费完整 target-weight batch，先卖后买，
  不由 Python 模拟现金、成交或持仓。
- [x] 覆盖十年、20 次半年调仓；退出标的产生零 target，T-close 信号不早于 T+1 open 执行。
- [x] 真实 Tushare 数据落为 Parquet，DuckDB cache-first；第二进程 provider 调用为 0。
- [x] HTML 包含收益/benchmark、回撤、rolling risk、月度热力图、费用、行业/市值暴露及常见指标。
- [x] token 扫描 0 命中；Python/Rust/fmt/lint/type/package gates 全绿。

## Decisions

- 2026-08-12：本次报告用历史沪深300成分而非全 A 股，原因是用户小资金且每行业 Top5 在全 A
  通常超过 140 只，整数手下无法近似等权。选择器与数据接口不硬编码该 universe。
- 2026-08-12：报告使用 Rust target-weight runtime，而不是 Python fractional-share NAV，确保费用、
  T+1、现金和整数手仍由权威账本决定。
- 2026-08-12：真实 `index_member_all` 探针确认 `is_new=N` 返回 0 行；本报告退化为当前 SW2021
  行业映射，报告必须披露该分类幸存者偏差，后续可替换为有授权的历史行业主数据。
- 2026-08-12：十年运行采用周频 NAV/benchmark 观测和 52 期年化；交易执行仍只发生在真实调仓日。
- 2026-08-12：fresh-context 最终审查发现财务修订回灌、静态行业映射实现不一致和失败退出不重试
  三项 High；现已改为原始财报版本、真正静态行业映射和 20 交易日退出重试，旧结果作废。

## Current state

- 完成 2016-08-12 至 2026-08-11 的完整十年周频净值与 20 次真实半年调仓：2335 条入选记录、
  426 只历史入选股票、
  2145 笔成交；退出重试产生 383 次受限拒绝，期末仅余 `601989.SH` 一只连续停牌残留，占 NAV
  0.91%。
- 真实缓存含 1959 个请求对象：`fina_indicator=611`、`daily=446`、`adj_factor=446`、
  `daily_basic/index_weight=20`、`stk_limit=381`，另含日历、行业和基准；selection 的 Top5、逐期
  权重和、原始财报标识与公告截止均
  经 DuckDB 审计通过。
- 最终完整十年运行因扩展基准区间新增 1 次 provider 请求；无效 token 的第二进程复读为 provider
  0；两次 request SHA
  `55202d...2d84`、result SHA `2d4a75...9346`，报告 SHA `195931...a2de` 也完全一致。
- 结果：总收益 81.04%，年化 6.23%，年化波动 13.64%，Sharpe 0.511，最大回撤 -27.43%；
  沪深300价格收益 41.57%，期末现金 21.06%，交易成本 16,813.29 CNY。

## Verification evidence

- Red/Green：评分模块、target-weight constructor、周频/复权 helper、报告 52 期参数、原始财报
  选择、静态行业映射、零权重退出重试及拒单汇总均由失败测试锁定后修复。
- `TUSHARE_TOKEN=<external-file> uv run pytest -q`：142 passed，0 skipped。
- `uv run ruff check python tests`、`uv run mypy`：通过。
- `cargo fmt --check`、`cargo clippy --workspace --all-targets -- -D warnings`、
  `cargo test --workspace`：通过。
- `uv build`、`cargo package --workspace --allow-dirty --no-verify`：通过。
- 仓库 token literal 扫描：0；外部 token 文件权限：0600。
- 最终 20 个 selection snapshot：2335 rows、`financial_update_flag={'0'}`、未来公告 0、Top5 违规 0、
  权重和违规 0；clean deliverable/replay 四个关键文件 SHA 逐字节一致。

## Changed files

- `specs/INDEX.md`
- `specs/20260812-industry-fundamental-top5.md`
- `python/trademaster/strategies/AGENTS.md`
- `python/trademaster/strategies/industry_fundamental_top5/`
- `python/trademaster/e2e/__init__.py`
- `python/trademaster/reporting/__init__.py`
- `crates/tm-engine/src/lib.rs`
- `crates/tm-engine/tests/scheduled_strategy.rs`
- `crates/tm-runner/src/lib.rs`
- `crates/tm-runner/tests/wire.rs`
- `tests/test_industry_fundamental_top5.py`
- `tests/test_fundamental_strategy_data_cache.py`
- `tests/test_reporting_report.py`

## Open questions

- none；本次报告的 universe/capital/date 作为可配置运行参数，不改变策略定义。
