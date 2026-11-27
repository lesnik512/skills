# Filing import

Start from the [Microsoft mapping](../profiles/microsoft.json). Set the issuer and currency, fiscal periods, exact filings, and metric definitions. Configure XBRL tags for equivalent consolidated facts only. For PDFs, specify each table's page, heading, unit label, fiscal column order, scale and row labels. Use `pdf_after` and `pdf_before` for repeated labels and a checksum when pinning a layout. The [PhosAgro mapping](../profiles/phosagro-2025.json) shows same-row units, a note-reference column, and year labels with a suffix. Mark capex as a signed outflow. Inspect the page whenever a layout or label differs.

The [Costco mapping](../profiles/costco.json) uses `strip_dot_leaders` for printed rows with dotted separators, `end_heading` to stop at the next notes table, and `scale_page` when the units are printed at the beginning of the notes. A metric with an explicit `sources` array requires those formats. Use `["PDF"]` for defined amounts that can be verified from the report but have no equivalent in the selected Company Facts data. These amounts appear as **Single source**.

Run from the skill folder. Replace paths with the files under review.

```sh
uv run --no-project --with pymupdf python scripts/import_filings.py profiles/microsoft.json \
  --xbrl companyfacts.json --pdf 2025=2025.pdf --pdf 2026=2026.pdf \
  --output filing-review.html --json-output analysis-input.json
```

Pass `--xbrl auto --edgar-contact EMAIL` for the live SEC Company Facts endpoint when the user has supplied a contact. Pass `--pdf YEAR=auto` to use a PDF URL configured in the profile. For a downloaded XBRL instance, use `--instance YEAR=FILE` and specify the exact taxonomy namespaces. For Company Facts compiled from several filings, pin the accession and compare the relevant dates. Keep custom tags and segment facts separate from consolidated equivalents.

Image-only PDFs and unrecognized table layouts go to review. Inspect the page and supply verifiable evidence with the correct page link and units before using those figures. A blank cell stays missing. A figure supplied in one configured format is marked **Single source**. If two supplied formats disagree, both candidates are retained. Exit code 2 means the files were written and some items require review. The exported JSON can go directly to [the analysis command](analysis-input.md).

### Replay saved review decisions

Add `--replay-decisions prior-input.json` to the import command. Use a [shared analysis input](analysis-input.md) with the saved decisions. Supply all filings that supported those decisions, including earlier versions. Use separate paths for the new HTML and JSON.

A decision carries forward when the same metric, dates, unit and full set of source observations are reproduced. A changed, added or missing observation reopens the decision. The output keeps the fresh evidence and a separate record of the previous selection, its rationale and both filing links. A reopened decision stays open on subsequent imports until reviewed again. Decisions outside the mapped coverage stay in the saved input.

The importer rechecks assets against liabilities plus equity using the selected amounts. When the equation fails or lacks a required total, decisions on those balance sheet totals reopen. Any other unresolved figures keep their review flags.

#### DOM.RF example

The [DOM.RF profile](../profiles/domrf.json) pins the FY2024 and FY2025 summarized consolidated statements. It imports net interest income and net profit for FY2023–FY2025. The [saved decision](../examples/domrf.json) selects the revised FY2024 net interest income of ₽122,879m. Note 38 reconciles the ₽907m difference in the comparative. [FY2025 Note 38](https://rcsd.enforce.spb.ru/data/7729355614/2025.pdf#page=109)

```sh
uv run --no-project --python 3.10 --with pymupdf python scripts/import_filings.py profiles/domrf.json \
  --pdf 2024=auto --pdf 2025=auto --replay-decisions examples/domrf.json \
  --output domrf-review.html --json-output domrf-input.json
```

The import accepts six figures and shows both FY2024 observations and the selected amount. To use saved PDFs, replace each `auto` with the corresponding local filename. The profile verifies both file checksums.

## Refresh an existing analysis

Import the latest filing into a separate candidate JSON. Pin its accession, dates and PDF layout in the profile. Set `years` on a metric when the filing covers only a subset of the profile's fiscal years, for example two balance sheet dates alongside three income statement years. An omitted period is outside the refresh. Keep records for expected but unavailable amounts so the comparison flags them.

```sh
python scripts/analyze_company.py prior-input.json --refresh-from candidate.json \
  --report filing-refresh.html --refresh-json filing-refresh.json
```

The comparison joins the same metric and exact period. It shows new figures, changes to previously accepted figures, newly available evidence for prior gaps, and unresolved items. Both input files are read-only. The JSON comparison retains each side's observations and any decisions. Exit code 2 signals changes or remaining review items. A difference on the same dates warrants a review of definitions, signs, units, and filing notes before adoption. Year-over-year movements appear in the financial report.

Review the comparison before preparing a new analysis input. Retain prior verified periods and sources as needed. Keep unresolved observations and their review flags. Record an evidence-linked decision for any flagged amount you use. For a refreshed valuation, set the annual base and filing date, add a quote available after publication, and recheck the equity bridge and assumptions against the new filing.

The [Costco FY2026 profile](../profiles/costco-fy2026.json) pins only the FY2026 10-K. Compare its import with [the FY2025 input](../examples/costco.json). For the Costco review, confirm short-term bank borrowings from Note 4, shares outstanding from the cover, and warehouse counts and openings from Items 1 and 7. These disclosures sit outside the mapped statement tables.
