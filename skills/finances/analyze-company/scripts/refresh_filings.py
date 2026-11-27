from collections import Counter
from html import escape

from analysis_input import decimal_string, source_from_json, validate_comparison_scope
from import_filings import render_reopened_evidence


STATUSES = ("Changed", "Unresolved", "New", "Newly verified", "Confirmed", "Outside refresh")


def compare_refresh(baseline, candidate):
    validate_comparison_scope(baseline, company=candidate.company, issuer=candidate.issuer,
                              currency=candidate.currency, periods=candidate.periods)

    rows = []
    for key in baseline.records.keys() | candidate.records.keys():
        metric, start, end = key
        old, new = baseline.records.get(key), candidate.records.get(key)
        before, after = baseline.accepted.get(key), candidate.accepted.get(key)
        explanation = ""
        delta = None
        if new is None:
            status = "Outside refresh"
            explanation = "This figure was outside the candidate input's coverage."
        elif old is not None and old["unit"] != new["unit"]:
            status = "Unresolved"
            explanation = "Units differ for the same metric and period. Check the mappings."
        elif after is None:
            status = "Unresolved"
        elif old is None:
            status = "New"
        elif before is None:
            status = "Newly verified"
            explanation = "The baseline has no accepted amount for this period. Review its earlier evidence."
        elif before.value != after.value:
            status = "Changed"
            delta = after.value - before.value
            explanation = "The amounts differ for the same period. Check the filing before adopting the change."
        else:
            status = "Confirmed"

        row = {
            "metric": metric, "start": start, "end": end, "status": status,
            "requires_review": status in {"Changed", "Unresolved", "Newly verified"} or (new is None and before is None),
            "difference": str(delta) if delta is not None else None,
            "explanation": explanation, "baseline": old, "candidate": new,
            "baseline_value": str(before.value) if before is not None else None,
            "candidate_value": str(after.value) if after is not None else None,
        }
        rows.append(row)

    rows.sort(key=lambda row: (STATUSES.index(row["status"]), not row["requires_review"],
                               row["end"], row["metric"], row["start"] or ""))
    issues = {"baseline": baseline.payload.get("document_issues", []),
              "candidate": candidate.payload.get("document_issues", [])}
    checks = {"baseline": baseline.balance_checks, "candidate": candidate.balance_checks}
    counts = Counter(row["status"] for row in rows)
    return {"company": candidate.company, "currency": candidate.currency, "rows": rows,
            "summary": {status: counts[status] for status in STATUSES},
            "document_issues": issues, "balance_checks": checks,
            "needs_review": (any(row["requires_review"] for row in rows) or baseline.needs_review or candidate.needs_review)}


def amount(value, unit):
    return f"{decimal_string(value):,f} {unit}"


def evidence_cell(record, selected):
    if record is None:
        return "—"
    unit = record["unit"]
    decision = record.get("decision")
    lines = []
    if selected is not None:
        source = decision["source"] if decision is not None else next(
            item["source"] for items in record["observations"].values() for item in items
            if item["value"] is not None and not item.get("issue"))
        lines.append(f'<strong>{source_from_json(source).link(amount(selected, unit))}</strong>')
    else:
        lines.append("No accepted amount")
    for mode, observations in record["observations"].items():
        for observation in observations:
            raw = observation["value"]
            source = source_from_json(observation["source"])
            label = amount(raw, unit) if raw is not None else "Open source"
            issue = escape(observation.get("issue", ""))
            lines.append(f'<small>FY{source.filing_year} {escape(mode)} {source.link(label)} {issue}</small>')
    lines.extend(f"<small>{escape(issue)}</small>" for issue in record["issues"])
    if decision is not None:
        source = source_from_json(decision["source"])
        lines.append(f'<small>{source.link("Review decision")} · {escape(decision["reason"])}</small>')
    return "".join(lines) + render_reopened_evidence(record)


def render_refresh(result):
    def table_row(row):
        record = row["candidate"] or row["baseline"]
        start, end = row["start"], row["end"]
        dates = f"{start} to {end}" if start else end
        difference = row["difference"]
        delta = f'<small>Change: {escape(amount(difference, record["unit"]))}</small>' if difference is not None else ""
        note = f'<small>{escape(row["explanation"])}</small>' if row["explanation"] else ""
        css = ' class="review"' if row["requires_review"] else ""
        return (f'<tr{css}><th scope="row">{escape(record.get("label", row["metric"]))}</th>'
                f'<td>{dates}</td><td>{row["status"]}{delta}{note}</td>'
                f'<td>{evidence_cell(row["baseline"], row["baseline_value"])}</td>'
                f'<td>{evidence_cell(row["candidate"], row["candidate_value"])}</td></tr>')

    name = escape(result["company"])
    summary = " · ".join(f'{count} {status.lower()}' for status, count in result["summary"].items())
    issues = [f'{side.title()}: {issue}' for side, values in result["document_issues"].items() for issue in values]
    issues.extend(f'{side.title()} FY{check["year"]} balance sheet check: {check["status"]}'
                  for side, checks in result["balance_checks"].items() for check in checks if check["status"] != "Pass")
    notices = "".join(f"<li>{escape(issue)}</li>" for issue in issues)
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>{name} filing refresh</title>
<style>
body {{ margin: 0; background: #f3f6f4; color: #142724; font: 16px/1.5 system-ui, sans-serif }}
main {{ max-width: 82rem; margin: auto; padding: clamp(1rem, 4vw, 3rem) }}
a {{ color: #145749 }} small {{ display: block; margin-top: .25rem }} .scroll {{ overflow-x: auto }}
table {{ width: 100%; border-collapse: collapse; background: white; font-variant-numeric: tabular-nums }}
th, td {{ text-align: left; padding: .6rem .85rem; vertical-align: top; border-bottom: 1px solid #dfe8e3 }}
thead {{ background: #e8f0ec }} .review {{ background: #fff2d8 }}
</style></head><body><main>
<h1>{name} filing refresh</h1><p>{summary}.</p>
<p>Changes compare the same metric and fiscal dates. Shaded rows need review before adopting the new evidence.</p>
{f'<ul aria-label="Document issues">{notices}</ul>' if notices else ''}
<div class="scroll" role="region" aria-label="Figures and filing links" tabindex="0">
<table><thead><tr><th scope="col">Figure</th><th scope="col">Period</th><th scope="col">Result</th>
<th scope="col">Prior filing evidence</th><th scope="col">New filing evidence</th></tr></thead>
<tbody>{''.join(map(table_row, result["rows"]))}</tbody></table></div></main></body></html>
"""
