const test = require("node:test");
const assert = require("node:assert/strict");
const {runForecast} = require("../scripts/forecast_price.cjs");

function input() {
    const quote = (date, close) => ({date, close, url: "https://example.com/prices"});
    const scenario = (recoveryYears, multipleFactor) => ({recoveryYears, multipleFactor, salesGrowth: 0, marginOffset: 0});
    return {
        company: "Example Company", ticker: "EX", currency: "USD",
        filings: {
            annual: {publishedOn: "2023-03-01", url: "https://example.com/annual"},
            later: {publishedOn: "2024-03-01", url: "https://example.com/later"},
        },
        annualHistory: {
            "2020-12-31": [{filing: "annual", revenue: 1000, operatingProfit: 75}],
            "2021-12-31": [{filing: "annual", revenue: 1000, operatingProfit: 100}],
            "2022-12-31": [{filing: "annual", revenue: 1000, operatingProfit: 125}],
        },
        snapshots: {"2022-12-31": {
            flows: [{filing: "annual", label: "FY2022", sign: 1, revenue: 1000, operatingProfit: 100,
                depreciation: 100, capex: 100, interest: 0, dividends: 0}],
            balances: {filing: "annual", cash: 50, borrowings: 200, shares: 10, noncontrolling: 0, investments: 0},
        }},
        assumptions: {historyYears: 3, taxRate: 0, workingCapitalRate: 0,
            scenarios: {bear: scenario(5, 0.8), base: scenario(3, 1), bull: scenario(2, 1.2)}},
        forecast: {snapshot: "2022-12-31", quote: quote("2024-03-05", 20)},
        backtest: [{snapshot: "2022-12-31", quote: quote("2023-03-02", 20), actual: quote("2024-03-04", 24)}],
    };
}

test("one year of cash generation reduces net debt and moves the per-share forecast", () => {
    const result = runForecast(input());
    assert.equal(result.forecast.targetDate, "2025-03-05");
    assert.equal(result.forecast.cases.base.price, 30);
    assert.equal(result.forecast.cases.base.netDebt, 50);
    assert.equal(result.metrics.modelMae, 6);
    assert.equal(result.metrics.benchmarkMae, 4);
    assert.equal(result.metrics.directionHits, 1);
    assert.equal(result.metrics.benchmarkDirectionHits, 0);
});

test("a later comparative cannot change a historical forecast", () => {
    const data = input();
    const prior = runForecast(data).backtest[0];
    data.annualHistory["2022-12-31"].push({filing: "later", revenue: 1000, operatingProfit: 1});
    assert.deepEqual(runForecast(data).backtest[0], prior);
    data.snapshots["2022-12-31"].balances.filing = "later";
    assert.throws(() => runForecast(data), /published before/);
});
