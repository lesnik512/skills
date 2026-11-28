---
name: analyze-company
description: Forecast and backtest listed-company share prices using dated quotes and source-linked financials. Review XBRL and PDF filings, build fundamental reports, and value nonfinancial companies with editable DCF, earnings recovery, growth, expansion and refinancing scenarios.
---

# Analyze a company

1. Identify the issuer, share class, reporting currency, fiscal dates, and analysis date. Choose filings available on that date. Use consolidated statements, notes and dated quotes from official sources when available.
2. For filing import, saved decision replay or refresh, follow [filing import and comparison](references/filings.md). Inspect the statements before mapping an unfamiliar issuer. Check whether each XBRL tag and PDF line measures the same thing. Keep competing figures visible and use the notes to explain a difference when possible.
3. For a dated stock-price forecast and historical benchmark, follow [price forecasting](references/price-forecast.md). Build the forecast from information available at each starting quote. Show the target date, editable scenarios, error against a flat-price forecast, and direction hits.
4. For a fundamental report or DCF, follow [analysis inputs](references/analysis-input.md). Review missing or conflicting amounts before using them in calculations. Leave a figure unresolved when the evidence is insufficient. Record an approved decision against the original observation with the reason. Use issuer-specific adapters for their named companies. Choose operating drivers that fit the business and label forecast, normalization and financing assumptions.
5. Deliver the requested local outputs and summarize the major findings in plain language. Include the financial and quote dates, key assumptions, and any review flags that affect the conclusion. Keep the input JSON with the reports so the analysis can be reproduced. If essential inputs are unavailable, deliver the supported analysis and identify the gap.

Run scripts from this skill folder. Write generated outputs to the user's working directory. The bundled examples are dated inputs for reproducing the pilots.
