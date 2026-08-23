# M9 全A研究代码质量审核与处置

审核日期：2026-08-23
范围：20年全A数据、PIT panel、因子评估、报告、legacy基本面策略
方式：3个fresh-context subagent各做一次只读bounded review；随后按TDD修复，不循环追加审查。

## 架构审核

| ID | 严重度 | 问题 | 处置 | 结果/边界 |
|---|---|---|---|---|
| A-01 | High | 日期级财务公告在公告日收盘立即可用 | accept/complete | `date_only_next_eligible_session`，真实fundamental panel重建 |
| A-02 | High | 下载task未绑定实际request evidence | accept/complete | task/evidence/Parquet metadata三方canonical request SHA校验 |
| A-03 | High | verify只证对象完整性，blocked无生产路径 | accept/partial | v2 outcome、typed blocker已实现；124个历史空对象只能追溯为`verified_empty`，不能事后伪造source-history原因 |
| A-04 | Medium | report未绑定实际panel/materialization | accept/complete | report identity v3绑定market/factor input manifest SHA，CLI先重验输入 |
| A-05 | Medium | fundamental report无仓库内CLI路径 | accept/complete | `evaluate --factor-manifest-kind fundamental`与`verify-report` |
| A-06 | Medium | reconcile扫描共享raw glob且无plan-complete gate | accept/complete | 只消费目标plan的typed completed daily evidence，缺任一task即fail closed |
| A-07 | Medium | research反向依赖legacy strategy cache | accept/partial-defer | provider protocol已下沉`trademaster.data`；CLI cache adapter迁移需兼容里程碑 |
| A-08 | Medium | 最新session无publication watermark，跨市场policy硬编码 | accept/defer | M9冻结A股policy；港美股时钟/货币/symbol codec列入后续market-policy规格 |

## 工程实现审核

| ID | 严重度 | 问题 | 处置 | 结果/边界 |
|---|---|---|---|---|
| E-01 | High | request identity未强校验 | duplicate A-02 | 已关闭 |
| E-02 | High | NaN/Inf复权或收益可标为valid | accept/complete | finite-or-null规范，`is_valid => finite`，真实全A非有限valid计数为0 |
| E-03 | High | managed definition与手写SQL可能漂移 | accept/complete | 11因子含null/停牌session逐行Python↔DuckDB parity gate；M9输入合同固定dense sessions |
| E-04 | Medium | 权限、参数、瞬时错误无分类 | accept/complete | 权限立即blocked；credential/invalid/unknown不重试；仅明确transient退避 |
| E-05 | Medium | task领取非原子 | accept/complete | owner/expiry lease、事务claim、过期回收、lost-lease拒绝提交 |
| E-06 | Medium | status反序列化52,563任务且eager import | accept/complete | status直接查catalog、重模块lazy import；实测峰值从约485MB降至约162MB |
| E-07 | Medium | 两个builder直查私有表并复制逻辑 | accept/complete | 统一`completed_evidence_snapshot` typed API |
| E-08 | Medium | legacy cache endpoint/dataset路径逃逸 | accept/complete | stable identifier、root containment、filesystem-root拒绝与回归测试 |
| E-09 | Medium | legacy行情装配近二次扫描 | accept/complete | adj末值和price history改为单次分组，复杂度降为$O(rows)$ |
| E-10 | Medium | out-of-core测试只检查源码字符串 | accept/complete | 128MB DuckDB memory limit、spill目录和真实Parquet行为测试 |

## 因子与策略审核

| ID | 严重度 | 问题 | 处置 | 结果/边界 |
|---|---|---|---|---|
| F-01 | High | GTJA bar-compressed Python与dense SQL窗口不一致 | accept/complete | 正式M9输入固定dense session并对全部11因子做parity；旧cache结果不作M9证据 |
| F-02 | High | 同日公告和未知修订造成基本面前视 | accept/complete | 下一session可用、无revision known-at时只用`update_flag=0` |
| F-03 | High | quantile先过滤未来label，且不是可执行A股收益 | accept/partial | ex-ante分组/换手/容量已冻结；限价、退市和延迟退出明确要求Rust策略回放 |
| F-04 | High | 重叠horizon被当独立收益复利 | accept/complete | 使用真实最小观察间隔14 sessions；20日以上spread/Sharpe/DD/recovery为N/A |
| F-05 | High | capacity重复乘1000 | accept/complete | entry amount直接按CNY，Size方向化capacity约475万元而非约588亿元 |
| F-06 | Medium | evaluator忽略definition direction | accept/complete | direction进入config hash；IC保持raw，Top/spread/turnover/capacity按方向 |
| F-07 | Medium | 因子相关性跨年份混池 | accept/complete | 先逐event相关再等权平均，记录数语义改为有效event数 |
| F-08 | Medium | 市值中性/bucket/状态稳健性不完整 | accept/partial | Size中性RankIC与Top市值tercile完成；ST/板块/年龄/限价分组保留schema/Rust blocker |
| F-09 | High | 旧行业策略用未来静态行业映射 | accept/complete | 默认历史interval required；正式Top1 probe在首个gap前fail closed，旧报告撤回 |
| F-10 | Medium | QFQ合成价进Rust且`.BJ`映射错误 | accept/partial-defer | BSE venue已修；旧报告改称研究近似，公司行动/分红精确账本需独立Rust里程碑 |

## 修复后正式产物

以下路径由真实E2E在本地生成；`artifacts/`不进入Git：

- 全A研究索引：`artifacts/research/full-a-20y/FINAL_REPORT.md`
- 公开11因子报告：`artifacts/research/full-a-20y/public-factor-report/`
- 基本面10因子报告：`artifacts/research/full-a-20y/fundamental-factor-report/`
- 旧策略报告撤回说明：`artifacts/strategies/REVIEW_NOTICE.md`
- 正式Top1 PIT blocker：`artifacts/strategies/m9-pit-audit-top1-blocked-probe/BLOCKED.md`
