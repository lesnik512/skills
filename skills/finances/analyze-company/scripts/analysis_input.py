import json
import re
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation
from html import escape
from urllib.parse import urlsplit


MILLION = Decimal(1_000_000)
ACCEPTED = {"Matched", "Single source"}
UNRESOLVED = {"Conflict", "Missing", "Review"}


@dataclass(frozen=True)
class Source:
    filing_year: int
    locator: str
    url: str
    title: str

    def link(self, label):
        return (
            f'<a href="{escape(https_url(self.url))}" title="{escape(self.title)}" '
            f'aria-label="{escape(label)}, {escape(self.title)}">{escape(label)}</a>'
        )


@dataclass(frozen=True)
class Figure:
    value: Decimal
    sources: tuple[Source, ...]


def https_url(value):
    url = urlsplit(value)
    if url.scheme != "https" or not url.hostname or url.username is not None:
        raise ValueError("Source links must be absolute HTTPS URLs")
    return value


def source_from_json(raw):
    source = Source(**raw)
    https_url(source.url)
    if (not source.locator.strip() or not source.title.strip()
            or isinstance(source.filing_year, bool) or not isinstance(source.filing_year, int)):
        raise ValueError("A source needs a filing year, locator and title")
    return source


def decimal_string(value):
    if not isinstance(value, str):
        raise ValueError("Amounts must be decimal strings in base units")
    try:
        amount = Decimal(value)
    except InvalidOperation as error:
        raise ValueError("Invalid amount") from error
    if not amount.is_finite():
        raise ValueError("Amounts must be finite")
    return amount


def iso_date(value):
    if not isinstance(value, str) or date.fromisoformat(value).isoformat() != value:
        raise ValueError("Use an ISO date in YYYY-MM-DD form")
    return value


def validate_comparison_scope(baseline, *, company, issuer, currency, periods):
    common_ids = {"id", "cik", "ticker", "market"} & baseline.issuer.keys() & issuer.keys()
    if (baseline.currency != currency or baseline.company != company
            or any(baseline.issuer[field] != issuer[field] for field in common_ids)):
        raise ValueError("Filing inputs must describe the same issuer and reporting currency")
    for year in baseline.periods.keys() & periods.keys():
        if baseline.periods[year] != periods[year]:
            raise ValueError(f"Fiscal dates for FY{year} differ. Review the period mapping.")


class AnalysisInput:
    def __init__(self, payload):
        if type(payload["schema_version"]) is not int or payload["schema_version"] != 1:
            raise ValueError("Expected analysis input schema version 1")
        if payload["scope"] != "Consolidated":
            raise ValueError("Only consolidated inputs are supported")
        self.payload = payload
        self.company = payload["company"]
        self.issuer = payload["issuer"]
        self.currency = self.issuer["currency"]
        if not re.fullmatch(r"[A-Z]{3}", self.currency):
            raise ValueError("Use a three-letter reporting currency")
        self.periods = payload["periods"]
        annual_ends = set()
        for year, period in self.periods.items():
            start, end = (iso_date(period[key]) for key in ("start", "end"))
            if not re.fullmatch(r"\d{4}", year) or start > end or end in annual_ends:
                raise ValueError(f"Invalid or duplicate annual period FY{year}")
            annual_ends.add(end)

        self.records = {}
        self.accepted = {}
        for record in payload["records"]:
            start, end = record["start"], iso_date(record["end"])
            if start is not None and iso_date(start) > end:
                raise ValueError(f"Invalid dates for {record['metric']}")
            key = record["metric"], start, end
            if key in self.records:
                raise ValueError(f"Duplicate record for {key}")
            self.records[key] = record
            self.accepted[key] = self._accepted(record)

        self.balance_checks = payload.get("balance_checks", [])
        for check in self.balance_checks:
            if check["status"] == "Pass":
                continue
            period_end = self.periods[str(check["year"])]["end"]
            for metric in check["metrics"]:
                key = metric, None, period_end
                if self.records.get(key, {}).get("decision") is not None:
                    raise ValueError("Reimport and reconcile the balance sheet before accepting these figures")

    @staticmethod
    def _accepted(record):
        candidates = []
        for mode, observations in record["observations"].items():
            for item in observations:
                value = None if item["value"] is None else decimal_string(item["value"])
                source = source_from_json(item["source"])
                if value is not None and not item.get("issue"):
                    candidates.append((mode, value, source))

        status = record["status"]
        if record.get("reopened_decision") is not None and (status not in UNRESOLVED or record.get("decision") is not None):
            raise ValueError(f"Reopened decision must remain unresolved for {record['metric']}")
        if status in ACCEPTED:
            if record.get("decision") is not None or record["issues"]:
                raise ValueError(f"Invalid accepted record for {record['metric']}")
            amount = decimal_string(record["value"])
            if not candidates or {value for _, value, _ in candidates} != {amount}:
                raise ValueError(f"Accepted amount does not match the evidence for {record['metric']}")
            return Figure(amount, tuple(dict.fromkeys(source for _, _, source in candidates)))
        if status not in UNRESOLVED or record["value"] is not None or not record["issues"]:
            raise ValueError(f"Invalid review status or value for {record['metric']}")

        decision = record.get("decision")
        if decision is None:
            return None
        source = source_from_json(decision["source"])
        amount = decimal_string(decision["value"])
        if not decision["reason"].strip() or (decision["mode"], amount, source) not in candidates:
            raise ValueError(f"Review decision must cite an existing observation and reason for {record['metric']}")
        return Figure(amount, (source,))

    def figure(self, metric, start, end, unit=None):
        key = metric, start, end
        figure = self.accepted.get(key)
        if figure is None:
            return None
        unit = unit or self.currency
        if self.records[key]["unit"] != unit:
            raise ValueError(f"Expected {unit} for {metric}, period ending {end}")
        if unit in {self.currency, "shares"}:
            return Figure(figure.value / MILLION, figure.sources)
        return figure

    def annual(self, year, metric, *, balance=False, unit=None):
        period = self.periods[str(year)]
        return self.figure(metric, None if balance else period["start"], period["end"], unit)

    @property
    def needs_review(self):
        return (bool(self.payload.get("document_issues")) or any(figure is None for figure in self.accepted.values())
                or any(check["status"] != "Pass" for check in self.balance_checks))


def read_input(path):
    with path.open(encoding="utf-8") as stream:
        return AnalysisInput(json.load(stream, parse_float=Decimal))
