# Survey of Public Factor Libraries and Institutional Research

**Status:** Completed | **Date:** 2026-08-23 | **Scope:** Taxonomy, provenance, licensing, and point-in-time integration survey of public factor libraries (`docs/research/public-factor-library-survey.md`) | **Followed-by:** [M8: Huatai Style Factors and Guotai Junan Alpha191 Catalog](20260823-m8-huatai-gtja-factor-library.md)

## Motivation and Scope

Prior to expanding TradeMaster beyond M2's baseline momentum, size, and composite factors, this specification commissioned a comprehensive reference survey (`docs/research/public-factor-library-survey.md`, authored in Chinese as a read-only research reference) cataloging institutional and academic factor disclosures as of `2026-08-23` and establishing data, provenance, and licensing rules for integrating public factors into TradeMaster.

Rather than ranking platforms by headline factor count, the survey classifies public resources across four distinct access tiers:

1. **Published mathematical formulas** (such as broker research reports and academic papers).
2. **Open-source executable implementations** (with explicit software licenses and versioned repositories).
3. **Public factor-return benchmark series** (portfolio return time series without stock-level exposures).
4. **Access-restricted commercial factor APIs** (paid services returning precomputed factor values).

## Covered Libraries and Editorial Decisions

The 1,110-line survey profiles WorldQuant Alpha101 (documenting its 19 input-data dependencies), Guotai Junan Alpha191, Microsoft Qlib Alpha158 (`9 + 4 + 29 × 5 = 158`) and Alpha360 (`6 × 60 = 360`, verified at Qlib commit `79633dd9506ea689e5400dea0197717b5b3d74b7`), 13 Huatai Securities quantitative research reports, Tushare's 202-factor service, DolphinDB (`43ace2c...`), Peking University's China Anomalies and Factors (`469` factors), Open Source Asset Pricing (`8db8924...`), Global Factor Data / JKP (`6fb206b...`), Fama/French, AQR, `global-q` (`199` factors), and major commercial platforms.

Key classification and governance decisions established for downstream milestones ([M8](20260823-m8-huatai-gtja-factor-library.md)–[M9](20260823-m9-full-a-20y-factor-evaluation-quality.md)):

- **Research-only boundary:** Keep `docs/research/public-factor-library-survey.md` separate from runtime package dependencies; executable implementations require standalone specifications with operator definitions and unit contracts.
- **Deduplicated counting:** Treat Huatai's 2019 technical-factor report as an A-share empirical replication of WorldQuant Alpha101 rather than double-counting its 101 formulas; retain Huatai's 53-factor cross-industry summary and record both `reported_count=89` and the 86 chart-identifiable combinations in Huatai's historical-percentile study. Where direct institutional URLs were unavailable for Huatai's earliest four reports, factor names were cross-checked against later official Huatai PDF review tables.
- **Commercial factor-API provenance:** Classify Tushare's 202-factor API (launched `2026-08-12`) as a commercial factor-value service; because the endpoint lacked factor version identifiers and `known_at` availability timestamps at the survey date, formal point-in-time (PIT) backtests cannot consume its values without an explicit ingestion-time provenance policy.
- **Execution causality alignment:** Integration priorities preserve TradeMaster's core invariants: snapshot provenance, PIT cutoff filtering (`known_at <= as_of`), next-eligible-event execution (`T+1`) for close-derived signals, and exact ledger accounting.

## Changed Files

- `specs/INDEX.md`, `specs/20260823-public-factor-library-survey.md`
- `docs/research/public-factor-library-survey.md`

## Verification Record

- **Document structure & integrity:** `pandoc --from=gfm --to=html docs/research/public-factor-library-survey.md -o /dev/null` passed with zero warnings across 1,110 lines, 4,347 words, 68,413 bytes, 4 matched code fences, and zero placeholders or conflict markers (`SHA-256: 884c5f961ea256862b938c94373f88665bc58dbed548173c9d424d5a7eabecff`).
- **Source & URL audit:** Verified 118 Markdown links (2 local paths and 67 unique external URLs: 64 returned HTTP `2xx` directly—including PKU and JKP after local certificate-chain resolution—while DOI, Quantpedia, and S&P Global returned automated-client `403`/`466` codes and were confirmed via search with zero `404` destinations). Review passes also corrected FactSet factor-count wording and `MyTT` license attribution.
