---
name: analyze-company
description: Review listed-company XBRL and PDF filings, build source-linked fundamental reports, or value a nonfinancial company with an editable DCF. Use for filing comparisons, financial ratios, earnings normalization, market-implied growth and margin, location expansion, recovery paths, and refinancing checks.
---

# Analyze a company

1. Identify the issuer, share class, reporting currency, fiscal dates, and analysis date. Choose filings available on that date. Use consolidated statements, notes and dated quotes from official sources when available.
2. For filing import, saved decision replay or refresh, follow [filing import and comparison](references/filings.md). Inspect the statements before mapping an unfamiliar issuer. Check whether each XBRL tag and PDF line measures the same thing. Keep competing figures visible and use the notes to explain a difference when possible.
3. For a report or valuation, follow [analysis inputs](references/analysis-input.md). Run the packaged scripts on the selected input. Review missing or conflicting amounts before using them in calculations. Leave a figure unresolved when the evidence is insufficient. Record an approved decision against the original observation with the reason.
4. For a DCF, choose operating drivers that fit the business. Label forecasts, normalization adjustments and financing estimates as assumptions and explain their basis. Use the pilot adapters for their named issuers. If the provided model does not fit the business or lacks essential inputs, deliver the available financial report and explain the limit.
5. Deliver the requested local outputs and summarize the major findings in plain language. Include the financial and quote dates, key assumptions, and any review flags that affect the conclusion. Keep the input JSON with the reports so the analysis can be reproduced.

Run scripts from this skill folder. Write generated outputs to the user's working directory. The bundled examples are dated inputs for reproducing the pilots.
