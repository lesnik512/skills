from datetime import datetime
from decimal import Decimal
from html import escape
from pathlib import Path
from tempfile import NamedTemporaryFile
from zoneinfo import ZoneInfo

from analysis_input import Figure, Source, https_url
from mosaic_edgar import ANNUAL_FLOWS, BALANCES


HERE = Path(__file__).resolve().parent
SECTIONS = {
    "Core metrics": (
        ("growth", "Revenue growth", "Revenue ÷ prior-year revenue − 1", "%"),
        ("gross_margin", "Gross margin", "Gross profit ÷ revenue", "%"),
        ("operating_margin", "Operating margin", "Operating profit ÷ revenue", "%"),
        ("net_margin", "Net margin", "Group net profit ÷ revenue", "%"),
        ("cash_after_capex", "Cash after capex", "Operating cash flow less cash purchases of PP&E and intangibles", ""),
        ("cash_conversion", "Cash conversion", "Operating cash flow ÷ group net profit", "×"),
        ("net_debt", "Net debt", "Borrowings + lease liabilities − cash and equivalents", ""),
        ("leverage", "Net debt / EBITDA", "Net debt ÷ EBITDA as defined below", "×"),
        ("current_ratio", "Current ratio", "Current assets ÷ current liabilities", "×"),
        ("short_debt_share", "Current share of borrowings", "Current borrowings ÷ total borrowings, excluding leases", "%"),
    ),
    "Income statement and cash flow": (
        ("revenue", "Revenue", "", ""),
        ("net_sales", "Net sales", "", ""),
        ("membership_fees", "Membership fees", "", ""),
        ("gross_profit", "Gross profit", "", ""),
        ("operating_profit", "Operating profit", "Includes operating FX gains and losses", ""),
        ("net_profit", "Group net profit", "Includes non-controlling interests", ""),
        ("operating_cash", "Operating cash flow", "After cash taxes and finance expenses paid", ""),
        ("capex", "Cash capex", "PP&E and intangible purchases shown as an outflow", ""),
        ("depreciation", "Depreciation and amortization", "Cash flow statement adjustment", ""),
        ("ebitda", "EBITDA", "Operating profit + depreciation and amortization", ""),
    ),
    "Balance sheet": (
        ("cash", "Cash and equivalents", "", ""),
        ("short_debt", "Current borrowings", "", ""),
        ("current_long_term_debt", "Current maturities of long-term debt", "", ""),
        ("long_debt", "Non-current borrowings", "", ""),
        ("short_leases", "Current lease liabilities", "", ""),
        ("long_leases", "Non-current lease liabilities", "", ""),
        ("total_lease_liabilities", "Total lease liabilities as reported", "", ""),
        ("current_assets", "Current assets", "", ""),
        ("assets", "Total assets", "", ""),
        ("current_liabilities", "Current liabilities", "", ""),
        ("long_liabilities", "Non-current liabilities", "", ""),
        ("liabilities", "Total liabilities", "", ""),
        ("equity", "Total equity", "", ""),
    ),
}


def filing_source(company, year, locator):
    filing = company["filings"][str(year)]
    if "sections" in filing:
        fragment, description = filing["sections"][locator]
    else:
        page = int(locator)
        if page < 1:
            raise ValueError(f"Invalid PDF page: {locator}")
        fragment, description = f"page={page}", f"PDF page {page}"
    title = f'FY{year} {company["filing_label"]}, {description}'
    return Source(year, locator, f'{filing["url"]}#{fragment}', title)


def combined(value, *inputs):
    unique_sources = {source for figure in inputs for source in figure.sources}
    sources = tuple(sorted(unique_sources, key=lambda source: (source.filing_year, source.locator.zfill(6))))
    return Figure(value, sources)


def total(*inputs):
    return combined(sum(figure.value for figure in inputs), *inputs)


def ratio(numerator, denominator):
    if denominator.value <= 0:
        return None
    return combined(numerator.value / denominator.value, numerator, denominator)


OPTIONAL_ROWS = {"net_sales", "membership_fees", "current_long_term_debt", "total_lease_liabilities", "liabilities"}


def annual_facts(inputs):
    balance_metrics = {row[0] for row in SECTIONS["Balance sheet"]} | {
        "short_term_debt", "current_operating_lease", "current_finance_lease",
        "noncurrent_operating_lease", "noncurrent_finance_lease",
    }
    flow_metrics = ({row[0] for row in SECTIONS["Income statement and cash flow"]} - {"ebitda"}) | {"merchandise_costs"}
    facts = {}
    for year, period in inputs.periods.items():
        for metric in sorted(balance_metrics | flow_metrics):
            start = None if metric in balance_metrics else period["start"]
            if (metric, start, period["end"]) in inputs.records:
                facts[int(year), metric] = inputs.figure(metric, start, period["end"])
    return facts


def calculate(facts, year, *, gross_margin_basis="revenue"):
    if gross_margin_basis not in {"revenue", "net_sales"}:
        raise ValueError("Gross margin basis must be revenue or net sales")
    figures = {metric: figure for (period, metric), figure in facts.items() if period == year}

    def derive(function, *metrics):
        inputs = [figures.get(metric) for metric in metrics]
        if all(figure is not None for figure in inputs):
            return function(*inputs)
        return None

    def difference(left, right):
        return combined(left.value - right.value, left, right)

    if gross_margin_basis == "net_sales" and "gross_profit" not in figures:
        figures["gross_profit"] = derive(difference, "net_sales", "merchandise_costs")
    check_components = "long_liabilities" in figures or "liabilities" not in figures
    if not check_components:
        figures["long_liabilities"] = derive(difference, "liabilities", "current_liabilities")
    if "short_debt" not in figures:
        figures["short_debt"] = derive(total, "short_term_debt", "current_long_term_debt")
    if "short_leases" not in figures:
        figures["short_leases"] = derive(total, "current_operating_lease", "current_finance_lease")
    if "long_leases" not in figures:
        figures["long_leases"] = derive(total, "noncurrent_operating_lease", "noncurrent_finance_lease")

    capex = figures.get("capex")
    if capex is not None and capex.value > 0:
        raise ValueError(f"FY{year} capex must be a signed cash outflow")
    liabilities_and_equity = derive(total, "current_liabilities", "long_liabilities", "equity")
    assets = figures.get("assets")
    if (check_components and liabilities_and_equity is not None and assets is not None
            and liabilities_and_equity.value != assets.value):
        raise ValueError(f"FY{year} balance sheet does not reconcile")

    previous = facts.get((year - 1, "revenue"))
    revenue = figures.get("revenue")
    growth = ratio(revenue, previous) if revenue is not None and previous is not None else None
    figures["borrowings"] = derive(total, "short_debt", "long_debt")
    figures["lease_liabilities"] = derive(total, "short_leases", "long_leases")
    figures.update(
        growth=combined(growth.value - 1, growth) if growth is not None else None,
        gross_margin=derive(ratio, "gross_profit", gross_margin_basis),
        operating_margin=derive(ratio, "operating_profit", "revenue"),
        net_margin=derive(ratio, "net_profit", "revenue"),
        cash_after_capex=derive(total, "operating_cash", "capex"),
        cash_conversion=derive(ratio, "operating_cash", "net_profit"),
        ebitda=derive(total, "operating_profit", "depreciation"),
        net_debt=derive(lambda debt, leases, cash: combined(debt.value + leases.value - cash.value, debt, leases, cash),
                        "borrowings", "lease_liabilities", "cash"),
        current_ratio=derive(ratio, "current_assets", "current_liabilities"),
        short_debt_share=derive(ratio, "short_debt", "borrowings"),
    )
    figures["leverage"] = derive(ratio, "net_debt", "ebitda")
    return figures


def render_figure(figure, unit, percent_places=1):
    if figure is None:
        return '<span class="muted">N/A</span>'
    if unit == "%":
        label = f"{figure.value:.{percent_places}%}"
    elif unit == "×":
        label = f"{figure.value:.2f}×"
    else:
        label = f"{figure.value:,f}"
    primary, *other_sources = figure.sources
    extra = " ".join(source.link(f"+{index}") for index, source in enumerate(other_sources, 1))
    return primary.link(label) + (f' <small class="source">{extra}</small>' if extra else "")


def render_tables(results, company):
    tables = []
    years = company["years"]
    for section, metrics in SECTIONS.items():
        rows = []
        for metric, label, formula, unit in metrics:
            if metric in OPTIONAL_ROWS and all(results[year].get(metric) is None for year in years):
                continue
            formula = company.get("metric_notes", {}).get(metric, formula)
            description = f'<span class="formula">{escape(formula)}</span>' if formula else ""
            values = "".join(f"<td>{render_figure(results[year].get(metric), unit)}</td>" for year in years)
            rows.append(f'<tr><th scope="row">{escape(label)}{description}</th>{values}</tr>')
        headers = "".join(f'<th scope="col">FY{year}</th>' for year in years)
        tables.append(
            f'<div class="table-wrap" role="region" aria-label="{escape(section)}" tabindex="0">'
            f'<table><caption>{escape(section)}</caption>'
            f'<thead><tr><th scope="col">Metric</th>{headers}</tr></thead>'
            f'<tbody>{"".join(rows)}</tbody></table></div>'
        )
    return "\n".join(tables)


def render_notes(company):
    paragraphs = []
    for note in company.get("notes", []):
        links = []
        for reference in note["references"]:
            label = reference["label"]
            if "url" in reference:
                links.append(f'<a href="{escape(https_url(reference["url"]))}">{escape(label)}</a>')
            else:
                source = filing_source(company, reference["year"], reference["locator"])
                links.append(source.link(label))
        paragraphs.append(
            f'<p><strong>{escape(note["heading"])}.</strong> {escape(note["text"])} {", ".join(links)}</p>'
        )
    return "\n".join(paragraphs)


def render_report(results, company, review_section=""):
    years = company["years"]
    window = f"FY{years[0]}–FY{years[-1]}" if len(years) > 1 else f"FY{years[0]}"
    name = escape(company["name"])
    market = escape(company.get("market", ""))
    ticker = escape(company.get("ticker", ""))
    accounting = escape(company.get("accounting", ""))
    currency = escape(company["currency"])
    meta = " · ".join(filter(None, (market, ticker, window, accounting, f"{currency} millions unless shown as a ratio or percentage")))
    picture = ""
    if len(years) >= 2:
        first, previous, latest = results[years[0]], results[years[-2]], results[years[-1]]
        if all(period.get(metric) is not None for period, metric in (
            (first, "revenue"), (latest, "revenue"), (first, "operating_margin"), (latest, "operating_margin"),
            (previous, "cash_after_capex"), (latest, "cash_after_capex"),
        )):
            picture = (f'<h2>Reported results</h2><p>From FY{years[0]} to FY{years[-1]}, revenue went from '
                       f'{render_figure(first["revenue"], "")} to {render_figure(latest["revenue"], "")} and operating margin '
                       f'went from {render_figure(first["operating_margin"], "%")} to {render_figure(latest["operating_margin"], "%")}. '
                       f'Cash after capex was {render_figure(latest["cash_after_capex"], "")} in FY{years[-1]}, compared with '
                       f'{render_figure(previous["cash_after_capex"], "")} in FY{years[-2]}.</p>')
        if all(period.get(metric) is not None for period, metric in (
            (latest, "net_debt"), (latest, "short_debt_share"), (previous, "short_debt_share"),
        )):
            picture += (f'<p>Net debt was {render_figure(latest["net_debt"], "")} at the end of FY{years[-1]}. '
                        f'The share of borrowings due within a year was {render_figure(latest["short_debt_share"], "%")}, '
                        f'compared with {render_figure(previous["short_debt_share"], "%")} at the previous year-end.</p>')
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{name} | {window} financial analysis</title>
<style>
:root {{ color-scheme: light; font: 16px/1.55 system-ui, sans-serif; color: #142724; background: #f3f6f4; }}
body {{ margin: 0; }}
main {{ max-width: 66rem; margin: 0 auto; padding: clamp(1rem, 5vw, 3.5rem); }}
header {{ padding-bottom: 1.25rem; border-bottom: 2px solid #204e43; }}
h1 {{ margin: 0; font-size: clamp(2rem, 6vw, 3.5rem); line-height: 1.12; letter-spacing: -.045em; }}
h2 {{ font-size: 1.25rem; margin-top: 1.7rem; }}
p {{ margin: .75rem 0; max-width: 75ch; }}
.meta, .muted, .formula {{ color: #50645e; }}
a {{ color: #145749; text-underline-offset: .17em; }}
a:focus-visible, .table-wrap:focus-visible {{ outline: 2px solid #176758; outline-offset: 2px; }}
.table-wrap {{ overflow-x: auto; margin: 1.6rem 0; border: 1px solid #d8e4de; border-radius: .75rem; }}
table {{ width: 100%; min-width: 39rem; border-collapse: collapse; background: #fff; }}
caption {{ text-align: left; padding: .85rem 1rem; background: #16463c; color: #fff; font-weight: 700; }}
th, td {{ padding: .65rem 1rem; text-align: right; border-bottom: 1px solid #dfe8e3; }}
th:first-child {{ text-align: left; }}
thead {{ background: #e8f0ec; }}
tr:last-child > * {{ border-bottom: 0; }}
tbody td {{ font-variant-numeric: tabular-nums; white-space: nowrap; }}
.formula {{ display: block; font-size: .8rem; font-weight: 400; }}
.source {{ font-size: .72rem; }}
.source a {{ background: #edf5f0; padding: .08em .3em; border-radius: .25em; }}
aside {{ padding: .85rem 1.2rem; margin-top: 1.25rem; border-left: 3px solid #44776b; background: #e8f0ec; }}
aside p {{ margin: .35rem 0; }}
@media print {{
  :root {{ background: white; }}
  main {{ padding: 0; }}
  .table-wrap {{ overflow: visible; }}
  table {{ min-width: 0; }}
  tr {{ break-inside: avoid; }}
  caption {{ background: white; color: #142724; }}
}}
</style>
</head>
<body><main>
<header>
<h1>{name} financial analysis</h1>
<p class="meta">{meta}</p>
<p>Each value links to its source. Numbered links open additional sources used in the calculation.</p>
</header>
{review_section}
{picture}
{render_tables(results, company)}
{f'<aside aria-label="Reporting notes">{render_notes(company)}</aside>' if company.get("notes") else ""}
</main></body>
</html>
"""


def render_edgar_review(rows, matched, latest, fallback):
    checked_at = datetime.now(ZoneInfo("Europe/Moscow")).strftime("%Y-%m-%d %H:%M %Z")
    status = (
        f'<p>Checked {checked_at}. Latest annual filing: {latest.link(latest.title)}. '
        f'{matched} inputs matched the verified figures.</p>'
    )
    if fallback:
        status += f'<p><strong>Showing the verified report.</strong> {escape(fallback)}</p>'
    if not rows:
        return f'<section><h2>SEC EDGAR refresh</h2>{status}<p>No changes or review items.</p></section>'

    items = []
    for year, label, outcome, original, candidate, observation, source in rows:
        values = render_figure(candidate, "") if candidate is not None else source.link("Open filing")
        details = []
        for tag, value in observation.conflicting_values:
            link = render_figure(Figure(value, (source,)), "")
            details.append(f'{escape(tag)}: {link}')
        if observation.missing_tags:
            tags = escape("; ".join(observation.missing_tags))
            details.append(f"Missing tags: {tags}")
        if details:
            values += f'<span class="formula">{"<br>".join(details)}</span>'
        prior = render_figure(original, "") if original is not None else "—"
        items.append(
            f'<tr><th scope="row">FY{year} {escape(label)}</th><td>{outcome}</td><td>{prior}</td><td>{values}</td></tr>'
        )
    return (
        f'<section><h2>SEC EDGAR refresh</h2>{status}'
        '<p>Missing and conflicting figures require review. Verified inputs remain in use where available.</p>'
        '<div class="table-wrap" role="region" aria-label="EDGAR changes and review" tabindex="0">'
        '<table><caption>Changes and review · USD millions</caption><thead><tr>'
        '<th scope="col">Annual input</th><th scope="col">Status</th>'
        '<th scope="col">Verified figures</th><th scope="col">EDGAR</th></tr></thead>'
        f'<tbody>{"".join(items)}</tbody></table></div></section>'
    )


def render_edgar_report(verified, company, data):
    baseline_years = company["years"]
    if data.latest_year < baseline_years[-1]:
        raise ValueError("EDGAR submissions are older than the verified report")
    target_years = list(range(data.latest_year - len(baseline_years) + 1, data.latest_year + 1))
    labels = {metric: label for rows in SECTIONS.values() for metric, label, _, _ in rows}
    required = [(target_years[0] - 1, "revenue")]
    required += [(year, metric) for year in target_years for metric in ANNUAL_FLOWS | BALANCES]
    facts = dict(verified)
    matched = 0
    rows = []
    for year, metric in required:
        key = year, metric
        original = verified.get(key)
        observation = data.inspect(year, metric)
        filing = observation.filing
        source = Source(filing.year, filing.accession, filing.url, filing.title)
        candidate = None
        outcome = observation.status
        if observation.value is not None:
            if original is not None and original.value == observation.value:
                matched += 1
                continue
            candidate = Figure(observation.value, (source,))
            original_filings = [
                data.filing_by_url.get(item.url.partition("#")[0]) for item in original.sources
            ] if original else []
            if any(item is None or item.filed >= filing.filed for item in original_filings):
                outcome = "Conflict"
            else:
                facts[key] = candidate
                outcome = "Changed" if original else "New"
        rows.append((year, labels[metric], outcome, original, candidate, observation, source))

    fallback = ""
    if any(key not in facts for key in required):
        fallback = "The latest three-year window has unresolved inputs."
    else:
        try:
            results = {year: calculate(facts, year) for year in target_years}
        except ValueError as error:
            fallback = f"The EDGAR figures failed validation. {error}"
    if fallback:
        target_years = baseline_years
        results = {year: calculate(verified, year) for year in baseline_years}
    filing = next(filing for filing in data.filings if filing.form == "10-K")
    latest = Source(filing.year, filing.accession, filing.url, filing.title)
    review = render_edgar_review(rows, matched, latest, fallback)
    return render_report(results, {**company, "years": target_years}, review)


def write_report(path, report):
    with NamedTemporaryFile(mode="w", dir=path.parent, encoding="utf-8", delete=False) as file:
        temporary = Path(file.name)
        try:
            file.write(report)
            file.close()
            temporary.replace(path)
        finally:
            temporary.unlink(missing_ok=True)
