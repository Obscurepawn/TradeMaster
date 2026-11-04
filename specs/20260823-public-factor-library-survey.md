# Survey of public factor libraries and institutional research

- Status: done
- Owner: Codex
- Updated: 2026-08-23

## Goal and scope

The user asked which quantitative institutions publish factor libraries, with particular interest in WorldQuant Alpha101, single factors disclosed in Huatai Securities research, and Qlib Alpha158 and Alpha360. At the time, TradeMaster M2 implemented basic momentum, size and composite factors. The deliverable was a standalone Chinese-language survey of resources available on 2026-08-23, with sources, integration priorities and data-contract recommendations.

The survey distinguishes published formulas, open-source code, public factor-return series and commercial services that expose factor values under access restrictions. It covers Alpha101, Alpha191, Alpha158, Alpha360, the Huatai multifactor series, and representative Chinese and international resources. For each major library it records the publisher and maintainer, how counts are defined, market, frequency, input fields, code, license, data access and reproducibility. It addresses point-in-time (PIT) data, adjusted prices, neutralization, different versions sharing a name, and the distinction between an indicator and a tradable alpha.

## Evidence and requirements

Prefer original papers, official documentation, repositories and institutional publications. Identify secondary sources where primary material is unavailable. Treat names, return series, formula disclosure and paid API values as distinct levels of access. Attribute changing platform counts to their publisher and date them. Integration recommendations retain snapshot provenance, PIT availability (`known_at <= as_of`), execution at the next eligible event after a close-derived signal, and exact accounting at the ledger boundary.

## Acceptance criteria

- [x] AC-1: A standalone Markdown document covers at least the five requested material categories with source links.
- [x] AC-2: Include an overview table, major-library profiles, Chinese brokers and platforms, academic datasets, and exclusions.
- [x] AC-3: Document which formulas, implementations, data, licenses and access conditions are public, and what a reader can reproduce.
- [x] AC-4: Identify the sources and counting conventions for Alpha101, Alpha191, Alpha158 and Alpha360.
- [x] AC-5: Specify phased A-share integration priorities, minimum datasets and validation checks.
- [x] AC-6: Check the Markdown structure, extract links, and verify a sample of destinations.

## Tasks

| Task | Owner | Status | Evidence |
| --- | --- | --- | --- |
| Classify resources and public access | Codex | done | Four layers covering formulas, code, data and factor values. |
| Locate and verify primary sources | Codex | done | Verified the principal libraries and representative related resources. |
| Write the survey and integration recommendations | Codex | done | Output under `docs/research/`. |
| Verify structure, links and counts | Codex | done | Pandoc, link checks and counting conventions verified. |

## Decisions on 2026-08-23

- Classify resources by definition, code, data and service. A single ranking by factor count obscures those differences.
- Keep the survey in `docs/research/` as research evidence separate from runtime dependencies.
- Count Huatai's 2019 large-scale technical-factor report as an A-share replication of WorldQuant Alpha101. Counting its 101 factors again would overstate the number of distinct definitions.
- Include the Tushare 202-factor library launched on 2026-08-12 as a paid factor-value service. Its API at the survey date lacked versions and `known_at`. Formal PIT use requires a separate policy for recording them.

## Findings and evidence

The review covered repository rules, M2 boundaries, and the working tree. Public sources included WorldQuant, Microsoft Qlib, Guotai Junan, Huatai Securities, Tushare, DolphinDB, Peking University's China Anomalies and Factors, Open Source Asset Pricing, Global Factor Data, Fama/French, AQR, global-q and major commercial platforms.

At Qlib HEAD `79633dd9506ea689e5400dea0197717b5b3d74b7`, upstream code gave Alpha158 as `9 + 4 + 29×5 = 158` and Alpha360 as `6×60 = 360`. The 1,110-line Chinese survey includes 13 Huatai reports, Chinese academic benchmarks, domestic and international platforms, TradeMaster coverage, licensing, integration and validation. The review corrected the FactSet count and MyTT licensing language. For the Huatai historical-percentile analysis it retains both `reported_count=89` and the 86 combinations identifiable in the published chart.

No work remained in progress or blocked.

## Changed files

- `specs/20260823-public-factor-library-survey.md`
- `specs/INDEX.md`
- `docs/research/public-factor-library-survey.md`

## Verification

- `pandoc --from=gfm --to=html docs/research/public-factor-library-survey.md -o /dev/null` passed.
- The survey contained 1,110 lines, 4,347 words, 68,413 bytes, four matched code fences, 118 Markdown links, 67 unique external URLs and two local links. The scan found no outstanding TODOs, placeholders or conflict markers.
- Of the external URLs, 64 returned 2xx directly. Three sites (DOI, Quantpedia and S&P Global) returned 403/466 to automated requests but remained discoverable via search. The audit recorded zero failed or 404 destinations. The Peking University and JKP links returned 200 after a local certificate-chain fallback.
- Count checks covered the 29 rolling Alpha158 families, Alpha360's six 60-day loops, 19 WorldQuant information dependencies, Tushare 202, PKU 469, global-q 199, Huatai's aggregate 53 and its 86 visible historical-percentile combinations.
- Remote HEADs matched the survey for Qlib `79633dd...`, DolphinDBModules `43ace2c...`, OpenSourceAP `8db8924...` and JKP `6fb206b...`.
- Survey SHA-256: `884c5f961ea256862b938c94373f88665bc58dbed548173c9d424d5a7eabecff`. This deliverable comprised the survey and specification. No runtime tests, backtests or current profitability estimates were produced.

## Source limits and next step

There were no stable institutional direct links for Huatai's first four reports. The survey used a third-party index to locate them and checked the factor names against later official Huatai PDF review tables. Platform counts, API permissions, URLs and licenses are recorded as observed on 2026-08-23.

A future implementation should start a separate behavioral specification with the P0 operator definitions and data-unit contracts.
