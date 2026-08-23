# M7 因子管理与基本面策略重构

Status: complete
Owner: Codex
Updated: 2026-08-23

> M9审查勘误（2026-08-23）：M7产出的Top1/Top10收益报告不是正式PIT执行证据。
> `_active_industry`曾忽略信号日及`out_date`，560条Top1选择中55条、200条Top10选择中20条
> 使用了晚于信号日的行业`in_date`；连续QFQ价格也不是精确公司行动账本。代码现已默认按
> 历史interval fail closed，并把静态分类隔离成独立实验policy；旧artifact只保留为诊断资料。

## 目标

复盘全行业 Top1 与 Top10 行业 × Top1 两个真实基本面策略暴露的框架问题，先修复会影响正确性、公共接口语义、派生数据身份和复用性的缺陷；随后建设可持久化、可发现、可物化、可评估并绑定 PIT 血缘的因子管理模块，最后把九项基本面策略迁移到该模块并用真实 Tushare 十年数据重新验证。

## 已确认问题

| ID | 类型 | 问题 | 影响 | 处理方向 |
|---|---|---|---|---|
| F-01 | 截面语义 | 公开评分接口会把多个 `signal_time` 一起标准化 | 批量因子化会发生跨时点污染 | 单截面接口多时点fail closed；managed factor按event time分组 |
| F-02 | 交易证据 | 执行日缺limit/status时合成宽松边界并继续 | 缺失A股限制数据仍可能成交 | 正式执行缺必需状态时fail closed |
| F-03 | 派生身份 | 策略变体都写为 `fundamental_selections`，对象无策略/因子definition hash且同schema version承载不同结构 | 无法按语义发现与审计 | 冻结派生schema并绑定策略、因子及输入身份 |
| F-04 | 运行身份 | `strategy_id/run_id`只编码TopN，未编码权重、预处理、universe等 | 不同定义可能拥有相同业务身份 | 规范化完整配置并生成definition/run hash |
| F-05 | 所有权 | 九项指标、方向、预处理和权重由策略私有实现 | 无法独立管理、复用、评估 | 迁移为9个managed factors和2个composites |
| F-06 | 报告时态 | 市值暴露以最后一次入选桶覆盖完整历史 | 历史暴露带未来标签 | 报告支持event-time classification timeline |
| F-07 | 数据职责 | 策略包复制M1的Tushare/Parquet/DuckDB/cache/snapshot职责 | 两套证据语义并存 | 因子层只依赖统一带证据研究数据源；保留现有十年cache并设计迁移适配 |
| F-08 | 血缘 | selection snapshot只绑定最终入选行，不能证明候选全集、九项输入和因子定义 | clean replay只能证明冻结结果可执行 | materialization绑定输入snapshot、definition及输出hash |
| F-09 | 配置/报告 | public config允许替换universe/benchmark/date，但summary、标题和文字仍硬编码沪深300/十年 | 对外报告语义错误 | 报告从冻结run config生成 |
| F-10 | 采集时钟 | 默认把`fetched_at`写成回测end+1而非实际采集时间 | 混淆ingestion time与business availability | 分离wall-clock ingestion、run as-of和row known_at |
| F-11 | 数量语义 | 请求Top-N行业但不足N时静默返回更少行业 | 正式运行悄然降仓 | exact/minimum语义显式配置，默认fail closed |
| F-12 | 执行顺序 | 同一target-weight组合按`signal_id`排序，展示性身份变化会改变小资金买入顺序 | 相同目标组合产生不同整数手账本 | 卖出优先，随后按instrument ID稳定执行，signal ID只作最终tie-break |

## 范围

### 本次实现

- 修复F-01至F-06、F-08至F-12。
- F-07提供统一带证据的研究数据源接口，并让因子层不依赖策略私有cache；不删除或重新下载现有十年cache。
- 因子定义、注册、依赖DAG、代码/参数哈希、Parquet物化、DuckDB目录、PIT血缘、基础评估和CLI/API。
- 九项基本面原子因子、行业内复合分和全市场复合分；策略只保留行业筛选、TopN与调仓规则。
- 重跑全行业Top1和Top10行业×Top1，比较经济输出。

### 非目标

- 不引入机器学习训练、分布式调度或实时因子服务。
- 不解决Tushare缺少历史SW2021行业修订的问题；继续披露静态分类限制。
- 不在本阶段实现完整公司行动账本；复权执行近似继续披露。
- 退出重试下沉Rust另立执行里程碑，本阶段保持既有幂等target=0经济语义。

## 公共接口草案

- `FactorDefinition`：稳定ID/版本、family、description、dependencies、parameters、lookback、scope、implementation SHA和definition SHA。
- `ManagedFactorRegistry`：按identity/definition SHA发现，校验重复、未知依赖和DAG环。
- `FactorManager.materialize()/load()/query_values()`：输入已绑定snapshot的Arrow表；相同definition/input/universe/event range命中Parquet，否则计算、校验、原子写入并登记DuckDB。
- `FactorMaterialization`：绑定definition SHA、input snapshot ID/object hashes、output SHA/schema/rows/range/universe和created_at。
- `FactorEvaluator`：至少输出coverage、Pearson IC、RankIC、IC均值/标准差/ICIR、分层收益、long-short spread、turnover和因子相关性。
- `FactorCatalog`：列出definitions/materializations/evaluations；Parquet是值的权威存储，DuckDB只作目录和查询层。
- CLI：`factor list`、`factor describe`、`factor materialize`、`factor evaluate`。

## 实现里程碑

| Milestone | Owner | Status | Evidence |
|---|---|---|---|
| M7.1 策略复盘、问题复现与公共语义修复 | Codex | complete | focused regressions + F-01..F-12 closure |
| M7.2 因子定义、registry、DAG与身份 | Codex | complete | definition/parameter/code hash + graph tests |
| M7.3 Parquet/DuckDB物化、缓存与PIT血缘 | Codex | complete | real temporary Parquet/DuckDB + bundle v3 |
| M7.4 因子评估与CLI | Codex | complete | hand-calculated evaluation + CLI smoke |
| M7.5 九项基本面迁移与策略瘦身 | Codex | complete | Top1 560 + Top10 200 exact selection rows |
| M7.6 真实十年E2E、clean replay、报告与bounded review | Codex | complete | provider0 + exact replay/equivalence + review PASS |

## 验收标准

- [x] 已确认的框架问题均有修复或明确保留理由，不以重命名掩盖语义缺陷。
- [x] 因子定义和物化身份由规范化内容哈希决定，代码或参数变化产生不同definition。
- [x] 因子值以Parquet持久化；DuckDB可发现但不是权威值存储；损坏或血缘不一致fail closed。
- [x] materialization完整绑定输入snapshot、definition、universe、事件范围和输出hash。
- [x] evaluator在手算样本上产生正确IC/RankIC/分层收益/换手等指标。
- [x] 九项基本面指标不再由策略模块硬编码计算；策略通过因子管理API获得原子与复合分数。
- [x] 两种基本面策略重构后的20个截面与基线经济语义一致；若不同必须定位并解释。
- [x] 真实十年E2E与无效token clean replay通过，provider 0，Rust权威执行与HTML报告完整。
- [x] Python/Rust/package gates通过，并完成一次范围受限fresh-context review。
- [x] 根架构、SVG、模块AGENTS、策略README、因子存储/CLI/E2E文档与当前公共接口同步。

## Decisions

- 因子计算、管理和评估继续由Python负责；Rust只消费最终精确信号并负责交易、规则和账本。
- 用户已明确策略研究代码不需要形式化TDD；真实E2E是迁移策略的主要验收。公共契约、正确性修复和因子管理基础设施仍使用TDD。
- 保留当前真实十年cache作为迁移输入，禁止为了接口统一而无条件重新下载。
- 旧selection的内容语义必须逐行保持；因修复target-weight顺序导致旧持久结果变化时，以“旧request由当前稳定runtime重放”作为正确经济基线，不能保留signal-ID依赖来追旧SHA。
- `ResearchDataSource`统一返回命中的不可变Parquet证据；现有十年strategy cache作为兼容adapter保留，因子管理层只依赖候选input snapshot，不直接依赖该cache实现。

## Current state

- F-01至F-12已实现定点修复；初次bounded review发现的status fallback、虚假parent DAG和标签对齐声明三项High经同一reviewer定点复核均关闭，最终PASS。
- 当前factor root有11个definitions、220个materializations和220个factor Parquet；catalog definitions与当前源码重新构建hash完全一致。
- Top1最终产物：20期/560行selection与旧结果逐行exact；旧request由当前runtime重放后，与新result的NAV/fills/rejections/final positions经济投影exact。总收益108.3896%、期末NAV 1,041,947.75675909 CNY。
- Top10行业×Top1最终产物：20期/200行selection逐行exact；修复后经济投影exact。总收益47.1649%、期末NAV 735,824.26104058 CNY。
- 两个variant的final/replay均各540个文件逐字节一致、provider 0；bundle v3各含41 snapshots、11 definitions、220 materializations、220 factor objects及summary/Markdown绑定。
- 代码与文档已同步：稳定策略入口、因子管理说明、根/模块AGENTS、架构overview和可编辑SVG均已更新；SVG结构校验和4000px完整图/局部裁图视觉复核通过。

## Changed files

- `specs/INDEX.md`
- `specs/20260823-m7-factor-management-strategy-refactor.md`
- `AGENTS.md`
- `docs/factor-management.md`
- `docs/architecture/overview.md`
- `docs/architecture/trademaster-v1-architecture.svg`
- `python/trademaster/data/research.py`
- `python/trademaster/data/__init__.py`
- `python/trademaster/factors/`
- `python/trademaster/e2e/__init__.py`
- `python/trademaster/e2e/real_tushare.py`
- `python/trademaster/strategies/AGENTS.md`
- `python/trademaster/strategies/industry_fundamental/`
- `python/trademaster/strategies/industry_fundamental_top5/`
- `crates/tm-engine/src/lib.rs`
- `crates/tm-engine/tests/scheduled_strategy.rs`
- `tests/test_factor_management.py`
- `tests/test_factor_evaluation.py`
- `tests/test_factor_cli.py`
- `tests/test_fundamental_managed_factors.py`
- `tests/test_industry_fundamental_top5.py`
- `tests/test_fundamental_strategy_data_cache.py`
- `tests/test_e2e_orchestrator.py`
- `tests/test_real_tushare_e2e.py`
- `pyproject.toml`
- `artifacts/strategies/industry-fundamental-managed-top1-final-v2{,-replay}/`
- `artifacts/strategies/industry-fundamental-managed-top10-industries-top1-final-v2{,-replay}/`

## Verification evidence

- Red/Green：mixed signal time、缺limit、行业数不足、definition/DAG、materialization cache/corruption、parent value forgery、evaluation timing/alignment、target-weight signal-ID顺序均有失败复现及定向回归。
- 真实token横截面股票+ETF E2E：`2 passed`；ETF缺status且无显式limit rule单测fail closed。
- Python最终全量（真实token）`164 passed`，0 skip；真实股票横截面+ETF网络E2E `2 passed`。
- Ruff、strict mypy（55 source files）、Cargo fmt/Clippy/workspace tests/doc tests通过；wheel/sdist和Cargo workspace package通过。
- token literal扫描0命中；外部token文件权限0600。
- 因子CLI：`tm-factor list`返回11个definitions，describe/graph smoke通过；factor evaluation手算IC/RankIC/分层/spread/turnover通过并可持久化。
- 文档：本地链接0缺失，策略/因子CLI help命令与README参数一致；SVG validate/rsvg通过并完成完整图及title/research/output/legend裁图视觉检查。
- wheel/sdist重新构建；全新临时venv安装wheel后`tm-factor list`返回11个definitions，`trademaster.factors`与稳定策略包均可隔离import。
- bounded review初检3 High；定点修复后同一reviewer复核PASS，无Critical/High残留。

## Open questions

- none；默认保持两种策略的经济口径与真实缓存不变。
