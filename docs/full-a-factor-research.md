# 二十年全A因子研究

> **审查状态（2026-08-23）**：三路fresh-context审核确认的问题已经完成一次bounded修复，
> 下文链接为修复后v2 panel / v3 report。审查前报告仍保留用于追溯，但不再作为正式结论。

## 1. 研究边界

M9使用Tushare下载2006-08-23至运行时最新已完成交易日的因子研究数据。历史股票池来自
`stock_basic`的L/D/P/G全部状态，并按上市/退市有效区间展开，因此不会只保留今天仍上市的
股票。

当前正式计划为：

- 起点：2006-08-23；
- 终点：2026-08-21；
- 交易日：4,860；
- 区间内沪深京CNY股票：5,846，其中3个历史代码由market evidence补足生命周期；
- 下载任务：52,563；
- plan SHA-256：`6ce15c56c759a79f635af3ac489bbeb624620670e4f4cd323eb80935ed17a633`。

“完整全A”在这里指因子研究的数据闭包：股票主表、交易日历、OHLCV、复权因子、每日估值/
市值/换手、涨跌停、停牌、ST、财务指标与三张财务报表、常用指数和可获得的SW2021行业
成员。它不包含新闻、期权、逐笔行情或无许可的专有资金流。

## 2. 数据与执行链

```text
Tushare
  -> immutable endpoint Parquet + exact request hash
  -> DownloadPlanManifest + resumable DuckDB task state
  -> dynamic listing lifecycle x trading-session dense panel
  -> raw OHLC + adj_factor + daily_basic + market status
  -> public price factors / PIT fundamental factors
  -> next-session-close forward labels
  -> DuckDB out-of-core diagnostics
  -> content-addressed JSON/Parquet + standalone HTML/Markdown
```

原始Parquet是事实存储，`catalog.duckdb`只索引精确provider请求，`research.duckdb`只保存
下载任务状态。任务完成时会重新验证文件存在、SHA-256、行数、字段，并要求task的canonical
request SHA、evidence和Parquet内嵌request metadata三者一致。
进程在provider对象写完、状态提交前退出时，下次运行会重新claim任务，但底层cache会复用对象，
不会再次请求Tushare。

默认数据根：

```text
data/research/full-a-20y/
  raw/<endpoint>/<content-sha>.parquet
  catalog.duckdb
  research.duckdb
  plans/<plan-sha>/manifest.json
```

## 3. 下载命令

token只加载到当前进程环境，不写进命令输出、Parquet或manifest：

```bash
export TUSHARE_TOKEN="$(<~/.config/trademaster/tushare_token)"

tm-research-full-a --data-root data/research/full-a-20y plan \
  --start 2006-08-23 --end 2026-08-23

tm-research-full-a --data-root data/research/full-a-20y download \
  --plan-sha256 6ce15c56c759a79f635af3ac489bbeb624620670e4f4cd323eb80935ed17a633 \
  --phase market

tm-research-full-a --data-root data/research/full-a-20y status \
  --plan-sha256 6ce15c56c759a79f635af3ac489bbeb624620670e4f4cd323eb80935ed17a633

tm-research-full-a --data-root data/research/full-a-20y verify \
  --plan-sha256 6ce15c56c759a79f635af3ac489bbeb624620670e4f4cd323eb80935ed17a633
```

阶段为`bootstrap`、`market`、`status`、`statement`、`industry`和`benchmark`。可以用
`--max-tasks N`做小批量验证；同一命令可重复执行，已完成任务不会调用provider。只有全部任务
完成后，`verify`才会逐对象重验并成功。status v2分别列出`object_acquired`、
`verified_empty`、`source_history_absent`和`permission_blocked`；已验证空响应不是权限成功或
历史完整性的替代证明。

## 4. 面板、标签与评估

```bash
tm-research-full-a --data-root data/research/full-a-20y materialize \
  --plan-sha256 <plan-sha> \
  --output-root artifacts/research/full-a-20y/panel \
  --observation-frequency month_end \
  --horizons 1,5,20,60,120,252

tm-research-full-a --data-root data/research/full-a-20y evaluate \
  --plan-sha256 <plan-sha> \
  --panel-output-root artifacts/research/full-a-20y/panel \
  --panel-manifest-sha256 <panel-manifest-sha> \
  --report-root artifacts/research/full-a-20y/report \
  --horizons 1,5,20,60,120,252

tm-research-full-a --data-root data/research/full-a-20y materialize-fundamental \
  --plan-sha256 <plan-sha> \
  --panel-output-root artifacts/research/full-a-20y/panel \
  --panel-manifest-sha256 <panel-manifest-sha> \
  --output-root artifacts/research/full-a-20y/fundamental-panel

tm-research-full-a --data-root data/research/full-a-20y evaluate \
  --plan-sha256 <plan-sha> \
  --panel-output-root artifacts/research/full-a-20y/panel \
  --panel-manifest-sha256 <panel-manifest-sha> \
  --factor-manifest-kind fundamental \
  --factor-output-root artifacts/research/full-a-20y/fundamental-panel \
  --factor-manifest-sha256 <fundamental-manifest-sha> \
  --report-root artifacts/research/full-a-20y/fundamental-factor-report

tm-research-full-a --data-root data/research/full-a-20y verify-report \
  --report-root artifacts/research/full-a-20y/fundamental-factor-report \
  --manifest <report-manifest-path>
```

报告identity v3绑定实际参与评估的market panel与factor manifest SHA；CLI在计算前重验其
Parquet对象，`verify-report`再重验报告bundle，不能只凭可变DuckDB行或报告文件名复现结论。

稠密panel保留每只有效股票的每个交易session。停牌或缺bar不会被删除，因此`lag(5)`始终是
五个市场session，而不是五条非空观测。因子在完整session窗口计算，之后才抽取月末观察。

close-derived因子的事件是T日收盘；`next_session_close`标签以T+1收盘入场，horizon为`h`
时以入场后第`h`个session收盘退出。入场不可交易、entry/exit价格缺失或未来session不足都会
保留label行并写明确invalid reason。

## 5. 已管理因子

第一份公开因子报告覆盖：

- `huatai53.size.log_total_market_value@1`；
- `gtja191.alpha014/015/018/020/031/034/046/053/058/088@1`。

基本面PIT构建器另生成现有9个atomic因子和`fundamental.composite.global@1`。Tushare
`ann_date`只有日期、没有盘中发布时间，因此公告最早从严格晚于`ann_date`的下一eligible
session可用；没有修订发布时间时只接受`update_flag=0`原始版本，修订值不会回填到历史公告
日。manifest分别记录`date_only_next_eligible_session`与
`original_only_without_revision_known_at` policy，并逐行保留`financial_known_at_session`。

`fundamental.composite.industry_relative@1`只有在每个观察键都具有唯一历史行业interval时才
materialize；否则manifest记录`historical_industry_membership_interval_gap`，不会用当前行业
静态回填过去。

## 6. 测评指标语义

| 指标 | 含义 |
|---|---|
| Universe coverage | PIT股票池中应有的股票×观察日数量 |
| Factor / Label / Joint coverage | 因子有效、标签有效、两者同时有效的比例；分母始终是PIT universe |
| Horizon label coverage / invalid reason | 每个horizon独立的标签/联合覆盖与失效原因；不能用跨horizon均值代替 |
| IC / RankIC | 因子值与未来收益的Pearson/秩相关 |
| ICIR / RankICIR | IC均值除以其时间序列标准差；当前不做隐式年化 |
| IC t-stat | 假设独立同分布时的均值显著性 |
| Newey-West t-stat | 修正异方差和时间自相关，重叠horizon应优先阅读它 |
| Positive IC ratio | IC大于0的观察期比例 |
| Quantile monotonicity | 分位编号与分位平均收益的相关性 |
| Top-Bottom spread | 按definition方向后的经济Top减Bottom forward return；IC/RankIC仍为raw |
| Direction | `-1`表示原始低值为经济Top；`0/+1`表示原始高值为Top；IC仍保留raw符号 |
| Spread Sharpe / drawdown | 只对非重叠spread研究序列计算；重叠horizon为N/A |
| Recovery events | spread离开高水位后最长水下观察数，不是自然日 |
| Top turnover | 相邻观察期Top分组成员变化率 |
| Size correlation | 因子与`log(total_mv)`的截面相关 |
| Size-neutral RankIC | 截面回归`raw factor ~ 1 + log(total_mv)`后，残差与未来收益的RankIC |
| Top cap tercile shares | 完整PIT universe按当期市值三分位切组后，方向化Top成员的小/中/大市值数量占比 |
| Industry RankIC | 有历史PIT行业时的行业内RankIC；否则N/A |
| Capacity proxy | `Top组最小成交额 × 参与率 × 股票数`的粗略AUM proxy |
| Factor correlation | 先逐event算Pearson/Spearman再等权平均；记录数是有效event数，用于发现冗余 |

HTML中的容量proxy没有模拟盘口、冲击、佣金、印花税、滑点或涨跌停排队，不等于实际可管理
资金。Quantile在连接未来label前冻结，不会因退市或远期标签缺失改变当期成员；Top-Bottom也
不是已经过Rust交易约束回放的策略收益。真实月末观察的最小间隔为14个session，因此20/60/
120/252日forward标签属于重叠horizon，其spread累计、Sharpe、回撤与修复时间显示N/A。

## 7. 已知数据边界

- Tushare当前不返回独立BSE交易日历；北交所自2021-11-15起采用带policy ID的SSE session
  派生映射。
- SW2021行业接口有`in_date/out_date`，但不能假设它完整重建了2006年以来每次历史修订；
  覆盖不足的行业中性指标显示N/A或blocked。
- `stock_st`虽声明历史从2000年开始，实际完成后仍需按日coverage审计。
- HFQ=`raw_price * adj_factor`只服务因子研究；Rust执行和成交约束继续使用原始行情。
- direction映射来自managed definition并进入evaluation config hash；不会用全样本收益事后
  选择方向。当前Size为`-1`，10条GTJA为`0`。
- 当前报告尚未把ST、板块、上市年龄、涨跌停入场/退出拆成完整稳健性表；历史ST覆盖和精确
  A股退出延迟需要独立coverage proof与Rust规则回放，报告将其列为blocker而不是伪造收益。

## 8. 本次真实结果

本地运行后产物总索引位于`artifacts/research/full-a-20y/FINAL_REPORT.md`；`artifacts/`是
可重复生成目录，不随Git仓库发布。

最终下载状态为52,563/52,563 completed：52,439个`object_acquired`、124个
`verified_empty`、0 failed/blocked。真实token下完整cache probe的provider调用为0；最终
verify同时重验task/evidence/Parquet request SHA、对象SHA、metadata和行数。原始数据约2.7GB。

修复后市场panel manifest为
`9f163dd4c7b3fe958c4beb363303e767dbe40c00523e5d2a928ef24feb1d4c89`，
fundamental manifest为
`2f71fc7d9e081d7368ddbd0af8d0adf076b39e6a24ae03c9618afa29a1f2b2a2`。

公开11因子报告：

- HTML：`artifacts/research/full-a-20y/public-factor-report/objects/b3/b372bf8d50215a26bdfde017845b4a387307fe6561c7b57948909b2151a4a0c5.html`
- Markdown：`artifacts/research/full-a-20y/public-factor-report/objects/92/92bc256f85b9bfd71440aa795405df5842f1e15ce4a075c53a511d401740a1b6.md`
- report ID：`413067aee36089e6f3295c5165e1f069217a71c481436295d8411512ea29b927`
- report manifest：`5d0a8013a0e2086e6dcb10af1ee84499c75034048f75a7bcb6ed82eb109407f6`

20-session代表性raw RankIC：Alpha046为0.0546、Alpha034为0.0450、Alpha088为-0.0690、
Size为-0.0668。Size中性后分别为0.0562、0.0466、-0.0675和0.0074，说明Size原因子的
表现几乎完全是市值暴露。技术因子Top组换手约0.74–0.81；方向化Size Top组换手约0.11、
容量proxy约475万元，符合小资金研究但不是成交保证。逐event Rank相关中Alpha031/034为
-1.0000、Alpha034/046为0.9583、Alpha058/088为0.5575，存在明显冗余。

本地基本面10因子报告：

- HTML：`artifacts/research/full-a-20y/fundamental-factor-report/objects/50/5005791eabfcc7a6f2f0a93ffefe7003b9c670b7bf483553d4378a3a905cfbc0.html`
- Markdown：`artifacts/research/full-a-20y/fundamental-factor-report/objects/2d/2d0ea21e0e5b743555bf783bbf52b1f629cf421a5e56c0a783681fea9bf4b86a.md`
- report ID：`eddc6bef5097db84e1cb7d337d401bf241ec478536a0bdcf208aa0d52fb9a761`
- report manifest：`93c82636da316a441ef67743e8966cee1ff7b578f575614a410f6334733542d2`

20-session raw/Size中性RankIC：book yield 0.0555/0.0590、dividend yield
0.0382/0.0499、earnings yield 0.0335/0.0578、global composite 0.0279/0.0459。
保守PIT政策使global composite raw RankIC从审查前0.0315降至0.0279；9个财务atomic因子中
约17万到18万条共同有效暴露的值发生变化，global composite有613,223条共同有效值变化。
基本面Top组换手约0.04–0.16，容量proxy约1,600万至6,500万元。行业relative composite仍因
15,013个历史interval gap blocked。

真实panel coverage：15,882,389个active session rows；15,370,049个bar；同日bar缺
adj_factor为0，缺daily_basic为20；显式停牌473,924行、由adj连续性识别的停牌52,523行、
仍无证据解释的缺bar为4,963行。有效factor和forward-return中的非有限值均为0。
