# 开源仓库发布整理

Status: active
Owner: Codex
Updated: 2026-08-24

## 目标

把当前本地TradeMaster实现整理为可安全发布到GitHub `main`的开源仓库基线：保留源码、测试、
公共架构文档和开发规格，排除数据、回测产物、外部调研仓库、密钥、虚拟环境和构建缓存；提供
符合常见开源项目结构的README与Apache-2.0许可证，并在验证后提交和推送。

## 范围

- 重构根`.gitignore`，按数据/产物、语言工具链、测试缓存、IDE/OS和密钥分类；
- 新增根`README.md`，覆盖定位、能力、架构、目录、安装、配置、使用、验证、限制和License；
- README嵌入两张经高分辨率验收的可编辑SVG：系统架构图与端到端数据流图；
- 外部依赖章节明确当前唯一数据源为Tushare，并区分本地运行依赖与外部服务；
- 新增标准Apache License 2.0全文`LICENSE`，与Rust workspace现有声明保持一致；
- 补齐Python package的README/license metadata；
- 审计待提交文件、忽略效果、密钥和大文件；
- 创建一个非force提交并推送到`origin/main`。

## 非目标

- 不提交`research/`候选仓库、`docs/research/`内部调研证据、Tushare数据、回测artifact或构建产物；
- 不重写Git历史、不force push、不删除本地数据或产物；
- 不改变回测、因子或交易运行时行为。

## 不变量

- Tushare token和任何`.env`/credential文件不得进入Git index或提交历史；
- `Cargo.lock`与`uv.lock`作为可复现应用锁文件纳入版本控制；
- `AGENTS.md`、`specs/`、源码、schemas、测试和公开docs纳入版本控制；
- push前必须确认本地`main`基于最新`origin/main`，提交内容不含超大文件或被忽略数据；
- 远端更新使用普通fast-forward push，禁止force。

## 任务

| Task | Owner | Status | Evidence |
|---|---|---|---|
| 审计live Git状态、license与公开边界 | Codex | completed | main/origin均为fd656a0；仅.gitignore已跟踪 |
| 整理.gitignore并验证ignore contracts | Codex | completed | 大数据/产物/研究仓库/缓存均命中规则 |
| 编写README/LICENSE及package metadata | Codex | completed | README、2 SVG、Apache-2.0、wheel metadata |
| 运行安全、文档、构建和测试门禁 | Codex | completed | 248 Python + Rust/static/package/docs/security PASS |
| 提交并push origin/main | Codex | in_progress | origin/main已fetch且0 ahead/0 behind |

## 验收标准

- [x] `git status --short`只显示应公开的源码、测试、公共文档和仓库元数据；
- [x] data/artifacts/research/docs-research/dist/target/venv/cache/secrets均被明确忽略；
- [x] README中的命令、链接、架构和限制与live代码一致；
- [x] 两张README SVG通过结构、渲染和局部裁图检查，缩放后文字与连线仍可读；
- [x] Python/Rust package metadata均声明Apache-2.0且LICENSE全文存在；
- [x] secret scan、大文件审计、Markdown链接检查、build/test/lint通过；
- [ ] 本地`main`提交后工作树干净，`origin/main`指向相同提交。

## Decisions

- License沿用`Cargo.toml`已经声明的Apache-2.0，避免双重或冲突许可。
- 公开文档包含`docs/architecture`和通用使用文档；带本机路径且面向内部证据的
  `docs/research/`继续忽略。
- 生成数据和报告不入Git；README提供可重复生成命令和本地输出目录约定。

## Changed files

- `specs/INDEX.md`
- `specs/20260823-open-source-repository-publication.md`
- `.gitignore`
- `README.md`
- `LICENSE`
- `pyproject.toml`
- `Cargo.toml`
- `crates/tm-core/Cargo.toml`
- `crates/tm-engine/Cargo.toml`
- `crates/tm-runner/Cargo.toml`
- `docs/architecture/trademaster-overview.svg`
- `docs/architecture/trademaster-data-flow.svg`
- `docs/full-a-factor-research.md`
- `docs/reviews/20260823-m9-full-a-quality-review.md`
- `python/trademaster/strategies/industry_fundamental/README.md`

## Current state

- 当前`main`与`origin/main`均指向清空后的`fd656a0`，远端树只有`.gitignore`。
- 本地完整实现均未跟踪；`data/`约3GB、`artifacts/`约8.1GB、`research/`约5.7GB、
  `target/`约4.8GB、`.venv/`约504MB，必须保持在Git之外。
- 当前公开候选155个文件，最大为`uv.lock`约245KB；未发现候选文件包含本地Tushare token。

## Verification evidence

- README：Pandoc GFM解析PASS；44个候选Markdown的相对链接全部存在且未指向ignored文件。
- SVG：两图`validate-svg.sh`/`rsvg-convert` PASS；4000px整图与11个局部裁图完成视觉复核。
- License/package：LICENSE与系统Apache-2.0全文`diff -w`一致；wheel metadata为
  `License-Expression: Apache-2.0`并包含LICENSE，sdist包含README/LICENSE。
- Security/repository：精确Tushare token与常见GitHub/AWS/private-key模式扫描PASS；155个候选
  文件最大约245KB；本地大目录均由`git check-ignore -v`证明。
- Python：真实token环境`248 passed in 87.44s`；Ruff、strict Mypy 44 source files、
  compileall PASS。
- Rust：fmt、Clippy `-D warnings`、workspace tests与doc tests PASS。
- Package：`uv build` PASS；隔离venv重装wheel后两个CLI `--help` PASS。
- Remote：`git fetch origin main`后本地与远端为`0/0`，均指向`fd656a0`。

## Open questions

- 无；用户已明确授权整理、提交并push到`main`。
