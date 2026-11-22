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
