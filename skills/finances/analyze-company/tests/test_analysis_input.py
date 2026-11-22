import json
import subprocess
import sys
import unittest
from decimal import Decimal
from pathlib import Path
from tempfile import TemporaryDirectory

from analysis_input import AnalysisInput, Figure, Source, read_input
from build_report import annual_facts, calculate
from build_valuation import valuation_data


SKILL = Path(__file__).resolve().parents[1]


class AnalysisInputTests(unittest.TestCase):
    def test_retailer_margin_uses_net_sales_and_keeps_membership_revenue_in_operating_margin(self):
        filings = {metric: Source(2025, metric, "https://example.com/report#page=5", "Current filing")
                   for metric in ("revenue", "net_sales", "merchandise_costs", "operating_profit")}
        facts = {(2025, metric): Figure(Decimal(value), (filings[metric],))
                 for metric, value in (("revenue", 105), ("net_sales", 100),
                                       ("merchandise_costs", 88), ("operating_profit", 5))}
        result = calculate(facts, 2025, gross_margin_basis="net_sales")
        self.assertEqual(result["gross_profit"].value, Decimal(12))
        self.assertEqual(result["gross_margin"].value, Decimal("0.12"))
        self.assertLess(result["operating_margin"].value, Decimal("0.05"))
        self.assertEqual(set(result["gross_margin"].sources), {filings["net_sales"], filings["merchandise_costs"]})

    def test_microsoft_keeps_unresolved_candidates_and_june_reporting_dates(self):
        inputs = read_input(SKILL / "examples/microsoft.json")
        unresolved = {(metric, end): record["status"] for (metric, start, end), record in inputs.records.items()
                      if inputs.accepted[metric, start, end] is None}
        self.assertEqual(unresolved, {
            ("depreciation_and_other", "2024-06-30"): "Conflict",
            ("depreciation_and_other", "2025-06-30"): "Conflict",
            ("depreciation_and_other", "2026-06-30"): "Missing",
            ("short_term_debt", "2026-06-30"): "Missing",
        })
        accepted = {key: figure for key, figure in inputs.accepted.items() if figure is not None}
        self.assertEqual(len(accepted), 53)
        for figure in accepted.values():
            self.assertTrue(any("sec.gov/Archives/" in source.url for source in figure.sources))
            self.assertTrue(any("#page=" in source.url for source in figure.sources))
        fy26 = calculate(annual_facts(inputs), 2026)
        self.assertEqual(fy26["revenue"].value, Decimal("331839"))
        self.assertEqual(fy26["cash_after_capex"].value, Decimal("66987"))
        self.assertIsNone(fy26["ebitda"])
        self.assertIsNone(fy26["net_debt"])
        self.assertIsNone(inputs.annual(2026, "shares_outstanding", balance=True, unit="shares"))

    def test_review_decision_uses_an_existing_observation_and_keeps_history(self):
        file = SKILL / "examples/microsoft.json"
        payload = json.loads(file.read_text(encoding="utf-8"))
        key = "depreciation_and_other", "2023-07-01", "2024-06-30"
        target = next(record for record in payload["records"] if (record["metric"], record["start"], record["end"]) == key)
        candidate = target["observations"]["PDF"][0]
        target["decision"] = {"mode": "PDF", "source": candidate["source"], "value": "1", "reason": "Exercise approval"}
        with self.assertRaisesRegex(ValueError, "existing observation and reason"):
            AnalysisInput(payload)
        target["decision"]["value"] = candidate["value"]
        inputs = AnalysisInput(payload)
        self.assertEqual(inputs.accepted[key].value, Decimal(candidate["value"]))
        self.assertEqual(inputs.records[key]["status"], "Conflict")
        self.assertEqual(len(inputs.records[key]["observations"]["PDF"]), 2)
        self.assertTrue(inputs.needs_review)

    def test_unresolved_required_dcf_figure_does_not_block_the_financial_report(self):
        source = SKILL / "examples/mosaic.json"
        payload = json.loads(source.read_text(encoding="utf-8"))
        market = next(record for record in payload["records"] if record["metric"] == "market_price")
        market.update(status="Review", value=None, issues=["Quote date requires confirmation."])
        with TemporaryDirectory() as temp:
            temp = Path(temp)
            source, report, model = (temp / name for name in ("input.json", "report.html", "valuation.html"))
            source.write_text(json.dumps(payload), encoding="utf-8")
            run = subprocess.run([sys.executable, "-B", str(SKILL / "scripts/analyze_company.py"), str(source),
                                  "--report", str(report), "--valuation", str(model)], capture_output=True, text=True)
            self.assertEqual(run.returncode, 2, run.stderr)
            html = report.read_text(encoding="utf-8")
            self.assertIn("market_price is missing or unresolved", html)
            self.assertIn("Quote date requires confirmation", html)
            self.assertIn("<table", html)
            self.assertFalse(model.exists())

    def test_balances_use_a_complete_set_of_lease_components(self):
        filing = Source(2025, "lease note", "https://example.com/report#page=7", "Lease note")
        facts = {(2025, metric): Figure(Decimal(value), (filing,))
                 for metric, value in (("current_operating_lease", 208), ("current_finance_lease", 78),
                                       ("noncurrent_operating_lease", 2460), ("noncurrent_finance_lease", 1401))}
        result = calculate(facts, 2025)
        self.assertEqual(result["short_leases"].value, Decimal(286))
        self.assertEqual(result["long_leases"].value, Decimal(3861))
        facts[2025, "current_finance_lease"] = None
        self.assertIsNone(calculate(facts, 2025)["short_leases"])

    def test_annual_only_valuation_requires_outstanding_shares_and_uses_exact_dates(self):
        inputs = read_input(SKILL / "examples/mosaic.json")
        settings = inputs.payload["valuation"]
        del settings["interim"], settings["adapter"]
        with self.assertRaisesRegex(ValueError, "shares_outstanding"):
            settings["share_date"] = "2026-09-30"
            valuation_data(inputs)
        settings["share_date"] = "2025-12-31"
        model = valuation_data(inputs)["company"]
        self.assertNotIn("ltm", model)
        self.assertEqual(model["balanceDate"], "2025-12-31")
        self.assertEqual(model["priceDate"], "2026-11-20")
        self.assertEqual(model["shares"], Decimal("317.408647"))

    def test_location_growth_uses_linked_facts_and_reconciles_net_openings(self):
        payload = json.loads((SKILL / "examples/mosaic.json").read_text(encoding="utf-8"), parse_float=Decimal)
        payload["valuation"]["location_growth"] = {
            "horizons": [5, 10], "growth_rates": [5],
            "assumptions": {"sameSiteGrowth": 2, "ramp": [75, 100], "openingCost": "1000000",
                            "otherCapex": "10000000", "capexInflation": 3, "referenceMargin": 10},
        }
        source = {"filing_year": 2025, "locator": "Location counts", "url": "https://example.com/annual#locations",
                  "title": "Location counts in annual report"}
        for year, count, added in [("2024", "40", "2"), ("2025", "43", "3")]:
            for metric, amount in [("location_count", count), ("net_location_openings", added)]:
                period = payload["periods"][year]
                payload["records"].append({
                    "metric": metric, "start": period["start"] if metric == "net_location_openings" else None,
                    "end": period["end"], "unit": "locations", "status": "Single source", "value": amount,
                    "observations": {"PDF": [{"value": amount, "source": source}]}, "issues": [],
                })
        model = valuation_data(AnalysisInput(payload))["mosaic"]["locationGrowth"]
        self.assertEqual((model["startingSites"], model["startingYearSites"]), (43, 40))
        self.assertEqual(model["recentOpenings"], [3, 2])
        self.assertIn("https://example.com/annual#locations", model["history"][-1][2])
        latest = payload["records"][-1]
        latest["value"] = latest["observations"]["PDF"][0]["value"] = "4"
        with self.assertRaisesRegex(ValueError, "net openings must reconcile"):
            valuation_data(AnalysisInput(payload))
