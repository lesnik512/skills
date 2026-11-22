const assert = require("node:assert/strict");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const vm = require("node:vm");
const {execFileSync} = require("node:child_process");
const {test} = require("node:test");
const {valueCompany, phosagroRecoveryPaths, phosagroBreakEven, growthMarginForecast, growthMarginMap, locationGrowthPaths} =
    require("../scripts/valuation.js");

const skill = path.resolve(__dirname, "..");
const inputs = {revenue: 100, operatingProfit: -20, netDebt: 0, noncontrolling: 0, investments: 1000, shares: 1, price: 800};
const scenario = {
    priceGrowth: [0, 0, 0, 0, 0],
    volumeGrowth: [0, 0, 0, 0, 0],
    variableCostGrowth: [0, 0, 0, 0, 0],
    fixedCostGrowth: 0, variableShare: 70, tax: 25, daShare: 0, capexShare: 0,
    workingCapital: 0, wacc: 10, terminalGrowth: 0, terminalRoic: 10,
};

test("operating losses generate no cash tax benefit in the forecast or after Year 5", () => {
    const result = valueCompany(inputs, scenario);
    assert.ok(result.forecast.every(year => Math.abs(year.fcff + 20) < 1e-10));
    assert.ok(Math.abs(result.terminalCash + 20) < 1e-10);
    assert.ok(Math.abs(result.perShare - 800) < 1e-10);
    assert.ok(Math.abs(result.impliedMargin + 0.2) < 1e-10);
});

test("a slower recovery pays cash tax only after operating profit turns positive", () => {
    const {paths} = phosagroRecoveryPaths(inputs, {adjustedEbit: -20},
        {...inputs, operatingProfit: 20, changes: {}}, scenario, {});
    const {forecast} = paths.find(path => path.years === 5);
    const expectedCash = [-12, -4, 3, 9, 15];
    forecast.forEach((year, index) => assert.ok(Math.abs(year.fcff - expectedCash[index]) < 1e-10));
});

test("the break-even grid handles a transition from losses to taxable profits", () => {
    const ltm = {
        ...inputs,
        price: 1000,
        normalization: {
            adjustedEbit: -20, productRevenue: 100, sulphur: 50,
            cases: {Base: {price: 0, volume: 0, sulphur: -50, inputs: 0, priceRetention: 80}},
            inputs: 0, volumeContribution: 0.3,
        },
        liquidity: {
            asOf: "2025-12-31", eventsThrough: "2025-12-31", cash: 25, revolverLimit: 0, revolverExpiry: "2025-12-31",
            contracts: [], leases: [1, 0], leaseInterestNineMonths: 0, unallocatedStart: "2026-01", unallocated: 0,
            accruedInterest: 0, historicalProceeds: {}, historicalRepayments: {},
        },
    };
    const financing = {
        keyRate: 0, bankRate: 0, unallocatedRate: 0, replacementRate: 0, fxShock: 0,
        shortBankMonths: 12, unallocatedMonths: 12, putExercise: 0, dividends: 0,
    };
    const range = {reliefMin: 50, reliefMax: 50, rolloverMin: 0, rolloverMax: 0};
    const {paths} = phosagroBreakEven(ltm, "Base", scenario, {}, financing, range);
    assert.ok(Math.abs(paths[0].quoteRelief - 40) < 1e-10);
    assert.ok(Math.abs(paths[0].rows[0].perShare - 1037.5) < 1e-10);
    assert.ok(Math.abs(paths[0].rows[0].cells[0].minimumLiquidity - (25 + (3.75 - 1) / 12)) < 1e-10);
});

test("revenue components flow into consolidated cash flows and respond to edited growth rates", () => {
    const mixed = {
        ...inputs, operatingProfit: 20,
        revenueComponents: {sales: {value: 80}, fees: {value: 20}},
    };
    const assumptions = {...scenario, revenueGrowth: {sales: [0, 0, 0, 0, 0], fees: [25, 0, 0, 0, 0]}};
    delete assumptions.priceGrowth;
    const {forecast} = valueCompany(mixed, assumptions);
    assert.equal(forecast[0].revenue, 105);
    assert.deepEqual(forecast[0].componentRevenue, {sales: 80, fees: 25});
    assert.ok(Math.abs(forecast[0].fcff - 18.75) < 1e-10);
    assumptions.revenueGrowth.fees[0] = 50;
    assert.equal(valueCompany(mixed, assumptions).forecast[0].revenue, 110);
});

test("an assumed borrowing changes equity and market-implied margin while holding operating value fixed", () => {
    const original = valueCompany(inputs, scenario);
    const withDebt = valueCompany({...inputs, debtAssumption: {value: 5}}, scenario);
    assert.equal(withDebt.enterpriseValue, original.enterpriseValue);
    assert.equal(withDebt.perShare, original.perShare - 5);
    assert.ok(withDebt.impliedMargin > original.impliedMargin);
    assert.throws(() => valueCompany({...inputs, debtAssumption: {value: -5}}, scenario), /assumed borrowing/);
});

test("growth and margin map discounts both horizons and solves the threshold across loss years", () => {
    const settings = {horizons: [5, 10], growthRates: [0, 10], margins: [0, 1, 40, 100]};
    const targetMargin = 16;
    for (const years of settings.horizons) {
        const {forecast, perShare} = growthMarginForecast(inputs, scenario, null, years, 10, targetMargin);
        const discountedCashFlows = Array.from({length: years}, (_, index) => {
            const profit = -20 + (20 + targetMargin) * (index + 1) / years;
            return profit > 0 ? profit * 0.75 : profit;
        });
        const terminalValue = targetMargin * 0.75 / 0.10;
        const expected = 1000 + discountedCashFlows.reduce((a, b) => a + b, 0) + terminalValue;
        assert.equal(forecast.length, years);
        assert.ok(Math.abs(forecast.at(-1).revenue - 100 * 1.1 ** years) < 1e-9);
        assert.ok(Math.abs(perShare - expected) < 1e-9);
        const [{rows}] = growthMarginMap({...inputs, price: expected}, scenario, {...settings, horizons: [years]});
        assert.ok(Math.abs(rows[1].threshold - targetMargin) < 1e-7);
        assert.ok(rows[1].prices[1] < expected && rows[1].prices[2] > expected);
    }
    for (const [price, bound] of [[1, "below"], [1e6, "above"]]) {
        const maps = growthMarginMap({...inputs, price}, scenario, settings);
        assert.ok(maps.every(map => map.rows.every(row => row.bound === bound)));
    }
});

test("the varied revenue line compounds while the other line holds Base growth after Year 5", () => {
    const mixed = {
        ...inputs, operatingProfit: 20,
        revenueComponents: {sales: {value: 80}, fees: {value: 20}},
    };
    const base = {...scenario, revenueGrowth: {sales: [0, 0, 0, 0, 0], fees: [25, 0, 0, 0, 10]}};
    const {forecast, terminalRevenue} = growthMarginForecast(mixed, base, "sales", 10, 6, 40);
    assert.ok(Math.abs(forecast[0].revenue - 109.8) < 1e-9);
    assert.ok(Math.abs(forecast[0].margin - 0.22) < 1e-9);
    assert.ok(Math.abs(forecast.at(-1).margin - 0.4) < 1e-9);
    assert.ok(Math.abs(terminalRevenue - 80 * 1.06 ** 10 - 25 * 1.10 ** 6) < 1e-9);
});

test("site cohorts generate whole openings and spending that flows through the quote-matching margin", () => {
    const company = {...inputs, revenue: 90, operatingProfit: 18,
        price: 1000 - 23.35 / 1.10 - 12.90 / 1.10 ** 2 + (20.10 / 0.10) / 1.10 ** 2};
    const config = {
        startingSites: 10, startingYearSites: 8, recentOpenings: [2, 0], horizons: [2], growthRates: [0, 20],
        assumptions: {sameSiteGrowth: 0, openingCost: 10, otherCapex: 8, capexInflation: 0, referenceMargin: 20,
            ramp: [50, 80, 100]},
    };
    const [flat, expansion] = locationGrowthPaths(company, scenario, config);
    assert.deepEqual(flat.forecast.map(year => year.openings), [0, 0]);
    assert.deepEqual(expansion.forecast.map(year => year.openings), [3, 2]);
    assert.deepEqual(expansion.forecast.map(year => year.sites), [13, 15]);
    assert.deepEqual(expansion.forecast.map(year => year.sales), [111, 134]);
    assert.deepEqual(expansion.forecast.map(year => year.capex), [40, 33]);
    assert.ok(Math.abs(expansion.forecast[0].fcff + 23.35) < 1e-10);
    assert.ok(Math.abs(expansion.perShare - company.price) < 1e-10);
    assert.ok(Math.abs(expansion.projected.threshold - 20) < 1e-7);
    assert.ok(expansion.comparison.threshold < expansion.projected.threshold);
    config.assumptions.openingCost = 20;
    const [, expensive] = locationGrowthPaths(company, scenario, config);
    assert.equal(expensive.forecast[0].openings, 3);
    assert.ok(expensive.projected.threshold > expansion.projected.threshold);
    assert.equal(expensive.comparison.threshold, expansion.comparison.threshold);
    config.assumptions.ramp = [50, 80];
    assert.throws(() => locationGrowthPaths(company, scenario, config), /ending at 100%/);
});

function pilotPage(issuer, annualOnly = false, growthMap = false) {
    const dir = fs.mkdtempSync(path.join(os.tmpdir(), "analyze-company-test-"));
    try {
        let dataset = path.join(skill, "examples", issuer + ".json");
        if (annualOnly) {
            const input = JSON.parse(fs.readFileSync(dataset, "utf8"));
            delete input.valuation.adapter;
            delete input.valuation.interim;
            if (growthMap) input.valuation.growth_margin_map = {
                horizons: [5, 10], growth_rates: [2, 10], margins: [5, 25],
            };
            dataset = path.join(dir, "input.json");
            fs.writeFileSync(dataset, JSON.stringify(input));
        }
        const report = path.join(dir, "report.html");
        const valuation = path.join(dir, "valuation.html");
        execFileSync(process.env.PYTHON || "python3", ["-B", path.join(skill, "scripts/analyze_company.py"), dataset,
            "--report", report, "--valuation", valuation], {cwd: dir});
        const source = fs.readFileSync(valuation, "utf8");
        const ids = [...source.matchAll(/<[^>]+ id="([^"]+)"/g)].map(([, id]) => id);
        assert.equal(new Set(ids).size, ids.length);
        const elements = Object.fromEntries(ids.map(id => [id, {
            handlers: {},
            addEventListener(name, run) { this.handlers[name] = run; },
            replaceChildren() { this.innerHTML = ""; },
        }]));
        elements["valuation-data"].textContent = source.match(/type="application\/json">([^<]+)<\/script>/)[1];
        elements.issuer.value = annualOnly ? "company" : issuer;
        elements.period.value = "ltm";
        elements.scenario.value = "Base";
        vm.runInNewContext(source.match(/<script>([\s\S]*)<\/script>/)[1], {
            structuredClone, document: {getElementById: id => elements[id]},
        });
        const change = (id, value) => { elements[id].value = value; elements[id].handlers.change(); };
        return {elements, change, html: id => elements[id].innerHTML};
    } finally {
        fs.rmSync(dir, {recursive: true});
    }
}

test("Mosaic keeps the normalized and recovery valuations, refinancing gap and links", () => {
    const {html, change} = pilotPage("mosaic");
    assert.match(html("period-comparison"), /20\.99.*-21\.25.*22\.03/s);
    assert.match(html("recovery-comparison"), /3 years.*?19\.26.*?2029-06 \(\$130\.0m\)/s);
    change("period", "normalized");
    assert.match(html("normalization-bridge"), /-606\.2.*323\.0.*27\.7/s);
    assert.match(html("source-data"), /https:\/\/www\.sec\.gov\/Archives\/edgar/);
});

test("PhosAgro keeps recovery valuations, refinancing gaps and break-even links", () => {
    const {html, change} = pilotPage("phosagro");
    assert.match(html("period-comparison"), /2,126\.80.*401\.45.*1,449\.67/s);
    assert.match(html("phosagro-recovery-comparison"), /5 years.*?1,191\.60.*?2027-09/s);
    assert.match(html("phosagro-break-even-thresholds"), /Immediate.*38\.46%.*61\.04%.*2029-05/s);
    change("scenario", "Bull");
    assert.match(html("phosagro-break-even-thresholds"), /5 years.*51\.39%.*#page=20/s);
    assert.match(html("phosagro-break-even-grid"), /class="map-both"/);
});

test("annual-only view works without either pilot's funding model", () => {
    const {html, elements} = pilotPage("mosaic", true);
    assert.equal(elements.period.value, "annual");
    assert.ok(elements["ltm-details"].hidden);
    assert.ok(elements.recovery.hidden);
    assert.ok(elements["growth-margin-map"].hidden);
    assert.ok(elements["location-growth"].hidden);
    assert.match(html("source-data"), /FY2025.*2025-12-31/s);
    assert.match(html("cases"), /20\.99/);
});

test("an annual issuer can edit a consolidated growth map with filing-linked history", () => {
    const {html, elements} = pilotPage("mosaic", true, true);
    assert.equal(elements["growth-margin-map"].hidden, false);
    assert.match(html("growth-margin-context"), /18\.66.*2026-11-20/s);
    assert.match(html("growth-margin-history"), /FY2023.*sec\.gov.*FY2025/s);
    const grid = html("growth-margin-grids");
    assert.match(grid, /5-year path.*10-year path/s);
    const input = elements["map-margins"];
    input.value = "5, 50";
    input.handlers.input();
    assert.notEqual(html("growth-margin-grids"), grid);
    assert.match(html("growth-margin-grids"), /50%/);
    assert.match(html("cases"), /20\.99/);
    input.value = "5,";
    input.handlers.input();
    assert.equal(html("growth-margin-grids"), "");
    assert.match(elements["map-status"].textContent, /increasing/);
});
