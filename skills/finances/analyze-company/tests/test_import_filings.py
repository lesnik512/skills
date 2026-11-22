import unittest
from decimal import Decimal
from pathlib import Path
from tempfile import TemporaryDirectory

import pymupdf

from build_report import Source
from import_filings import Observation, company_facts, pdf_facts, review, xbrl_instance


def example_profile():
    return {
        "name": "Example Corp",
        "cik": "0000000012",
        "currency": "USD",
        "periods": {
            "2025": {"start": "2025-01-01", "end": "2025-12-31"},
            "2026": {"start": "2026-01-01", "end": "2026-12-31"},
        },
        "filings": {
            "2026": {
                "xbrl": {"form": "10-K", "accession": "test-2026", "url": "https://example.com/filing",
                         "namespaces": {"standard": "https://example.com/taxonomy"}},
                "pdf": {
                    "url": "https://example.com/statement.pdf",
                    "tables": {"income": {"page": 1, "years": ["2026", "2025"], "heading": "INCOME STATEMENT",
                                          "header": "Year ended December 31", "scale_label": "(USD in millions)",
                                          "scale": 1000000, "symbols": ["$"], "zero_tokens": ["—"]}},
                },
            },
        },
        "metrics": {
            "revenue": {"kind": "flow", "label": "Revenue", "tags": ["standard:Revenue"],
                        "table": "income", "pdf_labels": ["Revenue"]},
        },
    }


class FilingImportTests(unittest.TestCase):
    def test_companyfacts_filters_currency_accession_form_and_exact_reporting_dates(self):
        profile = example_profile()
        fact = {"start": "2026-01-01", "end": "2026-12-31", "accn": "test-2026", "form": "10-K", "val": 2000000}
        values = [fact, {**fact, "start": "2026-10-01", "val": 900},
                  {**fact, "form": "10-Q", "val": 800}, {**fact, "accn": "different", "val": 700}]
        data = {"cik": 12, "facts": {"standard": {"Revenue": {"units": {
            "USD": values, "EUR": [{**fact, "val": 600}],
        }}}}}
        result = company_facts(profile, data)
        self.assertEqual(list(result), [("2026", "revenue")])
        self.assertEqual(result["2026", "revenue"][0].value, Decimal(2000000))
        self.assertEqual(result["2026", "revenue"][0].source.url, "https://example.com/filing")
        with self.assertRaisesRegex(ValueError, "CIK differs"):
            company_facts(profile, {**data, "cik": 13})

    def test_xml_uses_consolidated_context_and_ignores_segment_facts(self):
        xml = """<xbrli:xbrl xmlns:xbrli="http://www.xbrl.org/2003/instance"
          xmlns:gaap="https://example.com/taxonomy" xmlns:iso4217="http://www.xbrl.org/2003/iso4217">
          <xbrli:context id="full"><xbrli:entity><xbrli:identifier scheme="urn:cik">0000000012</xbrli:identifier></xbrli:entity>
            <xbrli:period><xbrli:startDate>2026-01-01</xbrli:startDate><xbrli:endDate>2026-12-31</xbrli:endDate></xbrli:period>
          </xbrli:context>
          <xbrli:context id="part"><xbrli:entity><xbrli:identifier scheme="urn:cik">0000000012</xbrli:identifier>
             <xbrli:segment><gaap:Region>West</gaap:Region></xbrli:segment></xbrli:entity>
             <xbrli:period><xbrli:startDate>2026-01-01</xbrli:startDate><xbrli:endDate>2026-12-31</xbrli:endDate></xbrli:period>
          </xbrli:context>
          <xbrli:unit id="usd"><xbrli:measure>iso4217:USD</xbrli:measure></xbrli:unit>
          <gaap:Revenue contextRef="full" unitRef="usd" decimals="0">2000000</gaap:Revenue>
          <gaap:Revenue contextRef="part" unitRef="usd" decimals="0">900000</gaap:Revenue>
        </xbrli:xbrl>"""
        with TemporaryDirectory() as temp:
            path = Path(temp) / "instance.xml"
            path.write_text(xml, encoding="utf-8")
            result = xbrl_instance(example_profile(), "2026", path)
        self.assertEqual([fact.value for fact in result["2026", "revenue"]], [Decimal(2000000)])

    def test_pdf_keeps_column_dates_when_the_first_amount_is_missing(self):
        with pymupdf.open() as pdf:
            page = pdf.new_page()
            for x, y, text in [(40, 40, "INCOME STATEMENT"), (40, 63, "(USD in millions)"),
                               (40, 90, "Year ended December 31"), (350, 90, "2026"), (460, 90, "2025"),
                               (40, 113, "Revenue"), (447, 113, "$(1,234)")]:
                page.insert_text((x, y), text)
            snapshot = pdf.tobytes()
        result = pdf_facts(example_profile(), "2026", snapshot)
        self.assertIsNone(result["2026", "revenue"][0].value)
        fact = result["2025", "revenue"][0]
        self.assertEqual(fact.value, Decimal(-1234000000))
        self.assertEqual(fact.source.url, "https://example.com/statement.pdf#page=1")

    def test_pdf_handles_inline_units_note_numbers_and_year_suffixes(self):
        profile = example_profile()
        table = profile["filings"]["2026"]["pdf"]["tables"]["income"]
        table.update(header="Note", scale_label="USD in millions", year_suffix="FY", note_column_start=250)
        profile["metrics"]["revenue"]["pdf_before"] = "Other revenue"
        with pymupdf.open() as pdf:
            page = pdf.new_page()
            for x, y, text in [(40, 40, "INCOME STATEMENT"), (40, 90, "USD in millions"),
                               (270, 90, "Note"), (330, 90, "2026 FY"), (450, 90, "2025 FY"),
                               (40, 116, "Revenue"), (275, 116, "5"), (350, 116, "121"), (470, 116, "100"),
                               (40, 140, "Other revenue"),
                               (40, 160, "Revenue"), (350, 160, "9"), (470, 160, "9")]:
                page.insert_text((x, y), text)
            result = pdf_facts(profile, "2026", pdf.tobytes())
        self.assertEqual([fact.value for fact in result["2026", "revenue"]], [Decimal(121000000)])
        self.assertEqual([fact.value for fact in result["2025", "revenue"]], [Decimal(100000000)])

    def test_pdf_handles_dot_leaders_and_notes_tables_that_share_year_headers(self):
        profile = example_profile()
        table = profile["filings"]["2026"]["pdf"]["tables"]["income"]
        table.update(page=2, header="", end_heading="SECOND NOTE", scale_page=1, strip_dot_leaders=True)
        with pymupdf.open() as pdf:
            pdf.new_page().insert_text((40, 40), "(USD in millions)")
            page = pdf.new_page()
            for x, y, value in [(40, 40, "INCOME STATEMENT"), (350, 70, "2026"), (460, 70, "2025"),
                                (40, 90, "Revenue . . ."), (350, 90, "125"), (460, 90, "95"),
                                (40, 130, "SECOND NOTE"), (350, 150, "2026"), (460, 150, "2025"),
                                (40, 170, "Revenue . . ."), (350, 170, "500"), (460, 170, "500")]:
                page.insert_text((x, y), value)
            observation = pdf_facts(profile, "2026", pdf.tobytes())["2026", "revenue"]
            self.assertEqual([item.value for item in observation], [Decimal(125000000)])
            table["scale_label"] = "(USD in thousands)"
            wrong_scale = pdf_facts(profile, "2026", pdf.tobytes())["2026", "revenue"]
            self.assertTrue(all(item.value is None for item in wrong_scale))

    def test_pdf_only_rows_are_marked_single_source_when_both_sources_are_loaded(self):
        profile = example_profile()
        profile["metrics"]["revenue"]["sources"] = ["PDF"]
        source = Source(2026, "row", "https://example.com/report.pdf#page=1", "Report")
        result = review(profile, {"PDF": {("2026", "revenue"): [Observation(Decimal(10), source)]}, "XBRL": {}}, [])
        self.assertEqual([(item["status"], item["value"]) for item in result["records"]],
                         [("Missing", None), ("Single source", Decimal(10))])
        xbrl_only = review(profile, {"XBRL": {}}, [])
        self.assertTrue(all(item["status"] == "Missing" for item in xbrl_only["records"]))

    def test_mismatch_and_absence_are_not_published_as_values(self):
        source = Source(2026, "tag", "https://example.com", "Source")
        result = review(example_profile(), {
            "XBRL": {("2026", "revenue"): [Observation(Decimal(10), source)]},
            "PDF": {("2026", "revenue"): [Observation(Decimal(11), source)]},
        }, [])
        records = result["records"]
        self.assertEqual([(item["status"], item["value"]) for item in records], [("Missing", None), ("Conflict", None)])
        self.assertEqual(len(records[-1]["observations"]), 2)
        self.assertTrue(result["needs_review"])

    def test_balances_that_do_not_reconcile_are_sent_for_review(self):
        profile = example_profile()
        del profile["periods"]["2025"]
        profile["metrics"] = {key: {"kind": "balance", "label": key}
                              for key in ("assets", "liabilities", "equity")}
        source = Source(2026, "balance", "https://example.com", "Source")
        amounts = {("2026", metric): [Observation(Decimal(value), source)]
                   for metric, value in (("assets", "100"), ("liabilities", "65"), ("equity", "30"))}
        result = review(profile, {"XBRL": amounts, "PDF": amounts}, [])
        self.assertEqual(result["balance_checks"][0]["status"], "Failed")
        self.assertTrue(result["needs_review"])
        self.assertTrue(all(record["value"] is None and record["status"] == "Review" for record in result["records"]))


if __name__ == "__main__":
    unittest.main()
