# 公开因子库与机构因子研究调研

Status: done
Owner: Codex
Updated: 2026-08-23

## Goal

形成一份可独立阅读、来源可追溯的中文调研文档，系统梳理截至 2026-08-23
公开可获得的量化因子库、机构研报因子、开源实现、因子收益数据集与商业平台因子服务，
并给出面向 TradeMaster 的引入优先级和数据契约建议。

## Context

用户希望确认量化机构是否公开因子库，并特别要求覆盖 WorldQuant Alpha101、
华泰证券多因子研报披露的单因子、Qlib Alpha158 与 Alpha360。现有 TradeMaster M2
仅实现基础动量、市值和复合因子，尚无外部因子目录的系统选型依据。

## Scope

### In scope

- 区分“公开公式”“开源代码”“公开因子收益序列”“可调用但受限的商业因子值”四类供给。
- 覆盖 Alpha101、Alpha191、Alpha158、Alpha360、华泰多因子系列及其他代表性国际/国内资源。
- 为核心资源记录来源、维护方、数量口径、资产/市场、频率、输入字段、代码与许可证、
  数据可得性、PIT/复权/中性化风险和复现等级。
- 说明同名因子的版本差异、常见误称，以及“指标库不等于可交易 Alpha 库”的边界。
- 给出 TradeMaster 分阶段接入建议，但不修改运行时代码。

### Out of scope

- 不全文转载受版权保护的研报或一次性抄录数百条公式。
- 不承诺穷尽所有券商历史研报、私有模型或付费数据库。
- 不实现、回测或比较因子收益，不采购数据权限。

## Requirements and constraints

- 以官方论文、官方文档、官方代码仓库和机构原始页面为优先证据；仅在原始材料不可公开访问时使用可信二手来源并明确标注。
- 对“公开”采用分级口径，不能把只有名称、只有收益序列或需付费 API 的资源称为完全开源。
- 结论必须说明时间截面为 2026-08-23；对持续变化的平台数量使用“页面当前声称”并附核验日期。
- 接入建议遵守 TradeMaster 的 snapshot provenance、PIT、`known_at <= as_of`、
  收盘信号次一可交易事件执行和精确账本边界。

## Acceptance criteria

- [x] AC-1: 独立 Markdown 至少覆盖用户点名的五类材料，并给出可点击来源。
- [x] AC-2: 提供全景总表、核心库详解、国内券商/平台清单、学术因子数据集和排除项。
- [x] AC-3: 每个核心库明确公式、实现、数据、许可/访问和可复现性是否公开。
- [x] AC-4: 明确 Alpha101/Alpha191/Alpha158/Alpha360 的来源与数量口径，避免互相混同。
- [x] AC-5: 提供适配 A 股与 TradeMaster 的分阶段优先级、最小数据集和验证清单。
- [x] AC-6: 完成 Markdown 静态检查、引用链接提取检查和抽样可访问性核验。

## Plan

| Task | Owner | Status | Notes |
|---|---|---|---|
| 确定分类和公开性分级 | Codex | done | 公式、代码、数据、因子值四层 |
| 检索并核验一手来源 | Codex | done | 核心库及代表性扩展资源已核验 |
| 撰写调研与接入建议 | Codex | done | 输出至 `docs/research/` |
| 静态与链接验证 | Codex | done | Pandoc、结构、链接和数量口径均已核验 |

## Decisions

- 2026-08-23: 将“因子库”按定义/代码/数据/服务分层，避免用单一数量排行榜误导选型。
- 2026-08-23: 文档放在 `docs/research/`，仅作为研究证据，不进入运行时依赖。
- 2026-08-23: 华泰 2019 年海量技术因子报告视为 WorldQuant Alpha101 的 A 股复验，
  不作为独立的 101 个因子重复计数。
- 2026-08-23: 纳入 2026-08-12 新上线的 Tushare 202 因子库，但按付费因子值服务
  而非开源实现归类；其接口目前缺少版本和 `known_at` 字段，正式 PIT 运行前需另行治理。

## Current state

### Completed

- 已读取仓库规范、M2 因子边界与当前工作树状态。
- 已核验 WorldQuant、Microsoft Qlib、国泰君安、华泰证券、Tushare、DolphinDB、
  北大 China Anomalies and Factors、Open Source Asset Pricing、Global Factor Data、
  Fama/French、AQR、global-q 及主要商业平台的公开材料。
- 已从官方源码确认 Alpha158 的 `9 + 4 + 29×5 = 158` 与 Alpha360 的 `6×60 = 360`
  数量口径，并记录当前 Qlib HEAD `79633dd9506ea689e5400dea0197717b5b3d74b7`。
- 已完成 1,110 行独立中文调研，覆盖公开性分级、核心库、华泰 13 篇、A 股学术基准、
  国内外商业平台、TradeMaster 数据覆盖、接入路线、验证和许可边界。
- 已审计并修正 FactSet 当前数量口径和 MyTT 许可表述；华泰历史分位数保留
  `reported_count=89` 与图表可枚举 `86` 的差异，不静默补数。

### In progress

- none

### Blocked

- none

## Changed files

- `specs/20260823-public-factor-library-survey.md`
- `specs/INDEX.md`
- `docs/research/public-factor-library-survey.md`

## Verification

- `pandoc --from=gfm --to=html docs/research/public-factor-library-survey.md -o /dev/null`：PASS。
- 静态结构：1,110 行、4,347 words、68,413 bytes、4 个成对代码围栏、118 个 Markdown
  链接、67 个唯一外部 URL、2 个本地链接；无 TODO、占位符或冲突标记。
- 外链实测：64 个直接 2xx，3 个由站点返回 403/466（DOI、Quantpedia、S&P Global），
  0 个失败/404；北大和 JKP 在本机证书链回退后为 200。
- 数量断言：Alpha158 滚动族 29、Alpha360 六组 60 日循环、WQ 信息依赖项 19；
  Tushare 202、PKU 469、global-q 199、华泰汇总 53、华泰历史分位数可见组合 86 均自洽。
- 远端 HEAD 复核：Qlib `79633dd...`、DolphinDBModules `43ace2c...`、
  OpenSourceAP `8db8924...`、JKP `6fb206b...` 与文档记录一致。
- 文档 SHA-256：`884c5f961ea256862b938c94373f88665bc58dbed548173c9d424d5a7eabecff`。
- 本次仅新增研究文档和规格，没有运行代码测试或回测；验证不声称任何因子当前盈利。

## Risks and open questions

- 前四篇华泰报告没有稳定机构原始直链，文档明确将第三方目录降级为导航证据；其因子名由
  后续华泰官方 PDF 回顾表交叉核验。
- 平台因子数量、接口权限、许可证和 URL 会变化；所有动态口径都绑定 2026-08-23 检索日。
- 3 个商业/出版站点拒绝自动化链接请求，但网页由检索工具可发现；未将其误报为 404。

## Next step

本调研已完成。若决定实现某一批因子，应新建行为规格并从 P0 算子语义与数据单位契约开始。
