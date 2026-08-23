# M9 二十年全A因子研究与代码熵减

Status: complete
Owner: Codex
Updated: 2026-08-23

## 目标

下载并内容寻址保存2006-08-23至最新已完成交易日的完整A股因子研究数据，基于所有曾上市/
退市的沪深京CNY股票构造无前视因子暴露和forward-return标签，补齐全A口径的因子质量指标
与可阅读报告。
数据与测评完成后，开启多个fresh-context subagent，从系统架构、工程实现、因子管理和策略
实现等维度做一次定期熵减审核，修复有明确证据的设计或正确性问题。

## 范围

“完整全A”指因子研究所需的数据闭包，而不是Tushare全站所有产品：

- `stock_basic`全部L/D/P/G状态，保留上市与退市日期，构造无幸存者偏差历史universe；
- SSE/SZSE交易日历；Tushare当前不返回BSE独立日历，北交所上市后使用明确版本化的SSE
  session映射，并在coverage中标记为derived而非provider原始事实；
- 全部股票日线OHLCV/amount/pre_close与同日`adj_factor`；
- `daily_basic`的turnover、总/流通市值、PE/PB/PS和股息率；
- `stk_limit`、`suspend_d`、`stock_st`在数据源可提供范围内的交易状态；
- `fina_indicator`及构造已管理基本面因子所需的公告日/报告期/修订字段；
- 中证全指、沪深300、上证50、科创50等研究基准；
- SW2021行业成员的可获得历史。无法覆盖2006年以来历史行业时，行业指标明确标记coverage，
  不用当前静态行业回填历史。

非目标：新闻、研报文本、逐笔/LOB、期权期货、分析师一致预期和当前无许可的专有资金流。

## 时间与数据身份

- 请求起点固定`2006-08-23`。
- 终点由Tushare交易日历解析为运行时最新已完成交易日，写入不可变run manifest。
- 数据根默认`data/research/full-a-20y`，可配置覆盖。
- Tushare token只从环境读取；本地token文件只在shell进程加载，不进入日志、manifest或Parquet。
- 原始响应和派生结果均使用Parquet；DuckDB只做请求、覆盖、任务状态和查询发现。
- 下载计划稳定、有内容hash、可恢复；已验证请求不重复调用provider，失败从第一条未完成任务继续。

## 覆盖不变量

- 历史universe按`list_date <= session <= delist_date`解析，禁止用当前上市股票回填历史。
- 日线允许因停牌缺行，但必须由停牌/交易状态证据解释；不把停牌日压缩成相邻观测。
- 日线与复权因子对每个可交易观测同日连接；缺复权因子的记录进入明确缺口表。
- `daily_basic`等稀疏数据按endpoint真实语义验证，不要求虚假的笛卡尔矩阵。
- 财务因子只使用`ann_date/known_at <= factor event`的当时可知版本；后来修订不回填历史。
- 下载成功、权限不足、数据源历史不存在和真实缺口是不同终态。

## 因子与标签

- 首批覆盖全部精确可执行公开因子：华泰Size与10条低歧义GTJA。
- 对现有7条`implemented_variant`和其他基本面atomic因子，在typed PIT输入完成后单独测评，
  报告中不得与华泰原因子混为一谈。
- GTJA使用definition绑定的HFQ：`raw_price * adj_factor`；执行行情仍为原始价格。
- close-derived因子T日收盘后形成，forward return最早从T+1 eligible open/close开始。
- 默认评估horizon为1、5、20、60、120、252个交易session；具体配置进入evaluation identity。

## 测评输出

保留现有coverage、Pearson IC、RankIC、ICIR、RankICIR、正IC比例、分位数组收益、Top-Bottom
spread和Top组换手率，并扩展：

- 有效日期比例、截面样本数分布、缺失/无穷/常数截面比例；
- IC t-stat、Newey-West t-stat、IC偏度/峰度、年度/市场阶段稳定性；
- 分位数单调性、spread胜率/波动/Sharpe、累计spread、最大回撤与修复时间；
- 行业内RankIC、行业暴露、Size相关性/市值bucket暴露；
- 按上市板块、ST/停牌/涨跌停、上市年龄、市值分组的稳健性；
- quantile换手、可交易覆盖、成交额参与率/容量proxy和成本敏感性；
- raw、行业中性、市值中性三种口径，只有历史分类/PIT证据足够时才输出对应结果。

输出为不可变Parquet/JSON和自包含HTML/Markdown报告；每项绑定factor definition、数据snapshot、
universe policy、label alignment和完整配置hash。

## 公共接口草案

- `FullAResearchConfig`：日期、数据根、endpoint集合、批次、限流、重试、universe与评估配置。
- `DownloadTask` / `DownloadPlanManifest`：稳定任务identity、endpoint、参数、预期覆盖和状态。
- `FullAResearchDownloader.plan/status/run/verify`：计划、恢复、下载和覆盖审计。
- `FactorResearchPanelBuilder`：raw cache到PIT universe、HFQ market panel、fundamental panel和labels。
- `ExtendedFactorEvaluator`或evaluation v2：扩展统计量和分组诊断，不破坏v1 artifact加载。
- CLI：`tm-research-full-a plan|download|status|verify|evaluate|report`。

## Milestones

| Milestone | Owner | Status | Evidence |
|---|---|---|---|
| M9.1 数据范围、计划、状态与覆盖合同 | Codex | completed | 5,843 instruments / 4,860 sessions / 52,551 tasks |
| M9.2 cache-first下载器与CLI | Codex | completed | 8 focused tests + bootstrap cache probe 0 calls |
| M9.3 真实20年全A下载与coverage audit | Codex | completed | 52,563/52,563 + 0-call probe + full verify |
| M9.4 全A因子panel、labels和扩展测评 | Codex | completed | public 11 + fundamental 10 real evaluation |
| M9.5 全A因子报告 | Codex | completed | two verified standalone reports |
| M9.6 多Agent代码熵减审核 | Fresh reviewers | completed | 3 reviews + 28-item disposition |
| M9.7 明确问题修复与全局门禁 | Codex | completed | v2 panels/v3 reports + 248 Python + Rust/package gates |

## 验收标准

- [x] 20年历史universe包含曾上市和已退市A股，起止日与终点解析可复现。
- [x] 所有必要endpoint都有确定性下载计划、恢复状态、内容hash和coverage报告。
- [x] 日线/复权/每日指标/财务/状态/基准/行业的完成、已验证空对象和缺口分别可查询。
- [x] 真实下载完成后cache probe的provider调用为0。
- [x] 所有可执行因子产生全A评估artifact，行业因子有机器可读blocker。
- [x] v2测评覆盖收益、稳定性、Size中性、行业/市值暴露、换手和容量；需Rust的执行状态维度显式blocked。
- [x] 报告明确无幸存者偏差策略、PIT边界、行业历史缺口和微盘/成本敏感性。
- [x] 至少3个fresh-context subagent分别完成架构、工程质量、因子/策略审核。
- [x] 每条review comment有accept/reject/defer及证据；accepted项实现并回归验证。
- [x] Python/Rust/package/real-cache/E2E门禁通过，代码与AGENTS/docs/spec同步。

## Decisions

- `daily/adj_factor/daily_basic`与状态均按交易日拉全市场截面；这与Tushare全市场建议一致，
  也使后续只新增交易日task而不因终点变化重拉每只股票的整段历史。财务报表按股票长区间拉取。
- 覆盖证明优先于“请求成功”；当前source历史不足时保持blocked而非造数。
- 先完成数据和评估，再启动用户要求的多维fresh review；review仅一轮，避免无止境comment循环。
- 评估不是策略收益承诺；全A因子质量与此前446只工程cache结果严格分开。

## Current state

- M8已有460条candidate、22条managed definitions，其中11条公开原因子可执行。
- 当前十年策略cache日线为446只历史入选并集，不是全A；不可复用为本里程碑的覆盖结论。
- 三路fresh-context单轮审核已经完成。审查前public/fundamental报告因发现PIT、因子窗口和
  evaluation统计口径问题，暂时只作为诊断产物，不作为最终研究结论；正在TDD修复并准备重建。

## TDD evidence

- Red（2026-08-23）：`uv run pytest -q tests/test_full_a_research.py`在收集阶段因
  `trademaster.research`尚不存在而失败。测试先冻结了无幸存者偏差universe、确定性任务计划、
  内容hash、DuckDB状态恢复、完成后零source调用和损坏plan fail-closed合同。
- Green（2026-08-23）：focused research planner/store/downloader/CLI为`8 passed`，Ruff与
  strict Mypy通过；source evidence必须存在且重新核验Parquet hash/row/fields，完成plan支持
  全对象verify。
- Evaluation v2 Red（2026-08-23）：`tests/test_factor_evaluation_v2.py`因新模块尚不存在而
  在收集阶段失败。测试先冻结next-session close与精确horizon标签、不可交易entry显式原因、
  PIT universe分母、factor/label/joint coverage、IC/RankIC、HAC、分位单调性、spread、
  Size相关性、容量proxy及标签全键fail-closed。
- Evaluation v2 Green（2026-08-23）：4个手算测试通过；实现dense session标签、明确invalid
  reason、PIT-universe denominator、IC/RankIC/普通与Newey-West t-stat、quantile/spread/
  turnover/drawdown、行业内RankIC、log-size相关性和1% amount容量proxy。该实现是小表
  correctness oracle，真实20年计算将由DuckDB分片执行，禁止全量`to_pylist()`。
- Report v2 Green（2026-08-23）：报告store与evaluation相邻回归`11 passed`，Ruff/Mypy/
  compileall通过；内容寻址落盘summary/event/quantile/correlation/HTML/Markdown与manifest，
  非有限值转null/N/A，自包含Plotly、对象篡改和manifest篡改均fail closed。
- Panel Green（2026-08-23）：新增DuckDB/Arrow batch全链路面板构建器；focused+相邻
  `20 passed`，全Python当时为`195 passed, 2 credential skips`，Ruff/strict Mypy通过。
  它从完成task证据构造PIT生命周期×session稠密面板，BSE起点clamp到2021-11-15，保留
  停牌/缺bar行，在完整session窗口计算11个因子后再抽月末观察，并生成1/5/20/60/120/252
  labels；manifest绑定source/definition/config/code/output hash。
- Streaming evaluator Green（2026-08-23）：新增DuckDB out-of-core evaluator，与内存oracle
  做固定fixture和10个随机seed parity；相关回归`17 passed`，Ruff/Mypy/compileall通过。
  全键/重复/越界校验、event IC/RankIC/quantile/行业/Size/turnover/capacity与所有factor pair
  correlation均在DuckDB完成，Python只读取event级聚合表。
- Fundamental PIT Green（2026-08-23）：新增全A月末基本面构建器；focused+adjacent
  `18 passed`，全Python当时`205 passed, 2 credential skips`，Ruff/Mypy通过。它从
  `ann_date<=event`且`end_date<=event`的修订中确定性选当时版本，生成现有9 atomic和global
  composite，并与managed实现做1e-12 parity；行业区间不完整时输出机器可读blocker而不静态
  回填。未来公告值扰动不改变历史暴露。
- Entropy-reduction Red/Green（2026-08-23）：report identity新增输入manifest绑定的Red为
  `4 failed`，Green为`4 passed`；legacy cache路径逃逸Red为`6 failed, 1 passed`，加入稳定
  identifier和root边界后Green为`8 passed`；历史行业映射Red为`2 failed`，按`in_date/out_date`
  interval解析后相关测试`16 passed`；`.BJ` venue映射Red在收集阶段失败，Green为`1 passed`。
- Download identity/lease Red（2026-08-23）：focused `6 failed, 9 passed`，旧实现会接受错误
  request evidence/metadata，且没有原子claim、lease、typed blocker和acquisition outcome；Green
  为focused+panel+fundamental `24 passed`，双connection探针只有一个worker获得task。
- PIT/finite/parity Red（2026-08-23）：3个失败分别证明NaN adj未计gap、缺公告known-at session、
  未知修订会改变历史值；Green focused `8 passed`，相邻full-a/panel/fundamental/evaluator
  `40 passed`，11因子含null/停牌session逐行Python↔DuckDB一致。
- Provider classification Red（2026-08-23）：新typed异常在收集期缺失，网络分类另有1个失败；
  Green focused `11 passed`、相邻full-a+client `27 passed`。权限/积分不足立即blocked，认证、
  invalid和unknown不重试，只有明确transient错误做有界退避，包装错误不回显provider原文。
- Evaluation entropy Red/Green（2026-08-23）：direction配置最初被`extra_forbidden`；60-session
  重叠spread旧值为`0.01833`；pooled pair Pearson为`0.4186`而逐event期望为0；capacity重复
  乘1000。Green后quantile先按ex-ante pool冻结，逐horizon coverage/invalid reason、definition
  direction、CNY容量、event-level相关和重叠N/A在Oracle/DuckDB一致，focused+report
  `20 passed`。
- Size diagnostics Red/Green（2026-08-23）：先缺`size_neutral_rank_ic`，随后DuckDB旧13列与
  新17列schema不符；Green新增截面size残差RankIC与PIT市值tercile Top暴露，相关门禁
  `23 passed`。最后用真实dense panel最小观察间隔而非252/12平均值判overlap；spacing=18
  fixture证明h5保留spread、h20为N/A，最终evaluator/streaming/report `26 passed`。
- Reconcile idempotence Red/Green（2026-08-23）：真实目标plan证据重算得到同一SHA时，旧CLI
  仍调用cross-plan inheritance并报`download plan inheritance requires different plans`；新增
  same-plan幂等注册测试Red为import缺失、Green `1 passed`。原始真实命令复跑得到相同plan、
  `inherited_completed_tasks=0`、`provider_calls=0`和52,563 completed。

## Fresh-review disposition

三名reviewer均为一次fresh-context只读审查；相同根因合并处置，避免重复审查循环。
逐条原始finding处置见`docs/reviews/20260823-m9-full-a-quality-review.md`。

| Finding | Severity | Disposition | Current action |
|---|---:|---|---|
| 下载task/request/evidence/Parquet未强绑定 | High | accept/complete | canonical request、metadata、typed snapshot与回归已完成 |
| 财务公告日同日可用、未知修订回填历史 | High | accept/complete | 下一eligible session + original-only policy；真实重建中 |
| managed Python与稠密session SQL窗口不一致 | High | accept/complete | 11因子dense-session全公式parity gate已完成 |
| 非有限价格/复权/forward return仍可valid | High | accept/complete | 全链`isfinite`不变量与NaN/Inf回归已完成 |
| quantile依赖未来label可用性 | High | accept/complete | ex-ante分组、逐horizon coverage/原因已完成 |
| 重叠horizon被独立复利，DD/Sharpe失真 | High | accept/complete | 用真实最小观察间隔判定；重叠spread统计N/A |
| capacity重复乘1000、direction未应用 | High/Medium | accept/complete | CNY单位golden、definition方向与config identity已完成 |
| report未绑定panel/fundamental manifest | Medium | accept | report identity v3已绑定输入manifest；真实重建待执行 |
| fundamental report无正式CLI | Medium | accept | `evaluate --factor-manifest-kind fundamental`已加入；E2E待跑 |
| legacy cache路径逃逸 | Medium | accept | stable identifier/root containment已修复并验证 |
| legacy行业映射静态回填、BJ错映射 | High/Medium | accept/complete | PIT interval/BSE已修复；正式probe因真实gap fail closed，旧结论撤回 |
| legacy行情装配近二次扫描 | Medium | accept | 改为单次分组，回归与性能核对待执行 |
| provider-neutral store仍由compat策略包承载 | Medium | accept/defer | protocol已下沉`trademaster.data`；store迁移留后续兼容里程碑 |
| 原始价公司行动/现金分红尚无Rust事件合同 | High | accept/defer | 旧报告明确标为连续总收益近似；需独立引擎合同/TDD，不在M9伪修 |
| 港美股market policy与publication watermark | Medium | accept/defer | 记录为跨市场扩展里程碑；M9固定A股policy |
| 市值中性/bucket/状态稳健性仍不完整 | Medium | accept/partial | Size中性RankIC和Top市值tercile已完成；ST/板块/年龄/限价分组保留schema+Rust blocker |

正式Top1 PIT probe在`2016-11-07 / 000009.SZ`遇到历史行业interval gap并在selection/Rust执行前
fail closed；阻塞证据见`artifacts/strategies/m9-pit-audit-top1-blocked-probe/BLOCKED.md`。因此旧
Top1/Top10收益artifact不重写为“修复后报告”，而是保留审查追溯并明确撤回结论。

## Real download evidence

- 最终`plan_sha256=6ce15c56c759a79f635af3ac489bbeb624620670e4f4cd323eb80935ed17a633`。
  初版plan因把2006年尚无源历史的`stk_limit`误设为必须非空而fail closed；v2将空日保留为
  source-history coverage gap，并逐对象重验后继承14,586个相同task hash的已完成证据。
- market identity reconcile又发现3个有daily但当前stock_basic无记录的历史代码，以其首末market
  session形成显式inferred lifecycle；最终universe 5,846只、52,563 tasks，并逐对象重验继承
  29,166个market/status/bootstrap完成任务。最终market+status cache probe provider调用为0。
- Tushare bootstrap：L=5,549、D=339、P=0、G=0，共5,888个stock_basic身份；与区间重叠且
  为CNY沪深京股票的计划universe为5,843只，排除45只区间前已退市标的。
- SSE/SZSE日历一致，2006-08-23至2026-08-21共4,860个open sessions；BSE独立日历接口
  返回0行，按spec使用版本化SSE-derived session policy。
- 计划共52,551个tasks；bootstrap生成时8次真实provider调用（两份长日历各2页），随后
  `download --phase bootstrap`完成6个任务且provider调用为0。
- market阶段已完成：总completed=14,586（含bootstrap 6），failed=0；同阶段cache probe
  `provider_calls=0`。
- 业务键审计：daily 15,461,567行/5,838代码，adj_factor 16,204,207行/5,842代码，
  daily_basic 15,370,739行/5,838代码；三者均覆盖4,860日、2006-08-23至2026-08-21，
  `ts_code+trade_date`重复键均为0。
- `daily ANTI adj_factor=24`，全部是600018.SH在其stock_basic正式上市日之前的2006-08-23
  至09-25旧序列，PIT active grid会排除；`daily ANTI daily_basic=90,829`，其中绝大多数
  是北交/新三板历史段，另有13条深圳记录；panel按BSE 2021-11-15起点并显式报告剩余缺口。
- stock_basic计划与daily存在身份差异：3个历史代码有daily但当前stock_basic无记录；8个
  计划旧退市/特殊代码在Tushare daily无历史。它们进入universe reconciliation/source-history
  blocker，不把请求成功当作完整覆盖。

## Real panel and public-factor evaluation

- 修复后v2 public panel manifest：
  `9f163dd4c7b3fe958c4beb363303e767dbe40c00523e5d2a928ef24feb1d4c89`；
  materialization：`2279319feb5a1bd9864407524bcab73051fe98097c82f09501189de28e2ef9b0`。
- Dense PIT rows 15,882,389；5,846 instruments；789,359 month-end observation keys；
  8,682,949 factor rows（valid 8,154,780）；4,736,154 labels（valid 4,354,957）。
- active bar rows 15,370,049；active bar↔adj gap=0；bar↔daily_basic gap=20；显式停牌
  473,924；依据同日adj连续性推断停牌52,523；仍无法解释的missing bar 4,963。
- 修复后公开11因子report ID：
  `413067aee36089e6f3295c5165e1f069217a71c481436295d8411512ea29b927`；manifest
  `5d0a8013a0e2086e6dcb10af1ee84499c75034048f75a7bcb6ed82eb109407f6`；
  生成15,562条event metrics、77,810条quantile metrics和55条factor pair correlations。
- 代表性20-session raw/Size中性RankIC：Alpha046 `0.0546/0.0562`、Alpha034
  `0.0450/0.0466`、Alpha088 `-0.0690/-0.0675`、Size `-0.0668/0.0074`。Size的
  raw效果几乎完全由市值暴露解释；direction=-1后Top全部位于small tercile，容量proxy约
  475万元，Top换手约0.11。技术因子Top换手约0.74–0.81。
- 逐event冗余诊断：Alpha031/034 Rank相关`-1.0000`、Alpha034/046 `0.9583`、
  Alpha058/088 `0.5575`；`observation_count=240`表示有效月份，不再是混池股票行数。
- 真实月末最小间隔14 sessions；20/60/120/252 horizon均标记overlap，spread累计、Sharpe、
  drawdown和recovery为N/A。1/5-session保留非重叠spread诊断。
- report manifest已fresh verify；6个对象全部hash/schema/rows通过，离线HTML约5.8MB。

## Complete data and fundamental evaluation

- 最终download状态：52,563 completed，其中52,439 object-acquired、124 verified-empty，
  0 pending/running/failed/blocked；真实token完整plan cache probe provider调用为0，随后按
  task/evidence/Parquet request SHA和对象hash/metadata/row做全量verify。
- 数据根`data/research/full-a-20y`约2.7GB；财务表：balancesheet 436,266行/5,842股，
  cashflow 388,256行/5,841股，fina_indicator 563,732行/5,845股，income
  383,254行/5,842股；四类ann_date缺失均为0。
- 修复后基本面manifest：`2f71fc7d9e081d7368ddbd0af8d0adf076b39e6a24ae03c9618afa29a1f2b2a2`；
  materialization `5acb27c7e6fe0157763a42e6878d28f09cf5fde6eb90292fab77f7e57b0209e9`；
  7,893,590 factor rows，9 atomic+global materialized，industry composite仍因15,013个
  interval gap blocked。
- 基本面report ID：`eddc6bef5097db84e1cb7d337d401bf241ec478536a0bdcf208aa0d52fb9a761`；
  manifest `93c82636da316a441ef67743e8966cee1ff7b578f575614a410f6334733542d2`。
  20-session raw/Size中性RankIC：book yield `0.0555/0.0590`、dividend
  `0.0382/0.0499`、earnings `0.0335/0.0578`、global `0.0279/0.0459`。
- 下一session公告可见性与original-only revision policy使有财务输入观察降至776,656；
  global composite有613,223条共同有效值相对审查前改变，其raw RankIC由0.0315降至0.0279。
  有效基本面Top换手约0.04–0.16，明显低于价量因子。

## Final verification

- 真实token full-plan cache probe：`provider_calls=0`；status v2为52,563 completed、
  52,439 object-acquired、124 verified-empty、其余终态0。
- `tm-research-full-a verify`：52,563个对象的request identity、Parquet metadata、对象hash和
  行数全部通过。
- 真实`reconcile-universe`：同一plan幂等返回、`inherited_completed_tasks=0`、provider 0。
- public/fundamental report分别由安装后wheel的`verify-report`重验6个对象与输入manifest通过。
- Python：加载真实token的`uv run pytest -q`为`248 passed in 95.97s`。
- Python static：全仓Ruff check PASS；M9范围27文件format check PASS；strict Mypy
  `44 source files` PASS；compileall PASS。全仓另有27个本次范围外的历史格式差异，未批量改动。
- Rust：`cargo fmt --check`、workspace Clippy `-D warnings`、workspace tests全部PASS。
- Package：`uv build`生成sdist/wheel；独立venv解析28个依赖后，安装wheel的status、
  reconcile和两份report verify均PASS。
- Markdown：8个本次文档/索引的fence与相对链接静态检查PASS。

## Changed files

- `specs/INDEX.md`
- `specs/20260823-m9-full-a-20y-factor-evaluation-quality.md`
- `python/trademaster/research/AGENTS.md`
- `python/trademaster/research/__init__.py`
- `python/trademaster/research/__main__.py`
- `python/trademaster/research/full_a.py`
- `python/trademaster/research/factor_evaluation.py`
- `python/trademaster/research/factor_evaluation_duckdb.py`
- `python/trademaster/research/factor_report.py`
- `python/trademaster/research/full_a_panel.py`
- `python/trademaster/research/full_a_fundamental.py`
- `python/trademaster/research/tushare.py`
- `python/trademaster/data/research.py`
- `python/trademaster/strategies/industry_fundamental_top5/AGENTS.md`
- `python/trademaster/strategies/industry_fundamental_top5/README.md`
- `python/trademaster/strategies/industry_fundamental_top5/__main__.py`
- `python/trademaster/strategies/industry_fundamental_top5/data.py`
- `python/trademaster/strategies/industry_fundamental_top5/real.py`
- `tests/test_full_a_research.py`
- `tests/test_factor_evaluation_v2.py`
- `tests/test_factor_evaluation_duckdb.py`
- `tests/test_factor_report_v2.py`
- `tests/test_full_a_panel.py`
- `tests/test_full_a_fundamental.py`
- `tests/test_research_tushare_client.py`
- `tests/test_fundamental_strategy_data_cache.py`
- `tests/test_industry_fundamental_top5.py`
- `docs/full-a-factor-research.md`
- `docs/factor-management.md`
- `docs/reviews/20260823-m9-full-a-quality-review.md`
- `artifacts/strategies/REVIEW_NOTICE.md`
- `artifacts/strategies/m9-pit-audit-top1-blocked-probe/BLOCKED.md`
- `artifacts/research/full-a-20y/FINAL_REPORT.md`
- `pyproject.toml`

## Deferred follow-ups

- 将compat `StrategyDataCache`实现迁入provider-neutral data层，同时保留旧cache adapter。
- 为港股/美股增加带timezone、publication watermark、currency/unit和symbol codec的
  `MarketResearchPolicy`。
- 扩展evaluation input，补ST/板块/上市年龄/涨跌停分组稳健性表。
- 为Rust增加公司行动、拆并股和现金分红事件后，重建精确基本面策略执行收益。
