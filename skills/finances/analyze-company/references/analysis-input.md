# Analysis inputs

```sh
uv run --no-project --python '>=3.10' python scripts/analyze_company.py examples/mosaic.json \
  --report mosaic-report.html --valuation mosaic-valuation.html
uv run --no-project --python '>=3.10' python scripts/analyze_company.py examples/phosagro.json \
  --report phosagro-report.html --valuation phosagro-valuation.html
uv run --no-project --python '>=3.10' python scripts/analyze_company.py examples/microsoft.json --report microsoft-report.html
uv run --no-project --python '>=3.10' python scripts/analyze_company.py examples/costco.json \
  --report costco-report.html --valuation costco-valuation.html
```

Python 3.10 or newer runs the report and valuation builders. PDF import needs PyMuPDF, installed in an isolated environment by the command in [filing import](filings.md). Open a generated valuation HTML file in a browser with JavaScript enabled. To replay the Mosaic EDGAR comparison, pass `--refresh-edgar EMAIL` instead of `--valuation` with the Mosaic input. Treat the comparison as a review before updating any valuation inputs.

The [Costco example](../examples/costco.json) uses FY2023–FY2025 filings and the October 9, 2025 close of $942.89. It writes both reports and exits with code 2 for six open filing review items. In the valuation, open **Market-implied growth and margin** and **Location growth and capital spending** to compare five- and ten-year paths.

## Shared JSON format

- `schema_version` is `1`, `company` is a display name, and `scope` is `Consolidated`. Include `issuer.currency` and optionally issuer identity, market, ticker, and accounting method. Each entry in `periods` has a fiscal year label and exact `start` and `end` dates.
- Each item in `records` has a unique `metric`, `start`, `end` combination. Use a null `start` for point-in-time facts, including shares outstanding and market prices. Use date strings in `YYYY-MM-DD` form. Use decimal strings in base units. Set `unit` to `USD`, `RUB`, the relevant reporting currency, `shares`, or a price unit such as `USD/share`. The report and model handle conversion to millions.
- For each observation, keep the original amount and source. Each source supplies `filing_year`, `locator`, `url` and `title`. Use a filing URL with a page or section anchor when possible, or give the XBRL tag as the locator. Keep the original `observations`, `issues`, `document_issues` and `balance_checks` produced by the importer.
- Use `Matched` or `Single source` with a supported `value`. Keep `value` null for `Missing`, `Conflict` and `Review`. For an approved decision on a flagged figure, add `decision` on its record with the existing observation's exact `mode`, `value` and `source`, plus a `reason` explaining the accounting basis. The report shows the original flag and decision together. Reimport a failed or blocked balance sheet with corrected evidence before using its totals.
- Replay can return a `reopened_decision` containing the prior `decision`, `observations`, `unit` and reason for reopening. The active record stays unresolved. After reviewing the fresh observations and reconciling any affected balance sheet, replace `reopened_decision` with a new `decision` if the evidence supports one.

The names used in the financial report appear in `SECTIONS` in [build_report.py](../scripts/build_report.py). Match definitions before using them. The core net debt ratio expects borrowings excluding leases and separately reported lease liabilities. Preserve unfamiliar measures under distinct names. When an issuer defines merchandise gross margin using net sales, set `issuer.gross_margin_basis` to `net_sales` and supply `net_sales` and `merchandise_costs`. Operating and net margins continue to use total revenue. For example, reported depreciation **and other** and weighted-average diluted shares have their own identifiers in the Microsoft example. A ratio displays N/A when an input is unavailable or its denominator is invalid. Exit code 2 means the report was written with open review items or the requested valuation was blocked.

## Valuation

Use the `valuation` object in [the Mosaic example](../examples/mosaic.json) as a pattern. The generic annual model supports nonfinancial operating companies. Set `model` to `nonfinancial-fcff`, select `fiscal_year`, and provide a dated `quote` and editable Bear, Base and Bull scenario assumptions. Explain the basis of assumptions in `assumption_note`. Model rates are numeric percentages. Each annual operating driver takes five rates.

The script requires source-linked `revenue`, `operating_profit`, `noncontrolling`, `investments`, `shares_outstanding`, `market_price` and either `valuation_net_debt` or the accepted components of net debt. Use the issuer's outstanding shares for the relevant share class and set `share_date` when the share count differs from the balance date. Set `filing_date` for a refreshed base so the command rejects a quote dated before publication. Link the filing date in `assumption_references`. Document how debt and leases line up with the forecast. Quote and cash flows must use the same currency. A missing or unresolved required input stops the affected DCF. Optional gaps elsewhere remain visible alongside the available calculations.

An annual valuation can use `revenue_components` to project two or more reported revenue lines separately. The selected lines must sum to total revenue. Replace each scenario's `priceGrowth` array with `revenueGrowth`, an object that maps those metric names to their five annual rates. The model sums their forecasts. `volumeGrowth` still drives variable costs alongside `variableCostGrowth`. The valuation shows the source-linked revenue bridge and allows each component's growth rates to be edited.

Use `net_debt_bridge` when a missing annual borrowing balance has a quantified sensitivity. Provide the reported balance metric names under `add` and `subtract`. The `unquantified` object identifies the missing `metric` and supplies a `default`, `alternatives`, `reason`, and filing `source`. Express amounts as decimal strings in base currency units. The debt assumption affects the equity bridge. The reported input keeps its review flag. An optional `assumption_references` array of source objects links the forecast rationale to its evidence.

### Market-implied growth and margin

Add `valuation.growth_margin_map` to find the annual growth and sustainable operating margin combinations that reach the dated quote. Set `horizons` to increasing whole years from 1 to 30. Set `growth_rates` and `margins` to 2–15 increasing numeric percentages each. Growth must exceed −100%. Sustainable margins range from 0% to 100%. Readers can edit the axes in the HTML. Save lasting changes in the input JSON.

Growth applies to consolidated revenue by default. Set `revenue_component` to a metric in `revenue_components` to vary one revenue line. Other lines follow Base through Year 5 and keep their Year 5 growth in later forecast years. The margin is consolidated operating profit divided by revenue. It moves in equal annual steps from the reported annual margin to the selected margin at the end of each horizon. Each path uses the same dated quote, Base tax, explicit reinvestment, WACC, terminal growth and ROIC, and the same annual equity bridge. The selected margin carries into the terminal calculation.

The grids give value per share and solve for the margin that reaches the quote within the displayed range. Base and Bull reference points use each case's five-year growth CAGR and final margin, then price those points using the map's common Base financial assumptions. Historical growth uses the prior consecutive fiscal period with accepted figures for the same growth metric. The history links to the underlying sales, revenue and operating profit.

### Location expansion and capital spending

Set `valuation.location_growth` with `horizons`, `growth_rates`, an optional `revenue_component`, and `assumptions`. The growth axis uses consolidated revenue or the named modeled component. Up to 15 increasing annual growth targets above −100% can be priced for each horizon. Use `location_label` for the name shown in the history table. Supply `location_count` as a fiscal year-end fact and `net_location_openings` as an annual flow, both in `locations`. Add source-linked annual cash `capex` as an outflow. Net openings must reconcile to consecutive reported closing counts when available. Supply one consecutive nonnegative opening cohort per step before the sales ramp reaches maturity.

- `sameSiteGrowth` is annual growth in sales per mature location. `ramp` contains positive, nondecreasing percentages of mature sales from the opening year onward and ends at 100%. The latest reported sales calibrate productivity using opening cohorts and the ramp. The forecast adds the minimum whole number of locations to meet each annual target. Existing sites can exceed a target without new openings.
- `openingCost` is opening-year capex per net addition in starting-year currency. `otherCapex` is the starting-year investment in the existing estate and other projects. Use decimal strings in base currency units. The model checks their assumed split against the reported starting-year cash capex. Other capex scales with beginning-of-year locations. Both costs compound with `capexInflation` each year. Use `assumption_note` and `assumption_references` to identify estimates and attach the supporting sources.
- `referenceMargin` sets the final consolidated operating margin for the cash-flow forecast. It ramps linearly from the reported margin. Cash left is unlevered free cash flow after investment. The quote-matching margin is calculated separately. The comparison prices the same revenue path with Base capex as a share of revenue and with modeled location capex. Both use the Base assumptions for other inputs, including growth of other revenue lines and terminal reinvestment. Edit sales, cost and margin assumptions in the HTML or save them to the input JSON.

The Mosaic and PhosAgro examples select issuer-specific adapters for normalization, recovery and refinancing. Their profiles contain the underlying filing evidence and the modeled assumptions. Use the generic annual analysis for a new issuer when those company-specific models do not apply. The Microsoft example exercises the filing review and partial financial report.
