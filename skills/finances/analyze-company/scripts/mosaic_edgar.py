import json
from dataclasses import dataclass
from decimal import Decimal
from urllib.parse import quote
from urllib.request import Request, urlopen


CIK = "0001285785"
EDGAR = "https://data.sec.gov"
ARCHIVE = f"https://www.sec.gov/Archives/edgar/data/{int(CIK)}"
USD_PER_MILLION = Decimal(1_000_000)

ANNUAL_FLOWS = {
    "revenue": (("RevenueFromContractWithCustomerExcludingAssessedTax", "Revenues"), 1),
    "gross_profit": (("GrossProfit",), 1),
    "operating_profit": (("OperatingIncomeLoss",), 1),
    "net_profit": (("ProfitLoss",), 1),
    "operating_cash": (("NetCashProvidedByUsedInOperatingActivities",), 1),
    "capex": (("PaymentsToAcquirePropertyPlantAndEquipment",), -1),
    "depreciation": (("DepreciationDepletionAndAmortization",), 1),
}
DEBT_CURRENT = "LongTermDebtAndCapitalLeaseObligationsCurrent"
DEBT_NONCURRENT = "LongTermDebtAndCapitalLeaseObligations"
FINANCE_CURRENT = "FinanceLeaseLiabilityCurrent"
FINANCE_NONCURRENT = "FinanceLeaseLiabilityNoncurrent"
BALANCES = {
    "cash": [(("CashAndCashEquivalentsAtCarryingValue",), 1)],
    "current_assets": [(("AssetsCurrent",), 1)],
    "assets": [(("Assets",), 1)],
    "current_liabilities": [(("LiabilitiesCurrent",), 1)],
    "equity": [(("StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest",), 1)],
    "short_debt": [(("ShortTermBorrowings", "OtherShortTermBorrowings"), 1),
                   ((DEBT_CURRENT,), 1), ((FINANCE_CURRENT,), -1)],
    "long_debt": [((DEBT_NONCURRENT,), 1), ((FINANCE_NONCURRENT,), -1)],
    "short_leases": [(("OperatingLeaseLiabilityCurrent",), 1), ((FINANCE_CURRENT,), 1)],
    "long_leases": [(("OperatingLeaseLiabilityNoncurrent",), 1), ((FINANCE_NONCURRENT,), 1)],
    "long_liabilities": [((DEBT_NONCURRENT,), 1), (("DeferredIncomeTaxLiabilitiesNet",), 1),
                         (("OtherLiabilitiesNoncurrent",), 1)],
}


@dataclass(frozen=True)
class Filing:
    year: int
    filed: str
    form: str
    accession: str
    document: str

    @property
    def url(self):
        return f"{ARCHIVE}/{self.accession.replace('-', '')}/{quote(self.document, safe='')}"

    @property
    def title(self):
        return f"FY{self.year} Form {self.form} filed {self.filed}"


@dataclass(frozen=True)
class Observation:
    filing: Filing
    status: str
    value: Decimal | None = None
    missing_tags: tuple[str, ...] = ()
    conflicting_values: tuple[tuple[str, Decimal], ...] = ()


def fetch_data(contact_email):
    def fetch(path):
        request = Request(
            f"{EDGAR}/{path}",
            headers={"User-Agent": f"Mosaic Financial Report {contact_email}", "Accept": "application/json"},
        )
        with urlopen(request, timeout=20) as response:
            payload = json.load(response, parse_float=Decimal)
        if int(payload["cik"]) != int(CIK):
            raise ValueError("EDGAR returned data for a different issuer")
        return payload

    submissions = fetch(f"submissions/CIK{CIK}.json")
    facts = fetch(f"api/xbrl/companyfacts/CIK{CIK}.json")
    return submissions, facts


class MosaicFacts:
    def __init__(self, submissions, company_facts):
        rows = submissions["filings"]["recent"]
        columns = ("form", "reportDate", "filingDate", "accessionNumber", "primaryDocument")
        filings = [
            Filing(int(period[:4]), filed, form, accession, document)
            for form, period, filed, accession, document in zip(*(rows[name] for name in columns), strict=True)
            if form in {"10-K", "10-K/A"} and period.endswith("-12-31")
        ]
        self.filings = sorted(filings, key=lambda filing: (filing.year, filing.filed, filing.accession), reverse=True)
        self.filing_by_url = {filing.url: filing for filing in self.filings}
        self.latest_year = next(filing.year for filing in self.filings if filing.form == "10-K")
        self.gaap = company_facts["facts"]["us-gaap"]

    def inspect(self, year, metric):
        flow = metric in ANNUAL_FLOWS
        terms = [ANNUAL_FLOWS[metric]] if flow else BALANCES[metric]
        start = f"{year}-01-01" if flow else None
        end = f"{year}-12-31"
        lag = 2 if flow else 1
        for filing in self.filings:
            if not year <= filing.year <= year + lag:
                continue
            values = []
            missing = []
            conflicts = []
            for tags, sign in terms:
                matches = {
                    (tag, Decimal(fact["val"]) * sign / USD_PER_MILLION)
                    for tag in tags
                    for fact in self.gaap.get(tag, {}).get("units", {}).get("USD", [])
                    if fact["accn"] == filing.accession and fact["end"] == end and fact.get("start") == start
                }
                amounts = {amount for _, amount in matches}
                if not amounts:
                    missing.append(" or ".join(tags))
                elif len(amounts) > 1:
                    conflicts.extend(sorted(matches))
                else:
                    values.append(amounts.pop())
            if filing.form == "10-K/A" and len(missing) == len(terms):
                continue
            if conflicts:
                return Observation(filing, "Conflict", missing_tags=tuple(missing), conflicting_values=tuple(conflicts))
            if missing:
                return Observation(filing, "Missing", missing_tags=tuple(missing))
            return Observation(filing, "Available", sum(values, Decimal(0)))
        raise ValueError(f"No annual Mosaic filing available for FY{year} {metric}")
