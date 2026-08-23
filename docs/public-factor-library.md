# A股公开因子目录与可构造性

## 1. 结论

TradeMaster已经把本轮逐项核对的华泰系列与国泰君安Alpha191纳入因子治理系统。这里的
“入库”分成两层：

- `FactorCandidateRecord`保存来源、公式引用、输入依赖、数据可用性、语义阻塞、许可边界和
  本地definition映射；候选可以是blocked，不能被回测引擎直接执行。
- `FactorDefinition`只接收已经独立实现并通过本地golden case的因子；只有这一层可以物化为
  Parquet因子值。

当前内置目录有8条来源记录、7个collection、460条candidate；其中新增11条可执行definition：

- `huatai53.size.log_total_market_value@1`；
- `gtja191.alpha{014,015,018,020,031,034,046,053,058,088}@1`。

原有7条基本面definition与华泰53概念接近（其中5条已有当前cache输入，另2条是单季度
成长口径），但并不完全等同，因此只登记为`implemented_variant`，没有改名冒充华泰原因子。

## 2. 完整collection

| Collection | 条数 | 当前处理 |
|---|---:|---|
| 华泰2020九类风格因子 | 53 | 逐条记录公式、依赖和14/36/3数据层级 |
| 华泰资金流向 | 50 | 缺Wind订单大小、主动方向和日内分段字段，`blocked_data` |
| 华泰财务质量 | 51 | 已登记；等待typed财报行项目、公告日和修订PIT合同 |
| 华泰一致预期 | 19 | 缺有许可的历史一致预期快照，`blocked_data` |
| 华泰历史分位数 | 86 | 可见参数表是86条，同时保留研报摘要`reported_count=89` |
| 华泰结构化风险模型 | 10 | `risk_model_only`，不伪装成预测Alpha |
| 国泰君安Alpha191 | 191 | 编号1–191连续；逐条登记DolphinDB输入表和语义审查状态 |

华泰“海量技术101”是WorldQuant Alpha101在A股上的复验，不作为一套不同公式重复计数。

## 3. 华泰53的数据盘点

| 数据层级 | 数量 | 因子 |
|---|---:|---|
| 当前十年策略cache已有输入 | 14 | EP、BP、DP、ROE_q、grossprofitmargin_q、size、4个return、4个std |
| Tushare可扩展，但要补canonical字段/PIT派生 | 36 | 其余估值/成长/质量/杠杆、8个换手加权收益、4个换手率 |
| 先补基准与回归语义合同 | 3 | beta、HAlpha、resvol |

“当前cache已有输入”不表示已有无偏全A研究样本。现有十年日线是过去策略入选标的的
446只并集；基本面cache只有20个半年截面。它可以做工程和数值验证，不能用来声称全A
因子IC结论。

几个容易误接的口径已经显式记录：

- 华泰`EP=1/pe_ttm`没有“只保留正PE”的条件；本地`fundamental.earnings_yield@1`
  会过滤负PE，所以只是variant。
- Tushare `dv_ttm`是百分数；华泰DP raw exposure按ratio使用时要除以100。
- 本地`q_sales_yoy/q_profit_yoy`是单季度同比；华泰`Sales_G_q/Profit_G_q`是最新披露
  YTD同比。
- 华泰`operationcashflowratio`是经营现金流/净利润；本地`ocf_to_or`是经营现金流/
  营业收入，不能互换。
- 本地`debt_to_assets`不等于华泰53的五项杠杆定义。
- 华泰报告级复现还需要历史中信行业；当前最新SW2021静态成员映射只能用于明确标注的
  工程近似。

## 4. GTJA191的数据盘点

对Alpha001–Alpha191逐条核对DolphinDB固定提交的入参表后，字段层结果为：

| 数据层级 | 数量 | 说明 |
|---|---:|---|
| 当前OHLCV和复权因子可构造 | 146 | 原始字段已经在十年cache中，但股票池只有446只 |
| 当前数据可派生VWAP | 40 | `vwap_cny_per_share = amount * 10 / volume`，并与OHLC使用同一复权尺度 |
| 需要冻结指数基准 | 4 | Alpha075、149、181、182 |
| 缺正式因子收益输入 | 1 | Alpha030还需要MKT、SMB、HML；参考README的简表漏了这三个参数 |

字段可构造不等于公式已实现。除首批10条外，其余candidate保持
`blocked_semantics`，直到具备原文AST、批准勘误、版本化算子语义和golden case。
高风险项包括Alpha021的回归对齐、027的WMA、030的FF3、143的递归`SELF`、
165/183的`SUMAC`与括号、181的量纲/括号以及190的PDF排版破损。

首批10条只用open/close，避开RANK、SMA、VWAP、benchmark、regression和递归歧义。
definition直接依赖原始`daily_bars.open/close`与`adj_factors.adj_factor`，并在实现内部冻结
Tushare后复权公式`hfq_price = raw_price * adj_factor`，不接受语义不明的外部
`adjusted_close`。它们采用`gtja-reviewed-price/v1`语义profile；Alpha014单位是
`hfq_price_difference`，比例与百分数公式也分别登记真实单位。direction暂记0，由后续因子
评估决定，不把研报历史方向硬编码成永恒结论。

## 5. 状态语义

| 字段 | 含义 |
|---|---|
| `implementation_status=implemented` | 已有对应`factor_id@version`，可以进入managed registry |
| `implemented_variant` | 有本地近似definition，但差异已逐条记录 |
| `ready` | 公式与输入清楚，尚未实现 |
| `blocked_data` | 缺数据、typed合同或PIT证据 |
| `blocked_semantics` | 算子、括号、回归、递归或修正尚未批准 |
| `risk_model_only` | 风险暴露/归因合同，不作为预测Alpha |
| `data_availability=current_cache_partial` | 字段在现有cache中，但覆盖不代表完整全A |
| `derived_from_current` | 可从现有字段确定性派生，例如Tushare VWAP |
| `provider_extension` | Tushare能补数据或行项目，但正式schema/历史覆盖未完成 |
| `benchmark_contract_required` | 需要明确指数身份、价格口径和回归政策 |

`distribution_status=metadata-only`表示只分发来源和治理元数据。公开阅读券商研报不等于
OSS授权；DolphinDB固定提交只作为Apache-2.0数值oracle，TradeMaster不依赖其运行时。

## 6. 存储和查询

公共目录使用内容寻址JSON作为权威对象，DuckDB只做发现：

```text
<factor-root>/library/
  catalog.duckdb
  manifest.json
  sources/<source_id>/<record_sha>.json
  collections/<collection_id>/<record_sha>.json
  candidates/<candidate_id>/<record_sha>.json
```

`manifest.json`绑定完整source/collection/candidate成员集及每个对象的路径和hash；加载不从
DuckDB推导成员集。DuckDB中的发现行和dependency索引会逐项回验JSON。因而同一identity
出现不同内容、索引行被删除/篡改、JSON丢失、文件hash变化或definition映射不存在都会
fail closed。

```bash
tm-factor --root <factor-root> library-sync
tm-factor --root <factor-root> library-status
tm-factor --root <factor-root> library-list \
  --collection gtja-alpha191-2017.06.15 \
  --status implemented \
  --dependency daily_bars.close
tm-factor --root <factor-root> library-list \
  --collection huatai-53-2020.06.02 \
  --data-availability current_cache_partial
tm-factor --root <factor-root> library-describe huatai53.value.ep
```

所有460条记录无需在本文复制；`library-list`返回稳定ID和状态，`library-describe`返回单条
完整公式引用、输入、blocker、variant差异和内容hash。

## 7. 后续升格顺序

1. 把`pe_ttm/pb/ps_ttm/dv_ttm`和typed PIT财务指标纳入正式M1 schema；
2. 建立复权OHLCV/VWAP research view，执行层仍使用原始价格；
3. 冻结rolling/rank/corr/regression/SMA/decay等operator profile；
4. 为每条候选保存source AST、approved AST、勘误和golden case；
5. 补全全A历史日线、日频换手、历史股票状态和历史行业，再做无偏IC/分层评估。
