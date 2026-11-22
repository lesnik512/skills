import argparse
import json
import re
from bisect import bisect_right
from collections import defaultdict
from dataclasses import asdict, dataclass
from datetime import date
from decimal import Decimal, InvalidOperation
from hashlib import sha256
from html import escape
from itertools import pairwise
from pathlib import Path
from urllib.parse import urlsplit
from urllib.request import Request, urlopen
from xml.etree import ElementTree

from build_report import Source, write_report


XBRL_INSTANCE = "{http://www.xbrl.org/2003/instance}"
MODES = ("XBRL", "PDF")


@dataclass(frozen=True)
class Observation:
    value: Decimal | None
    source: Source
    issue: str = ""


def read_json(path):
    with path.open(encoding="utf-8") as stream:
        return json.load(stream, parse_float=Decimal)


def period_start(period, rule):
    return period["start"] if rule["kind"] == "flow" else None


def decimal_value(value):
    if isinstance(value, bool):
        raise ValueError("Expected a numeric amount")
    number = Decimal(value)
    if not number.is_finite():
        raise ValueError("Non-finite amount")
    return number


def xbrl_source(year, filing, tag):
    return Source(int(year), tag, filing["url"], f'FY{year} {filing["form"]} · {tag}')


def company_facts(profile, data):
    if int(data["cik"]) != int(profile["cik"]):
        raise ValueError("Company Facts CIK differs from the profile")
    observations = defaultdict(list)
    facts = data["facts"]
    filings = {item["xbrl"]["accession"]: (year, item["xbrl"])
               for year, item in profile["filings"].items() if "xbrl" in item}
    for metric, rule in profile["metrics"].items():
        for qualified_tag in rule.get("tags", []):
            taxonomy, tag = qualified_tag.split(":", 1)
            units = facts.get(taxonomy, {}).get(tag, {}).get("units", {})
            for fact in units.get(rule.get("unit", profile["currency"]), []):
                if fact["accn"] not in filings:
                    continue
                filing_year, filing = filings[fact["accn"]]
                if fact["form"] != filing["form"]:
                    continue
                for year, period in profile["periods"].items():
                    if (fact.get("start"), fact["end"]) == (period_start(period, rule), period["end"]):
                        value = decimal_value(fact["val"]) * rule.get("xbrl_sign", 1)
                        source = xbrl_source(filing_year, filing, qualified_tag)
                        observations[year, metric].append(Observation(value, source))
    return observations


def xbrl_instance(profile, year, path):
    filing = profile["filings"][year]["xbrl"]
    document = ElementTree.parse(path).getroot()
    q = XBRL_INSTANCE
    if document.tag != q + "xbrl":
        raise ValueError("Expected an XBRL instance XML document")
    namespaces = filing.get("namespaces", profile.get("namespaces", {}))
    if not namespaces:
        raise ValueError("Set XBRL namespace URIs in the profile before importing an XML instance")
    if "document_end_tag" in filing:
        prefix, tag = filing["document_end_tag"].split(":", 1)
        expected_end = filing["period_end"]
        ends = {element.text for element in document.findall(f"{{{namespaces[prefix]}}}{tag}")}
        if ends != {expected_end}:
            raise ValueError(f"Expected document period end {expected_end}")
    contexts = {}
    identifier_value = str(profile.get("entity_id", profile.get("cik")))
    for context in document.findall(q + "context"):
        entity = context.find(q + "entity")
        period = context.find(q + "period")
        if entity is None or period is None:
            continue
        identifier = entity.findtext(q + "identifier")
        same_issuer = (identifier == identifier_value if "entity_id" in profile else
                       identifier is not None and identifier.lstrip("0") == identifier_value.lstrip("0"))
        if not same_issuer:
            continue
        if entity.find(q + "segment") is not None or context.find(q + "scenario") is not None:
            continue
        contexts[context.attrib["id"]] = (period.findtext(q + "startDate"),
                                         period.findtext(q + "endDate") or period.findtext(q + "instant"))
    if not contexts:
        raise ValueError("No consolidated contexts for the profile issuer")
    units = {}
    for unit in document.findall(q + "unit"):
        measures = unit.findall(q + "measure")
        if len(measures) == 1 and measures[0].text:
            units[unit.attrib["id"]] = measures[0].text.split(":")[-1]
    observations = defaultdict(list)
    for metric, rule in profile["metrics"].items():
        for qualified_tag in rule.get("tags", []):
            prefix, tag = qualified_tag.split(":", 1)
            if prefix not in namespaces:
                continue
            for fact in document.findall(f"{{{namespaces[prefix]}}}{tag}"):
                if units.get(fact.get("unitRef")) != rule.get("unit", profile["currency"]):
                    continue
                context = contexts.get(fact.get("contextRef"))
                if context is None or fact.text is None:
                    continue
                for period_year, period in profile["periods"].items():
                    if context == (period_start(period, rule), period["end"]):
                        value = decimal_value(fact.text) * rule.get("xbrl_sign", 1)
                        source = xbrl_source(year, filing, qualified_tag)
                        observations[period_year, metric].append(Observation(value, source))
    return observations


def label_key(label):
    return " ".join(label.casefold().replace("’", "'").split())


def pdf_rows(page, tolerance):
    rows = []
    for word in sorted(page.get_text("words"), key=lambda item: ((item[1] + item[3]) / 2, item[0])):
        y = (word[1] + word[3]) / 2
        if not rows or y - rows[-1][0] > tolerance:
            rows.append((y, []))
        rows[-1][1].append(word)
    return [sorted(words, key=lambda item: item[0]) for _, words in rows]


def pdf_amount(text, table, unit):
    text = text.strip().replace("−", "-")
    for symbol in table.get("symbols", []):
        text = text.replace(symbol, "")
    text = text.strip()
    if text in table.get("zero_tokens", []):
        return Decimal(0)
    negative = text.startswith("(") and text.endswith(")")
    text = text[1:-1].strip() if negative else text
    decimal = table.get("decimal_mark", ".")
    grouping = table.get("grouping_mark", ",")
    if grouping == " ":
        text = " ".join(text.split())
    digit_groups = rf"(?:\d+|\d{{1,3}}(?:{re.escape(grouping)}\d{{3}})+)"
    sign = "" if negative else "[+-]?"
    if not re.fullmatch(rf"{sign}{digit_groups}(?:{re.escape(decimal)}\d+)?", text):
        raise ValueError(f"Unreadable amount {text!r}")
    value = decimal_value(text.replace(grouping, "").replace(decimal, "."))
    return value * table.get("scales", {}).get(unit, table["scale"]) * (-1 if negative else 1)


def pdf_table(page, table, *, scale_elsewhere=False):
    rows = pdf_rows(page, table.get("row_tolerance", 2.5))
    heading = label_key(table["heading"])
    titles = [" ".join(word[4] for word in row) for row in rows]
    headings = [index for index, title in enumerate(titles) if heading == label_key(title)]
    if len(headings) != 1:
        raise ValueError("Expected a unique table heading")
    head = headings[0]
    end = len(rows)
    if "end_heading" in table:
        ends = [index for index in range(head + 1, end) if label_key(titles[index]) == label_key(table["end_heading"])]
        if len(ends) != 1:
            raise ValueError("Expected a unique closing table heading")
        end = ends[0]
    years = table["years"]
    suffix = table.get("year_suffix")
    year_words = [word for year in years for word in ([year, suffix] if suffix else [year])]
    year_count = len(year_words)
    header = label_key(table["header"])
    scale = label_key(table["scale_label"])
    matches = []
    for index in range(head + 1, end):
        words = [word[4] for word in rows[index]]
        if words[-year_count:] != year_words:
            continue
        prefix = label_key(" ".join(words[:-year_count]))
        units_above = scale_elsewhere or any(label_key(title) == scale for title in titles[head + 1:index])
        if prefix == label_key(f"{scale} {header}") or (prefix == header and units_above):
            matches.append(index)
    if len(matches) != 1:
        raise ValueError("Expected distinct year columns in the stated order and scale")
    header_index = matches[0]
    year_stride = 2 if suffix else 1
    year_edges = [word[2] for word in rows[header_index][-year_count:][year_stride - 1::year_stride]]
    note_column_start = table.get("note_column_start")
    padding = (year_edges[1] - year_edges[0]) / 2 if len(years) > 1 else table["single_column_padding"]
    thresholds = [year_edges[0] - padding, *[(left + right) / 2 for left, right in pairwise(year_edges)]]
    result = defaultdict(list)
    for index, words in enumerate(rows[header_index + 1:end], start=header_index + 1):
        cells = [[] for _ in range(len(years) + 1)]
        for word in words:
            if word[4] == "." and table.get("strip_dot_leaders"):
                continue
            column = bisect_right(thresholds, word[2])
            if column or note_column_start is None or word[0] < note_column_start:
                cells[column].append(word[4])
        label, *values = (" ".join(cell) for cell in cells)
        for symbol in table.get("symbols", []):
            label = label.removesuffix(symbol).strip()
        result[label_key(label)].append((index, dict(zip(years, values, strict=True))))
    return result


def pdf_facts(profile, filing_year, content):
    import pymupdf

    filing = profile["filings"][filing_year]["pdf"]
    observations = defaultdict(list)
    if not isinstance(content, bytes):
        content = content.read_bytes()
    if "sha256" in filing and sha256(content).hexdigest() != filing["sha256"]:
        raise ValueError("PDF checksum differs from the copy in the profile")
    try:
        document = pymupdf.open(stream=content, filetype="pdf")
    except pymupdf.FileDataError as error:
        raise ValueError("File could not be read as a PDF") from error
    with document:
        for table_name, layout in filing["tables"].items():
            table = profile.get("pdf_tables", {}).get(table_name, {}) | layout
            page = table["page"]
            url = f'{filing["url"]}#page={page}'
            try:
                if page < 1 or page > len(document):
                    raise ValueError("Configured page is outside the PDF")
                scale_page = table.get("scale_page")
                if scale_page is not None:
                    if not 1 <= scale_page <= len(document):
                        raise ValueError("Configured scale page is outside the PDF")
                    rows = pdf_rows(document[scale_page - 1], table.get("row_tolerance", 2.5))
                    if not any(label_key(" ".join(word[4] for word in row)) == label_key(table["scale_label"])
                               for row in rows):
                        raise ValueError("Expected the stated units on the notes scale page")
                rows = pdf_table(document[page - 1], table, scale_elsewhere=scale_page is not None)
                error = ""
            except ValueError as failure:
                rows, error = {}, str(failure)
            for metric, rule in profile["metrics"].items():
                if rule.get("table") != table_name:
                    continue
                rule_error = error
                lower, upper = -1, float("inf")
                for boundary in ("pdf_after", "pdf_before"):
                    if boundary not in rule:
                        continue
                    anchors = rows.get(label_key(rule[boundary]), [])
                    if len(anchors) != 1:
                        rule_error = error or f"Expected a unique row for {boundary}"
                    elif boundary == "pdf_after":
                        lower = anchors[0][0]
                    else:
                        upper = anchors[0][0]
                for label in rule.get("pdf_labels", []):
                    for year in table["years"]:
                        if year not in profile["periods"]:
                            continue
                        source = Source(int(filing_year), label, url, f'FY{filing_year} PDF p. {page} · {label}')
                        if rule_error:
                            observations[year, metric].append(Observation(None, source, rule_error))
                            continue
                        for index, row in rows.get(label_key(label), []):
                            if not lower < index < upper:
                                continue
                            try:
                                value = pdf_amount(row[year], table, rule.get("unit", profile["currency"]))
                                observations[year, metric].append(Observation(value * rule.get("pdf_sign", 1), source))
                            except ValueError as failure:
                                observations[year, metric].append(Observation(None, source, str(failure)))
    return observations


def review(profile, inputs, document_issues):
    records = []
    for year, period in sorted(profile["periods"].items()):
        for metric, rule in profile["metrics"].items():
            if "years" in rule and year not in rule["years"]:
                continue
            modes = rule.get("sources", inputs)
            candidates = {mode: list(dict.fromkeys(inputs.get(mode, {}).get((year, metric), []))) for mode in modes}
            amounts = {mode: {item.value for item in rows if item.value is not None}
                       for mode, rows in candidates.items()}
            missing = [mode for mode, values in amounts.items() if not values]
            observed = set().union(*amounts.values())
            unclear = any(item.issue for rows in candidates.values() for item in rows)
            issues = [f"{mode} has no matching figure." for mode in missing]
            if "XBRL" in missing and rule.get("xbrl_note"):
                issues.append(rule["xbrl_note"])
            if unclear:
                issues.append("At least one source location needs review.")
            if len(observed) > 1:
                issues.append("Source amounts disagree for the same metric and period.")
            status = ("Conflict" if len(observed) > 1 else "Missing" if missing else "Review" if unclear else
                      "Matched" if len(candidates) > 1 else "Single source")
            records.append({
                "year": year, "start": period_start(period, rule), "end": period["end"], "metric": metric,
                "unit": rule.get("unit", profile["currency"]), "label": rule["label"], "status": status,
                "value": next(iter(observed)) if not issues else None, "observations": candidates, "issues": issues,
            })
    balance_checks = []
    for year in sorted(profile["periods"]):
        balances = {item["metric"]: item for item in records if item["year"] == year and item["start"] is None}
        liability_metrics = ["liabilities"] if "liabilities" in balances else ["current_liabilities", "long_liabilities"]
        equation = ["assets", *liability_metrics, "equity"]
        if not all(metric in balances for metric in equation):
            continue
        parts = [balances[metric] for metric in equation]
        assets, *claims = (item["value"] for item in parts)
        difference = None if assets is None or None in claims else assets - sum(claims)
        status = "Blocked" if difference is None else (
            "Pass" if abs(difference) <= profile.get("balance_tolerance", 0) else "Failed")
        balance_checks.append({"year": year, "difference": difference, "status": status,
                               "metrics": equation})
        if status == "Failed":
            for item in parts:
                item.update(status="Review", value=None)
                item["issues"].append("Assets do not reconcile to liabilities plus equity.")
    return {"schema_version": 1, "company": profile["name"], "scope": "Consolidated",
            "issuer": {**profile.get("issuer", {}), "currency": profile["currency"]},
            "periods": profile["periods"], "source_note": profile.get("source_note", ""),
            "records": records, "balance_checks": balance_checks,
            "document_issues": document_issues, "needs_review": bool(document_issues) or
            any(item["value"] is None for item in records) or any(check["status"] != "Pass" for check in balance_checks)}


def display_value(value, unit):
    if unit == "locations":
        return f"{value:,f} locations"
    label = f"{value / 1_000_000:,.6f}".rstrip("0").rstrip(".")
    return f"{label} million shares" if unit == "shares" else f"{label} {unit} m"


def render_review(result):
    def cell(item, mode):
        pieces = [f'<span class="source-year">FY{obs.source.filing_year}</span> ' +
                  obs.source.link(display_value(obs.value, item["unit"]) if obs.value is not None else "Review source") +
                  (f" <small>{escape(obs.issue)}</small>" if obs.issue else "")
                  for obs in item["observations"].get(mode, [])]
        return "<br>".join(pieces) or "—"

    def row(item):
        issues = "<br>".join(escape(issue) for issue in item["issues"])
        review_class = ' class="review"' if item["value"] is None else ""
        return (f'<tr{review_class}><th scope="row">{escape(item["label"])}</th><td>{item["year"]}</td>'
                f'<td>{cell(item, "XBRL")}</td><td>{cell(item, "PDF")}</td>'
                f'<td>{item["status"]}{"<br>" + issues if issues else ""}</td></tr>')

    records = result["records"]
    flagged = sum(item["value"] is None for item in records)
    name = escape(result["company"])
    checks = " · ".join(f'FY{check["year"]} {check["status"].lower()}' for check in result["balance_checks"])
    errors = "".join(f"<li>{escape(error)}</li>" for error in result["document_issues"])
    source_note = escape(result["source_note"])
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>{name} filing import</title>
<style>
body {{ margin: 0; background: #f3f6f4; color: #142724; font: 16px/1.5 system-ui, sans-serif }}
main {{ max-width: 74rem; margin: auto; padding: clamp(1rem, 4vw, 3rem) }}
h1 {{ line-height: 1.12 }} a {{ color: #145749 }} small {{ display: block }}
.source-year {{ color: #4f625e; font-size: .85em }}
.scroll {{ overflow-x: auto; border: 1px solid #d8e4de; border-radius: .5rem }}
table {{ width: 100%; border-collapse: collapse; background: white; font-variant-numeric: tabular-nums }}
th, td {{ text-align: left; padding: .6rem .9rem; vertical-align: top; border-bottom: 1px solid #dfe8e3 }}
thead {{ background: #e8f0ec }} .review {{ background: #fff2d8 }}
</style></head><body><main>
<h1>{name} filing import</h1>
<p>{len(records) - flagged} figures accepted · {flagged} require review. Amounts are shown in millions.</p>
{f'<p>{source_note}</p>' if source_note else ""}
{f'<p>Balance sheet checks · {checks}</p>' if checks else ""}
{f'<ul aria-label="Document issues">{errors}</ul>' if errors else ""}
<div class="scroll" role="region" aria-label="Figures and filing links" tabindex="0">
<table><thead><tr><th scope="col">Metric</th><th scope="col">Fiscal year</th>
<th scope="col">XBRL</th><th scope="col">PDF</th><th scope="col">Status</th></tr></thead>
<tbody>{''.join(row(item) for item in sorted(records, key=lambda item: item["value"] is not None))}</tbody>
</table></div></main></body></html>
"""


def json_value(value):
    if isinstance(value, Observation):
        return asdict(value)
    if isinstance(value, Decimal):
        return str(value)
    raise TypeError(f"Cannot serialize {type(value).__name__}")


def filing_path(text):
    year, separator, path = text.partition("=")
    if not separator or not re.fullmatch(r"\d{4}", year) or not path:
        raise argparse.ArgumentTypeError("Use YEAR=PATH or YEAR=auto")
    return year, path


def fetch(url, user_agent):
    if urlsplit(url).scheme != "https":
        raise ValueError("Automatic imports require HTTPS")
    with urlopen(Request(url, headers={"User-Agent": user_agent}), timeout=30) as response:
        return response.read()


def validate_profile(profile):
    for year, period in profile["periods"].items():
        start, end = (date.fromisoformat(period[key]) for key in ("start", "end"))
        if start > end or not re.fullmatch(r"\d{4}", year):
            raise ValueError(f"Invalid reporting period FY{year}")
    for metric, rule in profile["metrics"].items():
        if rule["kind"] not in {"flow", "balance"}:
            raise ValueError(f"Invalid period type for {metric}")
        if "years" in rule:
            years = rule["years"]
            if (not isinstance(years, list) or not years or len(set(years)) != len(years)
                    or set(years) - profile["periods"].keys()):
                raise ValueError(f"Invalid fiscal years for {metric}")
        if "sources" in rule:
            sources = rule["sources"]
            if not isinstance(sources, list) or not sources or len(set(sources)) != len(sources) or set(sources) - {"XBRL", "PDF"}:
                raise ValueError(f"Invalid source selection for {metric}")
    for year, filing in profile["filings"].items():
        for source in (filing.get("pdf"), filing.get("xbrl")):
            if source and urlsplit(source["url"]).scheme != "https":
                raise ValueError(f"FY{year} source links must use HTTPS")
        for name, layout in filing.get("pdf", {}).get("tables", {}).items():
            table = profile.get("pdf_tables", {}).get(name, {}) | layout
            years = table["years"]
            if not years or len(set(years)) != len(years) or year not in years or table["scale"] <= 0:
                raise ValueError(f"Invalid PDF table configuration for FY{year}")


def main():
    parser = argparse.ArgumentParser(description="Compare filing-linked figures from XBRL and PDF sources.")
    parser.add_argument("profile", type=Path)
    parser.add_argument("--xbrl", metavar="PATH_OR_AUTO", help="SEC Company Facts JSON file or auto")
    parser.add_argument("--instance", action="append", type=filing_path, default=[], metavar="YEAR=PATH",
                        help="Local XBRL instance XML for a configured filing")
    parser.add_argument("--pdf", action="append", type=filing_path, default=[], metavar="YEAR=PATH_OR_AUTO")
    parser.add_argument("--edgar-contact", metavar="EMAIL", help="Contact used in the live SEC request")
    parser.add_argument("--output", type=Path, required=True, help="HTML review report")
    parser.add_argument("--json-output", type=Path, help="Machine-readable figures and review flags")
    args = parser.parse_args()
    if not (args.xbrl or args.instance or args.pdf):
        parser.error("Select at least one XBRL or PDF input")
    if args.xbrl == "auto" and not args.edgar_contact:
        parser.error("Live SEC Company Facts requests require --edgar-contact")
    input_paths = {args.profile.resolve(), *(Path(path).resolve() for _, path in args.instance + args.pdf if path != "auto")}
    if args.xbrl and args.xbrl != "auto":
        input_paths.add(Path(args.xbrl).resolve())
    outputs = [path.resolve() for path in (args.output, args.json_output) if path]
    if input_paths.intersection(outputs) or len(outputs) != len(set(outputs)):
        parser.error("Choose distinct output paths that do not overwrite an input")
    try:
        profile = read_json(args.profile)
        validate_profile(profile)
    except (OSError, ValueError, KeyError, TypeError) as error:
        parser.error(f"Invalid filing profile. {error}")

    inputs = {}
    issues = []

    def collect(mode, name, load):
        combined = inputs.setdefault(mode, defaultdict(list))
        try:
            for key, values in load().items():
                combined[key].extend(values)
        except (OSError, ValueError, KeyError, InvalidOperation, ElementTree.ParseError, ImportError) as error:
            issues.append(f"{name} could not be imported. {error}")

    if args.xbrl:
        def load_company_facts():
            if args.xbrl != "auto":
                return company_facts(profile, read_json(Path(args.xbrl)))
            url = f'https://data.sec.gov/api/xbrl/companyfacts/CIK{int(profile["cik"]):010d}.json'
            data = fetch(url, f"Financial Analysis Toolkit {args.edgar_contact}")
            return company_facts(profile, json.loads(data, parse_float=Decimal))

        collect("XBRL", "SEC Company Facts", load_company_facts)
    for year, path in args.instance:
        collect("XBRL", f"FY{year} XML instance", lambda: xbrl_instance(profile, year, Path(path)))
    for year, path in args.pdf:
        def load_pdf():
            data = fetch(profile["filings"][year]["pdf"]["url"], "Financial Analysis Toolkit") if path == "auto" else None
            return pdf_facts(profile, year, data if data is not None else Path(path))

        collect("PDF", f"FY{year} PDF", load_pdf)
    result = review(profile, inputs, issues)
    write_report(args.output, render_review(result))
    if args.json_output:
        write_report(args.json_output, json.dumps(result, default=json_value, ensure_ascii=False, indent=2) + "\n")
    return 2 if result["needs_review"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
