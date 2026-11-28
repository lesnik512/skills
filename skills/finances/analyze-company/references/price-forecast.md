# Dated share-price forecast

Use this model when the user asks where a listed nonfinancial company might trade in 12 months. Produce bear, base and bull prices for the same date and score the base case against a flat-price benchmark. Choose a model that fits the company. The packaged model uses operating recovery and EV / EBITDA.

## Run the Mosaic pilot

From the skill directory, run the example with Node.js. Choose a local output path in the user's working directory.

```sh
node scripts/forecast_price.cjs examples/mosaic-price-forecast.json --output mosaic-price-forecast.html
```

The example uses the November 27, 2026 NYSE regular-session close and Mosaic’s Q3 2026 financial statements. Its five completed historical forecasts start at the first session close after the original FY2020 through FY2024 10-K filings. The FY2025 filing contributes to the current forecast, and its historical test would need a closing price from March 2027. The input links the SEC filing and its filing-date record, and the matching date in the Yahoo Finance daily close series.

## Prepare inputs

Copy the [example input](../examples/mosaic-price-forecast.json) and replace the issuer’s data. Financial amounts and share counts are in millions. Quotes are in the same currency per share. Use one share class and a consistent split basis for both the opening and outcome closes. Use the regular-session closing price field. Cash dividends are excluded from the measured price change.

- In `filings`, record the source URL and publication date. Use the filing index or other official timestamp evidence. Use a quote from a later calendar day so a filing released after the close cannot enter a forecast prematurely.
- In `snapshots`, record a completed fiscal year or a trailing 12-month period. The `flows` list adds annual rows with `sign` set to `1` and subtracts earlier year-to-date comparatives with `sign` set to `-1`. Each row identifies its source filing. Pair an interim period with the comparable prior-year period from the same disclosure where possible. Record capex and cash dividends as positive outflows and interest on a net expense basis. `operatingAdjustment` is a signed addition to reported operating earnings. Explain the adjustments and unresolved effects in the snapshot note.
- In `balances`, record interest-bearing borrowings, unrestricted cash and equivalents, common shares outstanding, the carrying value of non-operating investments and minority interests. Mosaic uses the sum of its three balance-sheet debt lines, including finance leases classified as debt, and excludes separately reported operating leases. Use the filed cover share count or date and link the applicable disclosure. Record cash distributions paid to shareholders, including consolidated minority interests, in the cash flow assumptions.
- In `annualHistory`, record sales and reported operating earnings for each fiscal close. Each entry is a list of filing-linked versions. The script selects the latest version published before the forecast start for each period, then takes the median of the requested number of annual margins through the snapshot date. Resolve conflicting versions with the filing notes before use. Never assign a comparative version an earlier publication date.
- In `forecast` and `backtest`, specify the financial snapshot and a sourced starting close. For a historical filing-date test, pick the first trading close strictly after filing. Score at the first close on or after the 12-month anniversary. Include only completed outcomes by the current forecast date. Validate dates against the quote series before saving them. A missing historical outcome can be omitted with a reason in the analysis.

## Model and interpretation

Year-one operating margin moves from adjusted trailing margin toward the median of available annual reported margins plus the scenario offset. The fraction is one divided by the scenario’s recovery years, capped at one. Recovery years of zero apply the target margin immediately. Mosaic reuses its two-, three- and five-year recovery timings and sales-growth assumptions based on the earlier selling-price and volume scenarios. Model inputs are editable in JSON. Rates are fractions. For example, `0.02` means two percentage points for `marginOffset`.

The base reference multiple is starting market enterprise value divided by trailing sales times the median annual margin plus trailing depreciation. Each scenario uses that reference multiple times its `multipleFactor`, or an explicit `exitMultiple` when supplied. Treat the reference multiple as a market-implied estimate conditional on the chosen earnings base.

Projected net debt equals starting net debt less the year’s retained cash. Retained cash includes projected operating profit and depreciation less tax, net interest, capex, incremental working capital and dividends. D&A and capex follow sales growth. Interest, dividends, shares, non-operating investments and minority interests carry forward at the supplied levels. The equity bridge subtracts projected net debt and minority interests and adds non-operating investments. The residual equity value per share has a floor of zero. Nonpositive EBITDA or a nonpositive multiple leaves the scenario in review.

Keep the same assumption policy across the historical cases and the current forecast. Test changes on subsequent observations or dates reserved before making changes. Show mean absolute price error for both the scenario and the flat-price benchmark. A direction hit requires the predicted sign of the change to match the observed sign, with unchanged recorded separately by the sign of zero. Label a retrospective pilot as such and report the sample size. If the forecast underperforms the flat benchmark, state the result and identify the largest misses before using it for a decision.
