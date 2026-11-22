import json
import subprocess
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from analysis_input import AnalysisInput, read_input
from build_valuation import valuation_data
from import_filings import review, validate_profile
from refresh_filings import compare_refresh


SKILL = Path(__file__).resolve().parents[1]


def snapshot(filing_year, records, issuer="example"):
    return {
        "schema_version": 1, "scope": "Consolidated", "company": "Example Corp",
        "issuer": {"id": issuer, "currency": "USD"},
        "periods": {str(year): {"start": f"{year}-01-01", "end": f"{year}-12-31"}
                    for year in range(2025, filing_year + 1)},
        "records": [record(filing_year, *item) for item in records],
    }


def record(filing_year, metric, year, value, status="Single source", *, unit="USD"):
    source = {"filing_year": filing_year, "locator": metric, "title": f"FY{filing_year} filing",
              "url": f"https://example.com/{filing_year}#{metric}"}
    return {"metric": metric, "start": f"{year}-01-01", "end": f"{year}-12-31", "unit": unit,
            "status": status, "value": value if status == "Single source" else None,
            "issues": [] if status == "Single source" else ["Review the source evidence."],
            "observations": {"PDF": [{"value": value, "source": source}]}}


class FilingRefreshTests(unittest.TestCase):
    def test_cli_distinguishes_new_periods_changed_comparatives_and_missing_evidence(self):
        before = snapshot(2025, [("revenue", 2025, "10"), ("profit", 2025, "2"), ("debt", 2025, None, "Missing"),
                                 ("out_of_scope", 2025, "4")])
        after = snapshot(2026, [("revenue", 2025, "11"), ("revenue", 2026, "13"),
                                ("profit", 2025, "2.0"), ("debt", 2025, None, "Missing")])
        with TemporaryDirectory() as work:
            prior, latest, html, audit = (Path(work) / file for file in ("prior.json", "latest.json", "review.html", "review.json"))
            prior.write_text(json.dumps(before), encoding="utf-8")
            latest.write_text(json.dumps(after), encoding="utf-8")
            run = subprocess.run([sys.executable, "-B", str(SKILL / "scripts/analyze_company.py"), str(prior),
                                  "--refresh-from", str(latest), "--report", str(html), "--refresh-json", str(audit)],
                                 capture_output=True, text=True)
            self.assertEqual(run.returncode, 2, run.stderr)
            data = json.loads(audit.read_text(encoding="utf-8"))
            rows = {(item["metric"], item["end"]): item for item in data["rows"]}
            changed = rows["revenue", "2025-12-31"]
            self.assertEqual((changed["status"], changed["difference"]), ("Changed", "1"))
            self.assertEqual(changed["baseline"], before["records"][0])
            self.assertEqual(changed["candidate"], after["records"][0])
            self.assertTrue(changed["requires_review"])
            self.assertEqual(rows["revenue", "2026-12-31"]["status"], "New")
            self.assertEqual(rows["profit", "2025-12-31"]["status"], "Confirmed")
            self.assertEqual(rows["debt", "2025-12-31"]["status"], "Unresolved")
            self.assertEqual(rows["out_of_scope", "2025-12-31"]["status"], "Outside refresh")
            links = html.read_text(encoding="utf-8")
            self.assertIn('href="https://example.com/2025#revenue"', links)
            self.assertIn('href="https://example.com/2026#revenue"', links)
            self.assertEqual(json.loads(prior.read_text()), before)
            self.assertEqual(json.loads(latest.read_text()), after)

    def test_refresh_flags_incompatible_units_and_checks_issuer(self):
        before = AnalysisInput(snapshot(2025, [("revenue", 2025, "10")]))
        after = snapshot(2026, [])
        after["records"] = [record(2026, "revenue", 2025, "10", unit="EUR")]
        (row,) = compare_refresh(before, AnalysisInput(after))["rows"]
        self.assertEqual(row["status"], "Unresolved")
        self.assertIsNone(row["difference"])
        with self.assertRaisesRegex(ValueError, "same issuer"):
            compare_refresh(before, AnalysisInput(snapshot(2026, [], issuer="other")))

    def test_metric_coverage_distinguishes_unreported_comparatives_from_expected_missing(self):
        profile = {"name": "Example Corp", "currency": "USD", "periods": snapshot(2026, [])["periods"],
                   "filings": {}, "metrics": {"assets": {"kind": "balance", "label": "Assets", "years": ["2026"]}}}
        validate_profile(profile)
        (record,) = review(profile, {"XBRL": {}}, [])["records"]
        self.assertEqual((record["year"], record["status"]), ("2026", "Missing"))

    def test_valuation_rejects_a_quote_before_the_selected_annual_filing(self):
        payload = read_input(SKILL / "examples/costco.json").payload
        payload["valuation"]["filing_date"] = "2025-10-10"
        with self.assertRaisesRegex(ValueError, "filing date.*quote date"):
            valuation_data(AnalysisInput(payload))
