const {readFileSync, writeFileSync} = require("node:fs");
const {resolve} = require("node:path");
const {parseArgs} = require("node:util");
const {recoveryProgress} = require("./valuation.js");

const flowFields = ["revenue", "operatingProfit", "depreciation", "capex", "interest", "dividends"];
const caseNames = ["bear", "base", "bull"];
const millisecondsPerDay = 86_400_000;

function number(value, label, minimum = -Infinity) {
    if (!Number.isFinite(value) || value < minimum) throw new Error(`Invalid ${label}.`);
    return value;
}

function day(value) {
    if (typeof value !== "string" || !/^\d{4}-\d{2}-\d{2}$/.test(value) ||
        !Number.isFinite(Date.parse(value)) || new Date(value).toISOString().slice(0, 10) !== value) {
        throw new Error(`Invalid date ${value}. Use YYYY-MM-DD.`);
    }
    return value;
}

function https(value) {
    if (typeof value !== "string" || new URL(value).protocol !== "https:") throw new Error("Use an HTTPS source URL.");
    return value;
}

function anniversary(value) {
    const date = new Date(day(value));
    const month = date.getUTCMonth();
    date.setUTCFullYear(date.getUTCFullYear() + 1);
    if (date.getUTCMonth() !== month) date.setUTCDate(0);
    return date.toISOString().slice(0, 10);
}

function checkQuote(quote) {
    day(quote.date);
    if (number(quote.close, "closing price", 0) === 0) throw new Error("A closing price must be positive.");
    https(quote.url);
}

function filing(data, id) {
    const source = data.filings[id];
    if (!source) throw new Error(`Unknown filing ${id}.`);
    day(source.publishedOn);
    https(source.url);
    if (source.index) https(source.index);
    return source;
}

function median(values) {
    const sorted = [...values].sort((a, b) => a - b);
    return (sorted[Math.floor((sorted.length - 1) / 2)] + sorted[Math.floor(sorted.length / 2)]) / 2;
}

function project(data, origin) {
    const {quote, snapshot: period} = origin;
    checkQuote(quote);
    const snapshot = data.snapshots[day(period)];
    if (!snapshot || !snapshot.flows.length) throw new Error(`Missing financial snapshot ${period}.`);
    const sources = [...new Set([...snapshot.flows.map(row => row.filing), snapshot.balances.filing])];
    const availableOn = sources.map(id => filing(data, id).publishedOn).sort().at(-1);
    if (period > availableOn || availableOn >= quote.date) {
        throw new Error(`The snapshot for ${period} must be published before the ${quote.date} quote.`);
    }

    const {historyYears, taxRate, workingCapitalRate, scenarios} = data.assumptions;
    if (!Number.isInteger(historyYears) || historyYears < 1) throw new Error("Use at least one annual margin observation.");
    if (number(taxRate, "tax rate", 0) > 1) throw new Error("Tax rate must be between zero and one.");
    number(workingCapitalRate, "working capital per change in sales", 0);
    const history = [];
    for (const end of Object.keys(data.annualHistory).sort().reverse()) {
        if (day(end) > period) continue;
        const known = data.annualHistory[end].filter(row => filing(data, row.filing).publishedOn < quote.date);
        known.sort((left, right) => filing(data, right.filing).publishedOn.localeCompare(filing(data, left.filing).publishedOn));
        if (!known.length) continue;
        const {revenue, operatingProfit, filing: id} = known[0];
        if (filing(data, id).publishedOn < end || number(revenue, `annual sales for ${end}`, 0) === 0) {
            throw new Error(`Invalid annual history for ${end}.`);
        }
        history.push({end, filing: id, revenue, operatingProfit, margin: number(operatingProfit, "annual operating profit") / revenue});
        if (history.length === historyYears) break;
    }
    if (history.length < historyYears) throw new Error(`Insufficient published annual margins for ${quote.date}.`);

    const fields = [...flowFields, "operatingAdjustment"];
    const trailing = Object.fromEntries(fields.map(key => [key, 0]));
    for (const row of snapshot.flows) {
        if (![1, -1].includes(row.sign)) throw new Error(`Invalid flow sign for ${row.label}.`);
        for (const field of fields) {
            const value = field === "operatingAdjustment" ? row[field] ?? 0 : row[field];
            trailing[field] += row.sign * number(value, `${row.label} ${field}`);
        }
    }
    const {revenue, operatingProfit, operatingAdjustment, depreciation, capex, interest, dividends} = trailing;
    const {cash, borrowings, shares, noncontrolling, investments} = snapshot.balances;
    for (const [name, value] of Object.entries({cash, borrowings, shares, noncontrolling, investments, depreciation, capex, dividends})) {
        number(value, name, 0);
    }
    if (revenue <= 0 || shares <= 0) throw new Error("Sales and shares must be positive.");
    const netDebt = borrowings - cash;
    const equityBridge = netDebt + noncontrolling - investments;
    const startingMargin = (operatingProfit + operatingAdjustment) / revenue;
    const cycleMargin = median(history.map(row => row.margin));
    const normalizedEbitda = revenue * cycleMargin + depreciation;
    const referenceMultiple = normalizedEbitda > 0 ? (quote.close * shares + equityBridge) / normalizedEbitda : null;

    const cases = Object.fromEntries(caseNames.map(name => {
        const {recoveryYears, salesGrowth, marginOffset, multipleFactor, exitMultiple} = scenarios[name];
        if (!Number.isInteger(recoveryYears) || recoveryYears < 0 || number(salesGrowth, "sales growth", -1) === -1) {
            throw new Error(`Invalid recovery or sales growth for ${name}.`);
        }
        const margin = startingMargin + (cycleMargin + number(marginOffset, "margin offset") - startingMargin) *
            recoveryProgress(1, recoveryYears);
        const sales = revenue * (1 + salesGrowth);
        const earnings = sales * margin;
        const da = depreciation * (1 + salesGrowth);
        const investment = capex * (1 + salesGrowth);
        const workingCapital = (sales - revenue) * workingCapitalRate;
        const taxes = Math.max(earnings - interest, 0) * taxRate;
        const retainedCash = earnings - interest - taxes + da - investment - workingCapital - dividends;
        const debt = netDebt - retainedCash;
        const multiple = exitMultiple == null ? (referenceMultiple ?? 0) * number(multipleFactor, "multiple factor", 0) :
            number(exitMultiple, "exit multiple", 0);
        const ebitda = earnings + da;
        const price = ebitda > 0 && multiple > 0 ? Math.max(0, ebitda * multiple - debt - noncontrolling + investments) / shares : null;
        if (price !== null) number(price, `${name} projected share price`, 0);
        return [name, {recoveryYears, salesGrowth, marginOffset, sales, margin, earnings, ebitda, retainedCash, netDebt: debt, multiple, price,
            change: price === null ? null : price / quote.close - 1}];
    }));
    return {period, quote, targetDate: anniversary(quote.date), snapshot, sources, history, trailing, startingMargin, cycleMargin,
        netDebt, referenceMultiple, cases};
}

function runForecast(data) {
    const forecast = project(data, data.forecast);
    const backtest = data.backtest.map(origin => {
        const test = project(data, origin);
        const {actual} = origin;
        checkQuote(actual);
        if (actual.date < test.targetDate || Date.parse(actual.date) - Date.parse(test.targetDate) > 7 * millisecondsPerDay ||
            actual.date > forecast.quote.date) {
            throw new Error(`Observed close for ${test.quote.date} must be from the anniversary or following week, by the forecast date.`);
        }
        const prediction = test.cases.base.price;
        const error = prediction === null ? null : Math.abs(prediction - actual.close);
        const benchmarkError = Math.abs(test.quote.close - actual.close);
        const direction = prediction !== null && Math.sign(prediction - test.quote.close) === Math.sign(actual.close - test.quote.close);
        return {...test, actual, error, benchmarkError, direction};
    });
    const scored = backtest.filter(row => row.error !== null);
    const average = measure => scored.length ? scored.reduce((sum, row) => sum + row[measure], 0) / scored.length : null;
    const metrics = {scored: scored.length, modelMae: average("error"), benchmarkMae: average("benchmarkError"),
        directionHits: scored.filter(row => row.direction).length,
        benchmarkDirectionHits: scored.filter(row => row.actual.close === row.quote.close).length};
    return {forecast, backtest, metrics};
}

function escape(value) {
    return String(value).replaceAll("&", "&amp;").replaceAll("<", "&lt;").replaceAll(">", "&gt;").replaceAll('"', "&quot;");
}

function link(url, text) {
    return `<a href="${escape(https(url))}">${escape(text)}</a>`;
}

function table(headings, rows) {
    return `<div class="scroll"><table><thead><tr>${headings.map(h => `<th scope="col">${escape(h)}</th>`).join("")}</tr></thead>
<tbody>${rows.map(row => `<tr>${row.map(cell => `<td>${cell}</td>`).join("")}</tr>`).join("\n")}</tbody></table></div>`;
}

function render(data, result) {
    const {forecast, backtest, metrics} = result;
    const {currency} = data;
    const amount = (value, digits = 1) => value === null ? "Review" : value.toLocaleString("en-US", {
        minimumFractionDigits: digits, maximumFractionDigits: digits,
    });
    const price = value => amount(value, 2);
    const percent = value => value === null ? "Review" : `${amount(value * 100)}%`;
    const multiple = value => `${amount(value, 2)}×`;
    const quote = value => link(value.url, `${value.date} · ${price(value.close)}`);
    const reference = id => {
        const source = filing(data, id);
        const index = source.index ? ` · ${link(source.index, source.publishedOn)}` : ` · ${source.publishedOn}`;
        return link(source.url, source.label ?? id) + index;
    };
    const cases = caseNames.map(name => {
        const row = forecast.cases[name];
        return [name[0].toUpperCase() + name.slice(1), row.recoveryYears, percent(row.salesGrowth),
            percent(forecast.cycleMargin + row.marginOffset), percent(row.margin),
            amount(row.ebitda), amount(row.retainedCash), amount(row.netDebt), multiple(row.multiple), price(row.price), percent(row.change)];
    });
    const outcomes = backtest.map(row => [row.period, [...new Set([...row.sources, ...row.history.map(item => item.filing)])].map(reference).join("<br>"),
        quote(row.quote), row.targetDate,
        quote(row.actual), ...caseNames.map(name => price(row.cases[name].price)), price(row.error), price(row.benchmarkError),
        row.error === null ? "Review" : row.direction ? "Yes" : "No"]);
    const contributions = forecast.snapshot.flows.map(row => [escape(row.label), row.sign, ...flowFields.map(key => amount(row[key])),
        amount(row.operatingAdjustment ?? 0), reference(row.filing)]);
    const history = forecast.history.map(row => [row.end, amount(row.revenue), amount(row.operatingProfit), percent(row.margin), reference(row.filing)]);
    const historicalNotes = backtest.filter(row => row.snapshot.note)
        .map(row => `<li>${row.period} · ${escape(row.snapshot.note)}</li>`).join("");
    const title = `${data.company} · 12-month share price`;
    return `<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>${escape(title)}</title><style>
body{font:16px/1.55 system-ui,sans-serif;max-width:86rem;margin:2rem auto;padding:0 1.25rem;color:#202b38}
h1,h2{line-height:1.2}a{color:#125b87}.scroll{overflow:auto}table{border-collapse:collapse;width:100%;font-variant-numeric:tabular-nums}
th,td{text-align:right;padding:.55rem .7rem;border-bottom:1px solid #dce3e9;white-space:nowrap}th{background:#eaf1f5}
th:first-child,td:first-child{text-align:left}summary{cursor:pointer}small{color:#415164}
</style><main><h1>${escape(title)}</h1>
<p>${escape(data.ticker)} · Starting close ${quote(forecast.quote)} ${escape(currency)} · Target ${forecast.targetDate} · Financial period through ${forecast.period}</p>
<h2>Price scenarios</h2><p>Amounts are in ${escape(currency)} millions, with share prices in ${escape(currency)} per share.</p>
${table(["Case", "Recovery years", "Sales growth", "Mid-cycle margin", "Year-one margin", "EBITDA", "Cash retained", "Net debt", "EV / EBITDA", "Target price", "Price change"], cases)}
<p>Starting operating margin after selected adjustments ${percent(forecast.startingMargin)}. Median annual margin ${percent(forecast.cycleMargin)}. Starting net debt ${amount(forecast.netDebt)}. Starting valuation on normalized EBITDA ${forecast.referenceMultiple === null ? "Unavailable" : multiple(forecast.referenceMultiple)}.</p>
<p>Target price comes from projected EBITDA times the exit multiple, with projected net debt and minority interests subtracted and non-operating investments added. The share count is held fixed. Cash taxes use a ${percent(data.assumptions.taxRate)} rate on positive pre-tax profit. Working capital moves by ${percent(data.assumptions.workingCapitalRate)} of the sales change. Prices use regular-session closes and exclude cash dividends.</p>
<h2>Historical backtest</h2>
<p>${metrics.scored} of ${backtest.length} dates scored. Mean absolute price error ${price(metrics.modelMae)} ${escape(currency)} for the base case and ${price(metrics.benchmarkMae)} ${escape(currency)} for the flat-price benchmark. The base case called the direction correctly ${metrics.directionHits} of ${metrics.scored} times. The flat forecast matched the direction on ${metrics.benchmarkDirectionHits} dates with an unchanged observed price.</p>
${table(["Fiscal close", "Available filings", "Starting close", "Target date", "Observed close", "Bear", "Base", "Bull", "Base error", "Flat error", "Direction hit"], outcomes)}
<p>Each test uses the financial record and completed annual margins available before its start. The later close is used for scoring. The outcome is the first regular-session close on or after the target date. The pilot applies retrospectively specified rules to a small set of dates.</p>
${historicalNotes ? `<ul>${historicalNotes}</ul>` : ""}
<details><summary>Forecast inputs and source filings</summary>
${table(["Flow period", "Sign", "Sales", "Operating profit", "D&A", "Capex", "Net interest", "Dividends", "Operating adjustment", "Filing"], contributions)}
${forecast.snapshot.note ? `<p>${escape(forecast.snapshot.note)}</p>` : ""}
<p>Cash ${amount(forecast.snapshot.balances.cash)}, borrowings ${amount(forecast.snapshot.balances.borrowings)}, minority interests ${amount(forecast.snapshot.balances.noncontrolling)}, equity-method investments ${amount(forecast.snapshot.balances.investments)}, shares ${amount(forecast.snapshot.balances.shares, 6)} million. ${reference(forecast.snapshot.balances.filing)}</p>
${table(["Fiscal close", "Sales", "Operating profit", "Operating margin", "Filing"], history)}</details>
</main></html>\n`;
}

if (require.main === module) {
    try {
        const {positionals: [input], values: {output}} = parseArgs({allowPositionals: true, options: {output: {type: "string"}}});
        if (!input || !output || resolve(input) === resolve(output)) throw new Error("Usage: node scripts/forecast_price.cjs INPUT --output REPORT.html");
        const data = JSON.parse(readFileSync(input, "utf8"));
        writeFileSync(output, render(data, runForecast(data)));
    } catch (error) {
        console.error(error.message);
        process.exitCode = 1;
    }
}

module.exports = {runForecast};
