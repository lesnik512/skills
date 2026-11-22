import argparse
import json
from decimal import InvalidOperation
from html import escape
from pathlib import Path

from analysis_input import decimal_string, read_input, source_from_json
from build_report import annual_facts, calculate, render_edgar_report, render_report, write_report
from build_valuation import render_valuation, valuation_data
from import_filings import display_value, json_value
from mosaic_edgar import MosaicFacts, fetch_data
from refresh_filings import compare_refresh, render_refresh


def audit_section(inputs, valuation_issue):
    rows = []
    for key, record in sorted(inputs.records.items(), key=lambda entry: (
            inputs.accepted[entry[0]] is not None, entry[0][2], entry[0][0])):
        metric, start, end = key
        dates = f"{start} to {end}" if start else end
        evidence = []
        for mode, observations in record["observations"].items():
            for item in observations:
                raw = item["value"]
                if raw is None:
                    label = "Open source"
                elif record["unit"] == f"{inputs.currency}/share":
                    label = f"{decimal_string(raw):,f} {record['unit']}"
                else:
                    label = display_value(decimal_string(raw), record["unit"])
                source = source_from_json(item["source"])
                evidence.append(f"{escape(mode)} {source.link(label)} {escape(item.get('issue', ''))}")
        decision = record.get("decision")
        issues = [escape(issue) for issue in record["issues"]]
        if decision is not None:
            source = source_from_json(decision["source"])
            issues.append(f'Accepted after review: {source.link("selected observation")} · {escape(decision["reason"])}')
        rows.append(f'<tr><th scope="row">{escape(record.get("label", metric))}</th><td>{dates}</td>'
                    f'<td>{escape(record["status"])}</td><td>{"<br>".join(evidence) or "—"}</td>'
                    f'<td>{"<br>".join(issues)}</td></tr>')

    open_count = sum(figure is None for figure in inputs.accepted.values())
    issues = "".join(f"<li>{escape(issue)}</li>" for issue in inputs.payload.get("document_issues", []))
    checks = " · ".join(f'FY{int(check["year"])} {escape(check["status"])}' for check in inputs.balance_checks)
    source_note = escape(inputs.payload.get("source_note", ""))
    return f"""
<section aria-label="Filing evidence and review">
{f'<p><strong>Valuation needs input.</strong> {escape(valuation_issue)}</p>' if valuation_issue else ""}
<details{" open" if inputs.needs_review else ""}>
<summary>Filing evidence and review · {open_count} unresolved figures</summary>
{f'<p>{source_note}</p>' if source_note else ""}
{f'<ul>{issues}</ul>' if issues else ""}
{f'<p>Balance sheet checks · {checks}</p>' if checks else ""}
<div class="table-wrap" tabindex="0" role="region" aria-label="Reported figures and review flags">
<table><thead><tr><th scope="col">Figure</th><th scope="col">Period</th><th scope="col">Status</th>
<th scope="col">Source amounts</th><th scope="col">Review notes</th></tr></thead>
<tbody>{"".join(rows)}</tbody></table></div>
</details>
</section>
"""


def main():
    parser = argparse.ArgumentParser(description="Build a source-linked company report from versioned analysis input.")
    parser.add_argument("input", type=Path)
    parser.add_argument("--report", type=Path, required=True)
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument("--valuation", type=Path, help="Also generate an editable DCF when inputs are available")
    modes.add_argument("--refresh-edgar", metavar="EMAIL", help="Compare the packaged Mosaic baseline with EDGAR")
    modes.add_argument("--refresh-from", type=Path, metavar="CANDIDATE", help="Compare the baseline with a new filing import")
    parser.add_argument("--refresh-json", type=Path, help="Also save the filing comparison as JSON")
    args = parser.parse_args()
    if args.refresh_json and not args.refresh_from:
        parser.error("--refresh-json requires --refresh-from")

    paths = [path.resolve() for path in (args.input, args.report, args.valuation, args.refresh_from, args.refresh_json)
             if path is not None]
    if len(paths) != len(set(paths)):
        parser.error("Choose distinct paths for the input and reports")

    try:
        inputs = read_input(args.input)
        if args.refresh_from:
            result = compare_refresh(inputs, read_input(args.refresh_from))
            write_report(args.report, render_refresh(result))
            if args.refresh_json:
                write_report(args.refresh_json, json.dumps(result, default=json_value, ensure_ascii=False, indent=2) + "\n")
            return 2 if result["needs_review"] else 0
        facts = annual_facts(inputs)
        company = {**inputs.issuer, "name": inputs.company}
        company["years"] = company.get("years") or sorted(map(int, inputs.periods))
        if args.refresh_edgar:
            if company.get("id") != "mosaic" or inputs.needs_review:
                parser.error("The Mosaic EDGAR comparison requires a resolved Mosaic baseline")
            report = render_edgar_report(facts, company, MosaicFacts(*fetch_data(args.refresh_edgar)))
            write_report(args.report, report)
            return 0

        basis = company.get("gross_margin_basis", "revenue")
        results = {year: calculate(facts, year, gross_margin_basis=basis) for year in company["years"]}
        model = None
        valuation_issue = None
        if args.valuation is not None:
            try:
                model = render_valuation(valuation_data(inputs, facts))
            except (KeyError, TypeError, ValueError, InvalidOperation) as error:
                valuation_issue = str(error)
        write_report(args.report, render_report(results, company, audit_section(inputs, valuation_issue)))
        if model is not None:
            write_report(args.valuation, model)
        return 2 if inputs.needs_review or valuation_issue else 0
    except (KeyError, TypeError, ValueError, OSError, StopIteration, InvalidOperation) as error:
        parser.exit(1, f"Company analysis failed: {error}\n")


if __name__ == "__main__":
    raise SystemExit(main())
