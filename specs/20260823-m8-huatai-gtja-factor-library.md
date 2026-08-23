# M8 华泰与国泰君安公开因子库接入

Status: completed
Owner: Codex
Updated: 2026-08-23

## 目标

依据 `docs/research/public-factor-library-survey.md`，把华泰2020汇总的53个A股风格因子和国泰君安Alpha191纳入TradeMaster因子治理体系。入库必须区分“已实现可物化”“存在本地变体”“待公式审计”“缺数据阻塞”和“许可待确认”，禁止只登记名称便声称因子已实现。

## 资源边界

### 华泰

- 主collection：2020《行业内因子选股实证分析》汇总的53因子。
- 另登记但不纳入53的：资金流50、一致预期19、财务质量51、历史分位数reported 89/enumerated 86、结构化风险模型。
- 资金流和一致预期当前为`blocked_data`；风险模型进入future `RiskModelDefinition`，不伪装成预测Alpha。

### 国泰君安

- 主collection：2017 Alpha1–Alpha191，先全部登记source candidates。
- 每条转为可执行definition前必须具备原文公式、规范AST、批准修正、算子语义版本和golden case。
- `SMA/MAX/MIN`歧义、benchmark/MKT/SMB/HML、回归、递归`SELF`等未解决项保持blocked；不一次性声称191全部实现。

### 许可证

- 券商公开研报属于`paper-disclosure`，不是OSS代码许可证。
- DolphinDB Apache-2.0模块只作为独立数值oracle；不引入DolphinDB Server运行时。
- 对外分发完整公式实现前保留`legal_review`状态和来源归属。

## 公共接口

- `FactorSourceRecord`：来源类型、标题、release/date/reference、证据hash、许可与重分发边界。
- `FactorCandidateRecord`：稳定identity、collection、category、公式/依赖、状态、blocked reasons、现有definition映射和semantic differences。
- `FactorCollectionDefinition`：有序成员、数量、source hash、collection类型和collection hash。
- `PublicFactorLibrary`：内容寻址JSON为权威，DuckDB只作source/collection/candidate发现；冲突与损坏fail closed。
- CLI：`tm-factor library-list`、`library-describe`、`library-status`。

## 状态枚举

- `implemented`：公式、数据、PIT和许可元数据齐全，可物化。
- `implemented_variant`：已有本地因子概念接近，但口径不完全等同。
- `ready`：公式语义清楚且当前数据足够，尚未实现。
- `blocked_data`：缺合法历史字段或PIT快照。
- `blocked_semantics`：原文/算子存在未裁决歧义。
- `legal_review`：技术可行但对外复制/分发边界未确认。
- `benchmark_only`、`risk_model_only`、`shadow_only`：进入对应非逐股Alpha合同。

## 里程碑

| Milestone | Owner | Status | Evidence |
|---|---|---|---|
| M8.1 来源、collection、candidate内容寻址合同 | Codex | completed | content-addressed JSON + DuckDB + fail-closed tests |
| M8.2 华泰53完整manifest与状态映射 | Codex | completed | 53; category 8/4/12/5/1/1/13/5/4; data 14/36/3 |
| M8.3 GTJA191完整candidate manifest与语义阻塞 | Codex | completed | IDs 001..191; data 146/40/4/1 |
| M8.4 当前可执行/变体映射与第一批实现决策 | Codex | completed | 11 exact implementations + 7 explicit variants |
| M8.5 CLI、持久化、文档、package与bounded review | Codex | completed | 176 tests + gates + wheel + one fresh review resolved |

## 验收标准

- [x] 华泰53有且仅有53个稳定成员，九类数量与调研文档一致。
- [x] GTJA Alpha001–Alpha191连续、唯一且全部有source/status；未审计公式不冒充implemented。
- [x] 资金流50与一致预期19明确`blocked_data`，风险模型明确`risk_model_only`。
- [x] source、candidate和collection全部内容寻址；同identity不同内容冲突，文件损坏fail closed。
- [x] 可以按collection/category/status/dependency查询，CLI输出稳定JSON。
- [x] 现有本地基本面因子仅以`implemented_variant`映射，并记录具体口径差异。
- [x] README/因子管理文档/AGENTS与代码同步，发行wheel隔离安装后CLI可用。
- [x] Python/Rust相关门禁通过并完成一次范围受限fresh review。

## Decisions

- 本里程碑先完成可信入库和状态治理；“已入库”不等于“已产生历史因子值”。
- 华泰与GTJA的完整公式实现按数据和语义分批升级candidate状态，不以数量替代正确性。
- Qlib Alpha158/360、WQ101、供应商shadow和因子收益benchmark另立collection，不混入本次两个券商collection。

## Current state

- 公开目录现有8个source、7个collection和460个candidate；本地实际factor root已完成
  `library-sync`。候选JSON是权威，`library/catalog.duckdb`只作发现。
- 统一managed registry现有原基本面11条，加华泰Size与10条首批GTJA，共22条definitions。
- 真实Tushare cache smoke从`strategy_requests`核验`daily/adj_factor`对象hash，使用2,378
  个session完成10条GTJA和一条华泰Size的Parquet物化与缓存命中。
- 当前十年日线仍仅覆盖446只历史入选标的并集，不能用于无偏全A IC结论。
- fresh review指出的顶层成员manifest、DuckDB回验、definition映射、明确HFQ输入、逐因子
  单位、dependency查询和Alpha015边界均已修正并由新增测试覆盖；按用户要求不再开启第二轮
  对抗式审查。

## TDD evidence

- Red（2026-08-23）：`uv run pytest -q tests/test_public_factor_library.py tests/test_factor_cli.py`
  在收集阶段因 `trademaster.factors.public_library` 尚不存在而失败。测试先冻结了华泰53
  的 9 类/53 条及 14/36/3 数据层级、GTJA191 的连续编号及 146/40/4/1 数据层级、
  7 个collection/460条candidate、内容寻址持久化、损坏fail-closed和CLI查询合同。
- Red（2026-08-23）：加入第一批可执行因子测试后，`tests/test_public_executable_factors.py`
  因 `trademaster.factors.public_factors` 尚不存在而在收集阶段失败；测试冻结一条华泰Size、
  十条低歧义GTJA公式、Tushare市值单位、warm-up和手算golden值。
- Green（2026-08-23）：`uv run pytest -q tests/test_public_executable_factors.py
  tests/test_public_factor_library.py tests/test_public_factor_real_cache_e2e.py
  tests/test_factor_cli.py`为`10 passed`；目标文件Ruff和严格Mypy均通过。
- Review Red（2026-08-23）：fresh-context review发现目录成员集/筛选仍可被DuckDB静默
  改写、GTJA复权输入仅以模糊external字段表达，并指出definition映射验证、dependency查询、
  单位与Alpha015缺失边界。相应测试先行后因新的`factors.builtin`统一registry尚不存在而
  在收集阶段失败；本轮只修正这些明确合同问题，不扩展第二轮审查。
- Review Green（2026-08-23）：目标四组测试为`13 passed`；删除candidate索引行、篡改
  discovery status、missing definition、无公式implemented、dependency filter、HFQ依赖/
  参数/单位和Alpha015缺失边界均有回归覆盖。

## Verification

- `TUSHARE_TOKEN="$(<...token-file...)" uv run pytest -q`：`176 passed`，包含实时Tushare
  网络合同测试及真实本地Tushare cache物化测试；token未输出或写入artifact。
- `uv run ruff check python tests`：PASS。
- `uv run mypy`：PASS，62个source files。
- `cargo fmt --all -- --check`、`cargo clippy --workspace --all-targets -- -D warnings`、
  `cargo test --workspace`：PASS。
- `uv build`：wheel和sdist成功；全新临时venv安装wheel后得到22个managed definitions、
  460个candidate，并由dependency查询得到10条implemented GTJA。
- Markdown fence/conflict/placeholder静态检查：PASS。
- 本地正式目录：`data/strategies/industry-fundamental-top5-10y/factors/library`，
  `library_sha256=c771e024b1dfeba5460e25c13bda616ea03ccf3b9f383831d637243678c77e6e`。

## Changed files

- `specs/INDEX.md`
- `specs/20260823-m8-huatai-gtja-factor-library.md`
- `python/trademaster/factors/public_library.py`
- `python/trademaster/factors/public_cn.py`
- `python/trademaster/factors/public_factors.py`
- `python/trademaster/factors/builtin.py`
- `python/trademaster/factors/__main__.py`
- `python/trademaster/factors/__init__.py`
- `tests/test_public_factor_library.py`
- `tests/test_public_executable_factors.py`
- `tests/test_public_factor_real_cache_e2e.py`
- `tests/test_factor_cli.py`
- `docs/public-factor-library.md`
- `docs/factor-management.md`
- `AGENTS.md`
- `python/trademaster/factors/AGENTS.md`

## Open questions

- none；默认按“完整目录入库、可执行项分批升级”的保守语义推进。
