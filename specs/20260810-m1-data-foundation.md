# M1 数据底座实施规格

Status: completed
Owner: Codex

## 交付边界

M1 只实现 Tushare 数据接入、三层 Parquet 数据湖、DuckDB catalog/coverage、PIT 查询与
不可变 snapshot。它不计算因子、不运行交易，也不把 DuckDB 文件当作权威数据。

## 目录与权威性

- `data/raw/<endpoint>/ingest_date=YYYY-MM-DD/*.parquet`：保留 provider 原始字段及请求元数据。
- `data/staging/<dataset>/<registry_partition>/*.parquet`：类型统一、代码规范化、去重后的暂存层。
- `data/canonical/<dataset>/<registry_partition>/*.parquet`：唯一权威事实；每个 dataset 在 registry
  独立声明 partition、event identity、主键与 `known_at/source_revision`，不假定都有 instrument。
- `data/catalog.duckdb`：只保存对象索引、coverage 和 view，不复制 canonical 事实。
- `data/snapshots/<snapshot_id>/manifest.json`：绑定请求、policy、对象内容摘要及 coverage 证明。

## Catalog 合同

`objects(dataset, partition_key, uri, sha256, schema_sha256, schema_version, row_count,
event_time_start, event_time_end, known_at_max, fields_json, coverage_keys_json,
coverage_keys_sha256, instrument_set_sha256, instrument_count, created_at)`；
`coverage(dataset, request_sha256, covered_start, covered_end, fields_json,
coverage_keys_json, coverage_keys_sha256, instrument_set_sha256, instrument_count, object_sha256,
verified_at)`。两表以内容摘要幂等写入，冲突即失败。

唯一键分别为 `objects(dataset, partition_key, sha256)` 与
`coverage(dataset, request_sha256, object_sha256)`；schema migration 使用单调版本号和事务，
未知的更高版本拒绝启动。

## V1 canonical datasets

| dataset | 主键 | 必需业务字段 | `known_at` 规则 |
|---|---|---|---|
| `trade_calendar` | venue, session_date | is_open, open_at, close_at | provider ingest time |
| `instrument_master` | instrument_id, effective_from | venue, asset_class, list/delist | announcement/effective policy |
| `daily_bars` | instrument_id, trade_date | OHLCV, amount, pre_close | session close + provider lag |
| `daily_limits_status` | instrument_id, trade_date | up/down_limit, suspended, ST | session open 可知状态策略 |
| `adj_factors` | instrument_id, trade_date | adj_factor | provider publication time |
| `daily_basic` | instrument_id, trade_date | float/total_mv, turnover | provider publication time |
| `index_bars` | instrument_id, trade_date | OHLCV | session close + provider lag |
| `index_membership` | index_id, instrument_id, effective_from | effective_to, weight | announcement/effective policy |
| `industry_membership` | taxonomy, instrument_id, effective_from | industry_id, effective_to | announcement/effective policy |
| `financial_indicators` | instrument_id, report_period, announcement_id | report values, revision | actual announcement timestamp |

所有 canonical 表还必须含 `event_time/source_revision/known_at`；业务主键加
`source_revision` 唯一，PIT 查询对同一业务主键取 cutoff 前最后修订。

## Coverage/session 算法

先用 `trade_calendar` 将 `[start,end]` 展开为排序后的 `coverage_keys`（例如
`SSE:2025-01-02`），再与规范化 universe 和字段列表分别用 UTF-8 compact JSON 计算 SHA-256。
对象必须声明其实际 coverage keys；对象集合的无重复并集必须与请求 keys 完全相等，不能只比较
首尾日期。非 session 型数据用其业务主键前缀作为 coverage key，规则写入 dataset registry。

## Coverage 责任

`DataPortal.ensure()` 先规范化请求和解析 universe，再由 DuckDB 证明日期/session、字段和标的集合
完整性；只有明确缺口才调用 Tushare。下载完成后必须检查分页、主键唯一、请求边界、schema、
`known_at` 和内容摘要，随后原子发布 canonical object 与 catalog。`DatasetCoverage` 只能由这一
验证器产生，不能由 strategy 或 notebook 手工声明。

## Snapshot 与文件验证

`snapshot()` 只选择 `known_at <= as_of` 的 immutable objects，并将 request/policy/coverage/object
共同内容寻址。查询返回的 Arrow metadata 同时绑定 snapshot、request 和 object-set digest。
正式运行前重新读取 Parquet 并核对文件 SHA、row count、字段、universe 和时间范围；报告层通过
`verify_run_manifest()` 重新读取十一个运行 Parquet，核对 run_id/hash/rows 及跨表生命周期后才接受
`status=complete`。

## 原子发布与恢复

下载写入同文件系统临时目录，完成 schema/coverage/hash 校验后 `fsync` 文件与目录，再以 rename
发布 immutable Parquet；随后在一个 DuckDB 事务内登记 object 与 coverage。启动恢复会删除未登记
的临时文件、登记孤立但摘要正确的已发布对象，绝不覆盖已有内容地址。provider 页游标与请求摘要
写入 raw metadata，重复运行从最后已验证页继续。

## 实施任务

| Task | Owner | Status | Green 证据 |
|---|---|---|---|
| M1.1 config/paths/dataset registry | Codex | done | 5 tests; Ruff/mypy/full pytest Green |
| M1.2 DuckDB migrations/catalog | Codex | done | 4 real DuckDB tests; full gates Green |
| M1.3 Parquet atomic object store | Codex | done | 4 real Parquet publish/tamper/recovery tests |
| M1.4 coverage planner + fake provider | Codex | done | 4 cache/gap/pagination tests; full gates Green |
| M1.5 Tushare adapters | Codex | done | all 10 registry datasets fixture-publish Green |
| M1.6 PIT query/snapshot | Codex | done | 3 cutoff/provenance/tamper tests Green |
| M1.7 fresh review closure | Codex + Fresh agent | done | Review 13 known 3H closed; full gates Green; global review deferred to M6 |

## Red/Green 测试

1. Red：缓存有完整 coverage 时仍触发 provider；Green：provider 调用数为 0。
2. Red：局部缺口、分页缺页、字段缺失、错误 universe 或 schema drift 被当作 complete；Green：
   精确补缺或 fail closed。
3. Red：`known_at > as_of`、未来修订或 T 日收盘后发布值进入查询；Green：PIT 隔离。
4. Red：篡改 Parquet、row count、catalog coverage 或 manifest 任一项仍可读取；Green：摘要链拒绝。
5. Red：同一规范请求重复运行产生不同 snapshot；Green：内容与排序确定时 ID 相同。

## 实施记录

- M1.1 Red：`tests/test_data_config_registry.py` 因 `trademaster.data` 不存在而在收集阶段失败。
- M1.1 Green：增加只保存 token 环境变量名的 `DataConfig`、限定目录的 `DataPaths`、逐数据集
  `DatasetSpec/DatasetRegistry` 和确定性 `normalize_request()`。5 个 focused tests、20 个全量
  Python tests、Ruff、strict mypy 和 `git diff --check` 通过。
- M1.2 Red：catalog 公共类型不存在，DuckDB 集成测试在收集阶段失败。
- M1.2 Green：version 1 migration 创建精确 `objects/coverage` 列；UTC 时间以微秒整数存储，
  避免运行时 timezone 依赖并保持跨语言精确。对象与 coverage 在同一事务内幂等登记，metadata
  冲突回滚，未知更高版本 fail closed。4 个 focused tests、24 个全量 tests、Ruff、strict mypy
  和 `git diff --check` 通过。
- M1.3 Red：Parquet object-store 公共接口不存在，测试在导入阶段失败。
- M1.3 Green：规范请求/分区/创建时间写入 Parquet metadata；实际文件 bytes 决定 SHA 文件名；
  临时文件与目录 fsync 后使用同文件系统 hard-link 原子发布。读取时重算 hash/schema/rows，篡改
  fail closed；启动删除残留 temp，并从 Parquet 自描述 metadata 重建和登记 catalog 孤儿对象。
  4 个 focused tests、28 个全量 tests、Ruff、strict mypy 和 `git diff --check` 通过。
- M1.4 Red：cache-first portal/page/provider 公共接口不存在，测试在导入阶段失败。
- M1.4 Green：planner 只接受字段、universe、请求 key 子集匹配且重新验证过 Parquet 的 catalog
  对象；完整缓存 provider 调用为零。缺口按明确 keys 获取，分页 key 必须无重复且并集精确，发布后
  再从 DuckDB 证明完整。4 个 focused cases（含参数化反例）、32 个全量 tests、Ruff、strict
  mypy 和 `git diff --check` 通过。
- M1.5 Red 1：Tushare credential/provider 公共接口不存在，fixture tests 在导入阶段失败。
- M1.5 Green 1：SDK 位于注入式 `TushareClient` 边界后；token 只在 factory 创建 client 时读取，
  raw request metadata 不含 token。`trade_cal` 和 `daily` 返回先写 raw，再规范化写 staging，并形成
 逐 coverage-key page。4 个 fixture tests 通过；真实 smoke 因进程没有 `TUSHARE_TOKEN` 明确
  skip，不计作网络验证。全量 36 passed/1 skipped，Ruff、strict mypy、diff check 通过。
- M1.5 Red 2：其余八个 registry dataset 均因明确的 unsupported normalizer 失败。
- M1.5 Green 2：`daily_limits_status` 组合 `stk_limit/suspend_d/stock_st`；其余 session、instrument、
  index/industry membership 和 financial revision endpoint 均规范化为 registry contract。参数化测试
  对十个 V1 dataset 的 required fields、partition、coverage key 和真实 Parquet publish 做同一门禁。
- M1.6 Red：cache-first portal 缺少 `snapshot/query`，三个 PIT tests 明确失败。
- M1.6 Green：同 coverage-key replacement 在 cutoff 前选择最新 eligible immutable object；snapshot
  绑定完整 request/policy/object digests 并原子落盘；DuckDB `read_parquet()` 按业务主键选择
  `known_at <= as_of` 的最后修订并附加 provenance。确定性、未来修订隔离与篡改反例通过。
- M1 初始门禁：47 passed/1 credential-gated skipped；Ruff、strict mypy、`git diff --check` 通过。
- M1 fresh review 1：FAIL。Critical 为 `instrument_master` 把当前 `delist_date` 赋予上市日
  `known_at`，造成历史 PIT 前视。High 为 canonical Arrow 类型未冻结；coverage 表及其 digest 未
  参与 proof；空 universe/coverage keys 缺少 resolver；4 个 Tushare endpoint 参数/字段偏离官方
  合同；单 coverage key 内 endpoint 行数上限/分页不可证明。两个 Medium 是 raw/staging 页级
  provenance 不足、migration 只检查列名。所有发现均保留为待关闭项，M1 不得完成。
- M1 Green 2：为十个 canonical dataset 冻结逐字段 Arrow 类型并在发布前精确比较；session 型
  instrument 数据拒绝未解析 universe。catalog 读取时重算 coverage-key digest，`_plan()` 必须
  找到与 object 的字段、keys、universe 完全相同的 coverage proof。公共 `RequestResolver` 允许
  配置层显式解析 universe/keys，未配置时继续 fail closed，trade-calendar 日期 key 可确定展开。
- M1 Green 2：修正 `stock_basic` 上市/退市事件隔离、`index_member_all` 官方字段、`index_daily`
  `ts_code`、`suspend_type=S`、三类 `list_status`，并为各 endpoint 加独立 limit/offset 分页和重复页
  检测。raw metadata 绑定请求摘要、cursor、页号与行 revision；staging 绑定实际 raw Parquet hashes。
- M1 Green 2：DuckDB 启动时校验列名、类型、NOT NULL 与主键/唯一约束；canonical 发布拒绝
  `known_at < event_time`。日线/指数线、daily-basic、复权因子和开盘状态使用 registry policy 对应
  的确定性 UTC availability，而不是历史数据的本次下载时间。
- M1 Green 2 门禁：56 passed/1 credential-gated skipped；Ruff、strict mypy、`git diff --check`；
  Rust fmt/Clippy、26 tests/doc-tests；wheel/sdist 均通过。真实网络 smoke 因进程没有
  `TUSHARE_TOKEN` 仍明确 skip，不算已验证。
- M1 fresh review 2：FAIL。三个 Critical：退市事件匹配先限定 `list_date` 导致 delist 分支不可达；
  industry normalizer 不绑定 `in_date`/taxonomy 而可伪造历史；coverage 只证明 instrument 与
  session 的边际集合，不能证明要求完整的数据集上的 instrument×session 矩阵。五个 High：schema
  未锁列顺序/nullability；coverage proof 未校验 request digest/range/time；resolver 可改写既有
  universe 且 business-key 空 universe 未拒绝；suspend/ST 辅助行未过滤请求日期；相同 known_at
  的修订用 source hash 字典序选取。三个 Medium：catalog_meta 非单例；stock_basic 未明确 G 状态；
  index membership 的 availability/移除区间尚未实现。M1 继续保持 active。
- M1 Green 3：`instrument_master` 以 list/delist 两类 effective event 匹配并覆盖 L/D/P/G 状态；
  `industry_membership` 只支持显式 `SW2021` taxonomy，按请求 instrument 查询并要求 provider
  `in_date` 精确相等；停牌/ST 辅助表也必须匹配请求 `trade_date`。
- M1 Green 3：registry 现在生成逐列有序、不可空的 canonical Arrow schema；发布门禁精确比较
  顺序、类型和 nullability。`daily_limits_status` 声明为完整 instrument×session matrix，且一个请求
  只能属于一个 venue；缺任意组合即拒绝，避免用两个边际集合冒充关系覆盖。
- M1 Green 3：planner 从实际 Parquet 自描述请求重建权威 coverage，再要求 DuckDB proof 完全相等，
  因而同时绑定 request digest、range、fields、keys、universe、object 和 verified time，并仍允许多个
  独立 partial objects 安全补齐大请求。resolver 只可填空字段，不能替换调用方已有 instruments/keys；
  所有含 instrument 的数据集空 universe 均 fail closed，calendar venue/key 也不再硬编码 SSE。
- M1 Green 3：financial PIT 在相同 `known_at` 时使用 registry 声明的业务 `revision` 排序；
  `catalog_meta` 必须恰有一行。65 passed/1 credential-gated skipped，Ruff、strict mypy 和 diff check
  已通过；完整跨语言/打包门禁与 fresh review 3 待执行。
- M1 fresh review 3：FAIL，0 Critical/6 High/1 Medium。High 为 sparse daily-bars 边际集合误证；
  object 要求全 universe 与 instrument bucket/venue 自然分区冲突；相同 business revision 仍由 hash
  排序；canonical 未绑定 staging/raw；ETF 在 v1 声明但 provider 仅股票；raw cursor 可审计但不能
  跨进程 resume。Medium 为 index-membership interval/availability 尚未闭合。
- M1 Green 4：`daily_bars` 与 `daily_limits_status` 均声明 venue-scoped complete matrix；停牌缺 bar
  当前严格 fail closed，不伪造 OHLC。正式允许 sparse bars 前必须增加由完整 status matrix 支持的
  explicit absence proof。`instrument_master` 改为单一 `asset_class` partition，financial 改为
  单一 `dataset_scope` partition，避免 partition object 与 resolved universe 不可能同时满足。
- M1 Green 4：相同 primary key、known_at 与 business revision 的多行在 canonical 发布时作为歧义
  拒绝。ProviderPage 携带 staging/raw 内容 hashes，canonical Parquet metadata 继续绑定这些上游
  hashes；raw metadata 保存 fields，从每个对象可独立重算 provider request digest。
- M1 Green 4：配置增加显式 ETF instrument 集合；instrument master 对 ETF 使用 `etf_basic`，日线
  使用 `fund_daily`，股票继续使用 `stock_basic/daily`，canonical contract 保持一致。其他 ETF 因子
  或状态 endpoint 未声明支持时必须 fail closed，不可回落到股票数据。
- M1 Green 4：`_query_raw()` 启动时扫描并逐文件重算 raw SHA，核对 request/fields/params/cursor/
  returned rows/row revisions，按连续 page index 恢复；首次 offset=6000 模拟崩溃、第二次只请求
  offset=6000 的跨 provider-instance 回归通过。当前 focused/full Python 为 68 passed/1 credential
  skip；完整 gates 与 fresh review 4 待执行。
- M1 fresh review 4：FAIL，0 Critical/6 High/2 Medium。High 为严格 bars matrix 无法表达合法停牌
  absence；mixed stock/ETF master 仍跨 asset partitions；equal business revision 跨对象歧义；上游
  hashes 只校验格式不定位文件；cached empty terminal 固化延迟修订；ETF venue/退市生命周期错误。
  Medium 为 raw `ingested_at` 未校验与 index membership interval/availability。
- M1 Green 5：portal 在补 daily bars 前先完整证明同 universe/session 的 `daily_limits_status`；仅当
  缺失 pair 在真实 content-addressed status object 中为 `suspended=True` 时，写入绑定该 status SHA
  的 canonical absence proof。读取/恢复时重新 hash status Parquet 并重放 suspension 行。正式 bar
  snapshot 自动纳入 status dependency，不能只携带孤立 bar object；无证明的缺行继续 fail closed。
- M1 Green 5：instrument master 的 stock/ETF 使用同一 `dataset_scope` object；financial 也使用单一
  logical scope。planner 会跨 replacement objects 检查 primary-key/known-at/business-revision identity，
  不同 source 内容冲突即失败，不由 created_at/hash 选业务事实。
- M1 Green 5：canonical publish/verify 会在 raw/staging 根目录定位每个上游 SHA、重算文件内容，要求
  至少一个 staging，并重放 staging metadata 中的 raw hashes。cached raw 的 `ingested_at` 必须为
  UTC；allow-empty endpoint 的空 terminal 不复用，下一次会重新询问 provider。
- M1 Green 5：ETF `SH/SZ` 规范化为 `SSE/SZSE`；`etf_basic` 非 L 状态在缺少权威退市日期时
  fail closed，不再伪造永久存续。70 passed/1 credential skip、Ruff/mypy/diff Green；完整 Rust/
  package gates 与 fresh review 5 待执行。
- M1 fresh review 5：FAIL，0 Critical/8 High/2 Medium。High 为错误 dataset Parquet 可冒充停牌
  absence evidence；snapshot 未绑定 bars 引用的具体 status SHA；全体停牌时零行 bars 无法发布；
  同 source revision 的跨对象冲突 payload 未拒绝；自造 raw/staging metadata 可洗白 canonical；ETF
  bars 的正式 portal/snapshot 路径被股票 status 依赖阻断；ETF venue 未绑定代码后缀；empty terminal
  的空/非空修订会形成永久 raw conflict。Medium 为 index-membership interval/availability 尚未闭合，
  以及本规格状态摘要陈旧。
- M1 Green 6：absence proof 只能引用经过完整 canonical metadata/schema/request 重放且 dataset 为
  `daily_limits_status` 的对象；snapshot 必须包含 bars 实际引用的证据 SHA，否则 fail closed。零行
  canonical bars 只在每个 instrument×session 均有 content-addressed suspension proof 时允许，覆盖
  all-suspended session 并可由 DuckDB 查询为空表。
- M1 Green 6：跨对象 business revision identity 对完整 canonical payload 计算摘要，不再只比较
  `source_revision`。raw 页重放 endpoint/request/time/cursor/row revisions；staging 重放 dataset、partial
  request、coverage、schema、source revisions、raw 引用，并要求全部 staging 行的规范并集与 canonical
  内容精确相等。ETF 仅接受与 instrument suffix 一致的 `SH/SZ` venue；完整 `fund_daily` bars 不再
  强制依赖股票 status。allow-empty mutable endpoint 以最新 ingestion revision 为准，并总是刷新终止页，
  同时拒绝同一时间戳的歧义内容。
- M1 Green 6 fresh gates：76 passed/1 credential-gated skipped；Ruff 与 strict mypy 通过；Rust fmt、
  Clippy、26 tests/doc-tests 通过；wheel/sdist 与 `tm-core` package verify 通过。`tm-engine` 的 registry
  package 当前因同版本 `tm-core` 尚未发布而无法准备/验证，因此仅工作区 build/test 通过，不能声称
  独立 crate package 通过；正式发布需先发布 `tm-core` 或使用本地 registry 做双 crate 验证。真实网络 smoke 因进程
  无 `TUSHARE_TOKEN` 继续明确 skip，不计作通过。fresh review 6 待执行。
- M1 fresh review 6：FAIL，0 Critical/6 High/3 Medium。High 为 raw metadata 自洽但业务变换可与
  staging/canonical 相反；跨对象 payload 冲突只覆盖有 business revision 的 dataset；同一
  `ingested_at` 的 allow-empty 空→非空 live 修订当次未拒绝；混合 stock/ETF 稀疏 bars 的 status
  请求错误携带 ETF；显式宽 status 与自动窄依赖会把同一对象重复加入 snapshot；fresh strict mypy
  实际有两处 test list type 错误。Medium 为缺少持久化 snapshot 的公开文件级 verifier、index
  membership interval/availability 以及 package/spec 记录边界。
- M1 Green 7：增加独立 `tushare_replay` 验证器，从 staging 实际引用的 raw Parquet 逐 dataset 重算
  十类 canonical 行，再与 staging/canonical 规范并集精确比较；provider metadata、source revision
  或 staging 自报一致但业务字段被改写时仍 fail closed。跨对象 conflict 检查扩展到所有 dataset，
  相同 primary key/known_at revision identity 的不同 payload 不再由写入时间选胜。
- M1 Green 7：allow-empty 终止页刷新会把 live fingerprint 与同一 ingestion timestamp 的缓存观察
  比较，空/非空歧义当次拒绝。bars 仅为实际 missing pairs 请求 status instruments/coverage keys，
  因而 mixed stock/ETF 中完整 ETF bar 与停牌股票可共同发布；snapshot 从实际 absence proofs 推导
  窄依赖，并在显式 status 已选中 exact evidence SHA 时去重。跨 universe 的更晚 status correction
  也会使旧 suspended proof 失效，不能靠窄对象遮蔽修订。当前 80 passed/1 credential skip、fresh
  strict mypy 与 Ruff 通过。完整 Green 7 gate 为 80 passed/1 credential skip、Rust fmt/Clippy、
  26 tests/doc-tests、wheel/sdist、`tm-core` package verify 与 diff check 全部通过；fresh review 7 待执行。
- M1 fresh review 7：FAIL，1 Critical/2 High/3 Medium。Critical 为 raw 3 月 `ingested_at` 未约束
  staging `normalized_at`，可将 ingest-derived `known_at` 回拨到 1 月并进入错误 PIT。High 为跨不同
  resolved universe 的同一 revision/payload conflict 未进入同一审计集合，以及 raw endpoint 的
  params/fields 只自摘要、未绑定 partial canonical request。Medium 继续为 snapshot 文件级 loader、
  index-membership interval/availability 和根规格 Next step 陈旧。
- M1 Green 8：staging `normalized_at` 必须不早于其引用的每个 raw object `ingested_at`，从而禁止
  ingest-derived availability 回拨；逐 endpoint 的官方 fields 与 params 现在由独立 replay validator
  精确绑定 partial request 的 venue/date/instrument/index/period/list-status。全局 revision consistency
  在 requested coverage 相交且 cutoff eligible 的所有 canonical objects 上执行，先于 exact-universe
  coverage 选择，窄/宽 universe 不再能给同一事实不同 payload。新增三个持久化反例测试。
- M1 Green 8 fresh gates：83 passed/1 credential skip；Ruff、无参数 strict mypy、Rust fmt/Clippy、
  26 tests/doc-tests、wheel/sdist、`tm-core` package verify 与 diff check 全部通过；fresh review 8 待执行。
- M1 fresh review 8：FAIL，0 Critical/2 High/3 Medium。High 为更晚的 status correction 即使仍是
  `suspended=True`，旧 exact absence proof 仍未失效；以及 `DataConfig.paths` 丢失权威 ETF 集合，
  provider/store 可把配置 ETF 通过股票 `daily/stock_basic` 证据发布。Medium 为重叠且被包含的
  snapshot requests 可能重复对象、缺少公开 persisted snapshot loader/verifier，以及 index membership
  interval/availability 尚未闭合。Green 9 先以持久化反例关闭两项 High，再跑完整门禁和新一轮复审。
- M1 Green 9 Red：同一缺失行情的较新 `suspended=True` revision 未使旧 proof 失效；配置中的 ETF
  集合未进入 `DataPaths`，无显式 provider 参数时实际调用 `stock_basic`；合法的同 scope 不同字段
  snapshot 请求因重复对象失败；持久化 manifest 没有公开 loader。对应 focused tests 均先按预期失败。
- M1 Green 9：任何较新 eligible status revision 都使旧 exact proof 失效。`DataConfig` 将排序后的 ETF
  集合传入 immutable `DataPaths`，provider 仅从 paths 读取，raw request 与独立 staging replay 对
  `daily/fund_daily`、`stock_basic/etf_basic` 做逐标的分类校验；即便先用无分类 paths 生成完整股票
  endpoint 证据，再交给权威 ETF store 发布也会 fail closed。
- M1 Green 9：snapshot 在 publication 前按 dataset/time/universe/coverage 合并字段投影，只绑定一次
  immutable object。公共 `load_snapshot()` 会重新解析 identity、自 catalog 定位 exact object、重算
  Parquet hash/metadata 并重放 absence freshness。`index_weight` 明确定义为 dated composition snapshot，
  `effective_to=effective_from` 且 `known_at=ingested_at`，不再伪造无限 membership 或历史公告时间。
- M1 Green 9 fresh gates：87 passed/1 credential-gated skipped；Ruff、无参数 strict mypy、Rust fmt/
  Clippy、26 tests/doc-tests、wheel/sdist 内容校验、`tm-core` package verify、JSON 与 diff check 全部
  通过。真实 Tushare smoke 因当前进程无 `TUSHARE_TOKEN` 仍明确 skip，不算网络验证；fresh review 9
  待执行。
- M1 fresh review 9：FAIL，0 Critical/3 High/1 Medium。同一 status object 内旧 True/new False
  revision 仍可生成并保留 absence proof；小写 ETF 配置未规范化而可走股票 endpoint；先用无分类
  paths 生成 ETF 的 stock-only `daily_basic` raw/staging，再交给权威 ETF store 仍可发布。Medium 为
  同 dataset 的窄/宽 coverage objects 重叠时 planner 拒绝，而 Green 9 只合并了完全相同 scope 的字段
  投影。独立反例位于 `/tmp/trademaster_m1_green9_review_repros.py`；M1 继续保持 active。
- M1 Green 10 Red：lowercase ETF、同 object 内 latest False status、unclassified producer 的
  unsupported ETF `daily_basic` 证据、subsumed request + overlapping coverage object 四个真实
  Parquet/DuckDB focused tests 均先按预期失败。
- M1 Green 10：新增共享 latest-status revision 解析器，以 `(instrument, venue:session)` 选择唯一
  最大 `known_at`，同时间不同内容 fail closed；portal 仅用 latest True 生成 proof，object store 也要求
  evidence object 的 latest row 为 True，snapshot freshness 再独立重放 exact SHA。ETF identifier 在
  config/path 边界大写规范化；raw request replay 对除 `instrument_master/daily_bars` 外的配置 ETF
  dataset 统一拒绝。planner 允许 revision objects 的 coverage keys 重叠，manifest 以无重复 key-set
  并集证明请求，且被宽请求完全包含的窄请求会规范化移除。
- M1 Green 10 fresh gates：加载仓库外 `0600` 凭证文件到单个测试进程后，Python 92 passed、真实
  Tushare `trade_cal` smoke 实际执行且通过，不再 skip；Ruff、无参数 strict mypy（20 source files）、
  Rust fmt/Clippy、26 tests/doc-tests、wheel/sdist 内容、`tm-core` package verify、canonical JSON 与
  diff check 全部通过。token 未进入代码、spec、日志、catalog、manifest 或包；fresh review 10 待执行。
- M1 fresh review 10：FAIL，0 Critical/3 High/0 Medium。object-level `known_at_max` 会把同对象内
  cutoff 前已经 eligible 的 correction 与另一条 cutoff 后记录一起跳过，普通 PIT query 返回旧值且
  snapshot/load/query 接受旧 suspension proof；空 upstream tuple 直接绕过 raw/staging replay 和
  authoritative ETF 分类；mutable endpoint 只刷新 terminal short page，较早 full page 的 provider
  correction 永久不可见。Green 9 的 3H/1M 均被独立确认关闭，真实 `trade_cal` 网络请求也确认产生
  1 raw + 1 staging Parquet；M1 继续保持 active。
- M1 Green 11 Red：新增真实 Parquet/DuckDB 反例，分别证明 mixed-`known_at` object 会遮蔽 cutoff
  前 correction、空 upstream 可绕过权威来源/ETF 分类、mutable endpoint 只刷新 terminal page；
  默认来源策略及每个缓存页刷新测试先按预期失败。
- M1 Green 11：canonical 默认使用 `tushare_only` 来源策略，空 upstream 立即 fail closed；只有测试
  fixture 或明确离线导入才能显式选择 `trusted_imports`。planner 逐对象读取 `known_at`，包含 cutoff
  前后两类行的对象不再整体跳过，而是拒绝进入 PIT object-set；全局 revision 审计按行过滤 cutoff。
  persisted snapshot loader 同样按其原始 cutoff 重放 latest status，使 mixed object 中已 eligible 的
  non-suspended correction 废止旧 proof。allow-empty/mutable endpoint 每轮从 offset 0 刷新全部页，并
  对同 ingestion timestamp 的每页内容歧义 fail closed。
- M1 Green 11 fresh gates：从仓库外 `0600` 凭据文件只向测试进程注入 token，Python 96 passed、
  0 skipped，真实 Tushare `trade_cal` smoke 实际执行；Ruff、无参数 strict mypy（20 source files）、
  Rust fmt/Clippy、26 tests/doc-tests、wheel/sdist 内容检查、`tm-core` package verify、canonical JSON
  与 diff check 全部通过；fresh review 11 仍为完成 M1 的必需门禁。
- M1 fresh review 11：FAIL，0 Critical/3 High/0 Medium。mixed-cutoff 检查位于 exact scope filter
  之后，因此 wider coverage/universe object 内的 eligible correction 可被忽略；exact-scope 多对象仍
  以 object-level 最大 `known_at` 选一个对象，会丢掉另一个对象中不同业务键的较新 revision；组合
  status 可把三 endpoint 拆进多个 staging，利用 staging union 洗白真实停牌。Green 11 原三项及真实
  Tushare smoke 均被独立确认关闭；反例保存在 `/tmp/trademaster_m1_green11_review_repros.py`。
- M1 Green 12 Red：wider coverage、wider universe、exact-scope per-key merge 和 split-status staging
  四个持久化 Parquet/DuckDB 反例先得到 4 failed；未用 mock 掩盖 object-store/catalog 行为。
- M1 Green 12：planner 保留所有 eligible exact-scope objects，DuckDB 按 primary key、`known_at` 与
  business revision 逐行选最新值，不再用 object max 选胜。随后扫描所有 canonical objects 的请求内
  eligible rows；若全局最新 revision 只存在于不能为该请求提供 coverage 的更宽 scope，则 snapshot/
  query fail closed。每个 `daily_limits_status` staging 必须独立包含并重放三个 provider endpoints，
  staging union 仅用于 canonical 内容相等性，不能补齐单个 staging 的缺失证据。
- M1 Green 12 fresh gates：四个 focused 反例 4 passed；审查员原始 `/tmp` 脚本重放显示两个 wider-scope
  攻击 fail closed、exact-scope 输出 `day2-corrected/day3-new`、split staging fail closed；带真实 token
  的 Python 全量为 100 passed/0 skipped，Ruff、strict mypy、Rust fmt/Clippy、26 tests/doc-tests、
  wheel/sdist 内容、`tm-core` package verify、canonical JSON、credential scan 与 diff check 全部通过；
  fresh review 12 仍待完成。
- M1 fresh review 12：FAIL，0 Critical/2 High/2 Medium/1 Low。persisted loader 没有重跑全局
  shadowed-revision 审计，后来登记的 cutoff-eligible correction 不会废止旧 manifest；raw provenance
  不证明分页链已到短终止页，可省略 `suspend_d` 第 2 页并发布错误 `suspended=False`。Medium 为全局
  revision conflict 未先按 row universe/scope 过滤，以及部分重叠 snapshot requests 重复共享 object。
  Low 为根规格 current-state 陈旧。Green 12 原三项均被独立确认关闭。
- M1 Green 13 Red：persisted correction、unterminated 5000-row status page、disjoint-universe conflict、
  partial-overlap snapshot 四个真实文件反例先得到 4 failed。
- M1 Green 13：`load_snapshot()` 现在按每个 coverage 的 exact object set 与 manifest cutoff 重跑
  global latest-row comparison；later catalogued correction 会明确废止旧 manifest。object store 按
  provider request digest 分组 raw pages，使用 endpoint 固定 limit 验证从 page 0 开始、index/cursor
  连续、中间页满载、末页严格小于 limit。global revision identity 与 latest-row scan 共享 row-level
  event/instrument/coverage/cutoff scope；不相关 universe 的冲突不阻断窄请求。同 dataset/universe 且
  coverage keys 相交的 requests 做传递 union，输出单个 coverage proof 与无重复 object manifest。
- M1 Green 13 fresh gates：4 个 focused 反例 4 passed；审查员原始脚本得到 strict/wider persisted
  manifest fail closed、unterminated status fail closed、partial-overlap PASS、disjoint query `[10.5]`；
  带真实 token 的 Python 全量 104 passed/0 skipped，Ruff、strict mypy、Rust fmt/Clippy、26 tests/
  doc-tests、wheel/sdist 内容、`tm-core` package verify、canonical JSON、credential scan 与 diff check
  全部通过；fresh review 13 待执行。
- M1 fresh review 13：FAIL，0 Critical/3 High/0 Medium。同 `known_at` 但不同 payload/source 的
  conflict 在 persisted loader 中可被 source 字典序遮蔽；mutable status raw pages 可把旧 ingestion
  page0 与新 ingestion terminal 拼链；重复 full-page fingerprint 在 provider 会拒绝但 strict store
  replay 会接受。Review 12 原 2H/2M 均被独立确认关闭。
- M1 Green 14：loader 对每个 persisted coverage 同时执行 global revision identity 与 shadowed-row
  两类审计；同 `known_at` 冲突与 fresh snapshot 行为一致。mutable `suspend_d/stock_st` chain 必须
  来自磁盘上同 request 的最新单一 ingestion observation，且 supplied page-index→fingerprint 映射与
  latest observation 完全一致；所有非空分页 fingerprint 唯一。三个 reviewer 反例先 3 failed 后
  3 passed，原 `/tmp/trademaster_m1_green13_review_repros.py` 输出 persisted conflict、cross-ingestion
  与 duplicate-page 均 fail closed，同时 same-fact/future 与 transitive-overlap controls PASS。
- M1 最终门禁：真实 token 单进程注入，Python 107 passed/0 skipped；Ruff、strict mypy、Rust fmt/
  Clippy、26 tests/doc-tests、wheel/sdist 内容、`tm-core` package verify、canonical JSON、credential
  scan 与 diff check 全部通过。根据用户 2026-08-11 决策，停止 M1 无限 adversarial review 循环；
  M2-M5 每个 milestone 只做一次有界轻量 review，完整 E2E 后在 M6 做一次全局高强度审查。

## 完成门禁

- 单元/属性测试覆盖请求规范化、coverage 区间集合和内容寻址。
- 使用 fake provider 的 DuckDB+Parquet 集成测试覆盖完整缓存、部分缺口、失败恢复与 PIT。
- 在无 `TUSHARE_TOKEN` 时真实 E2E 明确 skip；有凭据时执行最小日线请求且不记录 token。
- Ruff、strict mypy、pytest、Parquet schema 检查、DuckDB catalog migration 检查全部通过。
- 已知 review finding 必须关闭；全链路高强度复审在 M6 E2E 后统一执行。
