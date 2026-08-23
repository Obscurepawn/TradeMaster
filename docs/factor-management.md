# TradeMaster 因子管理

## 1. 边界

Python负责因子定义、计算、物化、评估和信号；Rust只消费最终精确信号并负责市场规则、执行和账本。

```text
PIT source snapshot
  -> governed public factor candidates/source records
  -> managed atomic factor definitions
  -> immutable factor Parquet
  -> grouped/global composite materializations
  -> sparse strategy selection
  -> target-weight signals
  -> Rust runtime
```

因子值的权威存储是Parquet。DuckDB只提供definition、materialization和evaluation的发现索引。
公开因子目录的权威存储是内容寻址JSON；它的DuckDB同样只用于发现。candidate入库不代表
可以执行，只有映射到`FactorDefinition`的记录才能进入物化链路。

## 2. 稳定身份

`FactorDefinition`包含：

- `factor_id/version`；
- typed dataset/factor dependencies；
- 参数的规范化JSON与SHA-256；
- 实现模块源码SHA-256；
- lookback、频率、截面语义、单位和方向；
- 覆盖上述内容的`definition_sha256`。

同一个`factor_id/version`不能对应不同definition。实现或参数发生变化时必须升级version，不能静默覆盖历史结果。

## 3. 存储布局

默认策略数据根下的因子目录为：

```text
factors/
  catalog.duckdb
  library/
    catalog.duckdb
    manifest.json
    sources/<source_id>/<record_sha>.json
    collections/<collection_id>/<record_sha>.json
    candidates/<candidate_id>/<record_sha>.json
  definitions/<factor_id>/<version>/<definition_sha>.json
  values/factor_id=<id>/factor_version=<version>/event_year=<year>/<sha>.parquet
  materializations/<materialization_id>/manifest.json
  evaluations/<evaluation_id>/
```

`FactorMaterialization`绑定definition、代码/参数hash、输入snapshot及其对象集合、精确输入Arrow表hash、universe/range、父因子materialization和输出Parquet。缓存命中时会重新读取并校验文件hash、Schema、行数和metadata。

## 4. 基本面因子

基本面策略suite注册11个definitions：

- 价值：`earnings_yield`、`book_yield`、`dividend_yield`；
- 质量：`roe`、`gross_margin`、`ocf_to_revenue`；
- 成长：`sales_growth`、`profit_growth`；
- 安全：`debt_safety`；
- 复合：`fundamental.composite.industry_relative`、`fundamental.composite.global`。

复合因子严格按事件时间隔离截面；先以PE/PB必需、至少5/9项有效确定eligible universe，再在eligible股票中对原始值做5%/95% winsorize和总体z-score。普通缺失项贡献中性0，固定权重不按可用项重新分配。

公共因子suite另注册11个definitions：一条华泰Size和十条低歧义GTJA价量公式。GTJA首批
definition直接绑定raw OHLC、`adj_factor`和后复权计算公式，并逐条登记价格差/比例/百分数
单位。CLI的
统一managed registry因此当前列出22条；既有基本面策略bundle仍只绑定其实际使用的11条，
不会因为目录扩展改变历史artifact identity。完整460条候选及其数据分层见
[`public-factor-library.md`](public-factor-library.md)。

## 5. 评估

`FactorEvaluator`要求forward-return表逐行携带factor event、entry time、exit time、alignment和
label snapshot ID；另以有序eligible-entry日历证明entry确实是factor event之后的下一可执行事件。
标签Arrow表本身也绑定独立label snapshot。相同标签不能只改一个字符串就在open/close口径间复用。
结果包括：

- coverage；
- 每期Pearson IC和Spearman RankIC；
- IC均值、标准差、ICIR、正IC比例；
- 分位数组收益和Top-Bottom spread；
- Top quantile turnover。

M9的全A evaluation v2在此基础上增加逐horizon coverage/invalid reason、HAC、definition方向、
非重叠spread统计、Size中性RankIC、PIT市值tercile暴露、行业内RankIC、CNY容量proxy及逐event
因子相关。Quantile在未来label join前冻结；direction与真实最小观察session间隔进入config hash。

评估的forward returns、IC、quantiles、summary和manifest均可持久化，并登记到同一factor catalog。
manifest同时绑定label snapshot和eligible-entry日历hash。标签数据只能进入evaluation namespace，
不能流回signal。

## 6. CLI

```bash
tm-factor list
tm-factor describe fundamental.roe@1
tm-factor graph fundamental.composite.industry_relative@1
tm-factor --root <factor-root> verify <materialization-id>
tm-factor --root <factor-root> materialize <factor@version> ...
tm-factor --root <factor-root> evaluate <materialization-id> \
  --forward-returns <labels.parquet> \
  --label-snapshot <manifest.json> \
  --eligible-entry-times <times.json> ...
tm-factor --root <factor-root> library-sync
tm-factor --root <factor-root> library-status
tm-factor --root <factor-root> library-list --collection huatai-53-2020.06.02
tm-factor --root <factor-root> library-list --dependency daily_bars.close
tm-factor --root <factor-root> library-describe gtja191.alpha014
```

正式命令始终要求明确version，不支持隐式`latest`。

## 7. 正式E2E

基本面策略bundle使用`trademaster.e2e-bundle/v3`，同时绑定：

- candidate input snapshots和Parquet；
- 11个factor definitions；
- 20期共220个materializations及其Parquet；
- selection snapshots、signals、Rust request/result；
- report input、HTML、strategy summary和Markdown。

`index_member_all`可以提供部分`in_date/out_date`区间，但M9全A审计仍发现15,013个历史interval
gap。正式策略默认按信号日解析区间并在缺口处fail closed；`static_latest_experiment`拥有独立
策略identity，不能包装成历史PIT分类。旧M7收益artifact另受连续QFQ公司行动会计近似影响，
只保留为诊断证据。

## 8. 二十年全A测评

通用因子研究不再复用策略选股后的`selected_union`价格cache。M9提供独立的可恢复下载计划、
动态历史股票池、稠密session面板、DuckDB流式evaluation和因子专用报告，详见
[`full-a-factor-research.md`](full-a-factor-research.md)。`tm-factor`继续管理低层definition、
materialization与evaluation v1；`tm-research-full-a`负责长周期全市场编排。
