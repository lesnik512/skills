const driverFields = [
    ["priceGrowth", "Selling price"],
    ["volumeGrowth", "Sales volume"],
    ["variableCostGrowth", "Variable cost per unit"],
];

const rateFields = [
    ["fixedCostGrowth", "Annual fixed cost growth"],
    ["variableShare", "Variable share of starting costs"],
    ["tax", "Operating tax rate"],
    ["daShare", "D&A as a share of revenue"],
    ["capexShare", "Capex as a share of revenue"],
    ["workingCapital", "Working capital per change in revenue"],
    ["wacc", "Nominal WACC"],
    ["terminalGrowth", "Nominal terminal growth"],
    ["terminalRoic", "Terminal return on invested capital"],
];

function operatingTaxes(profit, taxRate) {
    return Math.max(profit, 0) * taxRate;
}

function valuationRates(scenario) {
    const rates = Object.fromEntries(
        rateFields.map(([key]) => [key, scenario[key] / 100]),
    );
    const {fixedCostGrowth, variableShare, tax, daShare, capexShare, wacc, terminalGrowth, terminalRoic} = rates;
    if (Object.values(rates).some(value => !Number.isFinite(value))) {
        throw new RangeError("Enter finite assumptions.");
    }
    if (fixedCostGrowth <= -1 || variableShare < 0 || variableShare > 1 ||
        tax < 0 || tax >= 1 || daShare < 0 || capexShare < 0) {
        throw new RangeError("Check the fixed cost growth, variable cost share, taxes, D&A and capex.");
    }
    if (wacc <= 0 || terminalGrowth <= -1 || wacc <= terminalGrowth ||
        terminalRoic <= Math.max(0, terminalGrowth)) {
        throw new RangeError("WACC must be positive and exceed terminal growth. Terminal ROIC must also be positive and exceed terminal growth. Growth must be above −100%.");
    }
    return rates;
}

function cashFlowYear(revenue, lastRevenue, operatingProfit, rates, year, capex = revenue * rates.capexShare) {
    const cashTax = operatingTaxes(operatingProfit, rates.tax);
    const nopat = operatingProfit - cashTax;
    const depreciation = revenue * rates.daShare;
    const workingCapitalInvestment = (revenue - lastRevenue) * rates.workingCapital;
    const fcff = nopat + depreciation - capex - workingCapitalInvestment;
    return {
        revenue, operatingProfit, margin: operatingProfit / revenue, cashTax, nopat,
        depreciation, capex, workingCapitalInvestment, fcff, presentValue: fcff / (1 + rates.wacc) ** year,
    };
}

function valueCompany(inputs, scenario) {
    const rates = valuationRates(scenario);
    const {fixedCostGrowth, variableShare} = rates;
    const components = Object.entries(inputs.revenueComponents ?? {});
    let componentRevenue = Object.fromEntries(components.map(([metric, component]) => [metric, component.value]));
    const revenueRates = components.length ? components.map(([metric]) => scenario.revenueGrowth?.[metric]) :
        [scenario.priceGrowth];
    const driverRates = [...revenueRates, scenario.volumeGrowth, scenario.variableCostGrowth];
    if (driverRates.some(values => !Array.isArray(values) || values.length !== 5 ||
        values.some(value => !Number.isFinite(value) || value <= -100))) {
        throw new RangeError("Annual operating drivers must have five finite rates above −100%.");
    }
    const startingCosts = inputs.revenue - inputs.operatingProfit;
    let revenue = inputs.revenue;
    let variableCost = startingCosts * variableShare;
    let fixedCost = startingCosts * (1 - variableShare);
    const forecast = [];

    for (let index = 0; index < 5; index++) {
        const volume = 1 + scenario.volumeGrowth[index] / 100;
        const costPerUnit = 1 + scenario.variableCostGrowth[index] / 100;
        const lastRevenue = revenue;
        if (components.length) {
            componentRevenue = Object.fromEntries(components.map(([metric]) => [
                metric, componentRevenue[metric] * (1 + scenario.revenueGrowth[metric][index] / 100),
            ]));
            revenue = Object.values(componentRevenue).reduce((sum, value) => sum + value, 0);
        } else {
            revenue *= (1 + scenario.priceGrowth[index] / 100) * volume;
        }
        variableCost *= volume * costPerUnit;
        fixedCost *= 1 + fixedCostGrowth;
        forecast.push({
            ...cashFlowYear(revenue, lastRevenue, revenue - variableCost - fixedCost, rates, index + 1),
            ...(components.length ? {componentRevenue} : {}),
        });
    }
    return valueForecast(inputs, rates, forecast);
}

function valueForecast(inputs, rates, forecast) {
    const {tax, wacc, terminalGrowth, terminalRoic} = rates;
    const assumedDebt = inputs.debtAssumption?.value ?? 0;
    if (!Number.isFinite(assumedDebt) || assumedDebt < 0) {
        throw new RangeError("Enter a finite, nonnegative assumed borrowing.");
    }
    const explicitValue = forecast.reduce((sum, year) => sum + year.presentValue, 0);
    const {margin: terminalMargin, revenue} = forecast.at(-1);
    const terminalRevenue = revenue * (1 + terminalGrowth);
    const terminalReinvestment = terminalGrowth / terminalRoic;
    const terminalOperatingProfit = terminalMargin * terminalRevenue;
    const terminalCashPerAfterTaxMargin = terminalRevenue * (1 - terminalReinvestment);
    const terminalDiscount = (wacc - terminalGrowth) * (1 + wacc) ** forecast.length;
    const terminalCash = (terminalOperatingProfit - operatingTaxes(terminalOperatingProfit, tax)) *
        (1 - terminalReinvestment);
    const terminalValue = terminalCash / terminalDiscount;
    const enterpriseValue = explicitValue + terminalValue;
    const equityBridge = -inputs.netDebt - assumedDebt - inputs.noncontrolling + inputs.investments;
    const equityValue = enterpriseValue + equityBridge;
    const perShare = equityValue / inputs.shares;
    const marketEnterpriseValue = inputs.price * inputs.shares - equityBridge;
    const requiredAfterTaxMargin = (marketEnterpriseValue - explicitValue) * terminalDiscount /
        terminalCashPerAfterTaxMargin;
    const impliedMargin = requiredAfterTaxMargin > 0 ? requiredAfterTaxMargin / (1 - tax) : requiredAfterTaxMargin;
    if (![enterpriseValue, equityValue, perShare, impliedMargin].every(Number.isFinite)) {
        throw new RangeError("These assumptions produce values outside the model’s numeric range.");
    }
    return {
        forecast, explicitValue, terminalMargin, terminalRevenue, terminalReinvestment, terminalCash, terminalValue,
        enterpriseValue, equityValue, perShare, impliedMargin, marketEnterpriseValue,
    };
}

function forecastComponentGrowth(scenario, metric, year) {
    const growth = scenario.revenueGrowth[metric][Math.min(year, 5) - 1];
    if (!Number.isFinite(growth) || growth <= -100) {
        throw new RangeError("Revenue component growth must be finite and above −100%.");
    }
    return growth;
}

function valueMarginPath(inputs, rates, plan, targetMargin) {
    const startingMargin = inputs.operatingProfit / inputs.revenue;
    const forecast = plan.map((item, index) => {
        const {revenue, capex} = item;
        const year = index + 1;
        const margin = year === plan.length ? targetMargin / 100 :
            startingMargin + (targetMargin / 100 - startingMargin) * year / plan.length;
        return {...item, ...cashFlowYear(revenue, plan[index - 1]?.revenue ?? inputs.revenue,
            revenue * margin, rates, year, capex)};
    });
    return valueForecast(inputs, rates, forecast);
}

function growthMarginForecast(inputs, scenario, revenueComponent, years, growth, targetMargin) {
    if (!Number.isFinite(growth) || growth <= -100) {
        throw new RangeError("Sales growth must be finite and above −100%.");
    }
    const components = revenueComponent ? Object.entries(inputs.revenueComponents) : [];
    const componentRevenue = Object.fromEntries(components.map(([key, item]) => [key, item.value]));
    let revenue = inputs.revenue;
    const plan = [];
    for (let year = 1; year <= years; year++) {
        if (components.length) {
            for (const [key] of components) {
                const annualGrowth = key === revenueComponent ? growth : forecastComponentGrowth(scenario, key, year);
                componentRevenue[key] *= 1 + annualGrowth / 100;
            }
            revenue = Object.values(componentRevenue).reduce((sum, amount) => sum + amount, 0);
        } else {
            revenue *= 1 + growth / 100;
        }
        plan.push({revenue, ...(components.length ? {componentRevenue: {...componentRevenue}} : {})});
    }
    return valueMarginPath(inputs, valuationRates(scenario), plan, targetMargin);
}

function quoteMargin(value, quote, minimum, maximum, firstPrice = value(minimum), lastPrice = value(maximum)) {
    if (firstPrice >= quote) return {threshold: minimum, bound: "below"};
    if (lastPrice < quote) return {threshold: maximum, bound: "above"};
    for (let step = 0; step < 36; step++) {
        const midpoint = (minimum + maximum) / 2;
        if (value(midpoint) >= quote) maximum = midpoint;
        else minimum = midpoint;
    }
    return {threshold: maximum};
}

function growthMarginMap(inputs, scenario, {horizons, revenueComponent, growthRates, margins}) {
    const validAxis = values => values.length >= 2 && values.length <= 15 &&
        values.every((value, index) => Number.isFinite(value) && (index === 0 || value > values[index - 1]));
    if (!validAxis(growthRates) || growthRates[0] <= -100 || !validAxis(margins) ||
        margins[0] < 0 || margins.at(-1) > 100) {
        throw new RangeError("Enter 2 to 15 increasing values per axis, with growth above −100% and margins from 0% to 100%.");
    }
    return horizons.map(years => ({
        years,
        rows: growthRates.map(growth => {
            const value = margin => growthMarginForecast(inputs, scenario, revenueComponent, years, growth, margin).perShare;
            const prices = margins.map(value);
            return {growth, prices, ...quoteMargin(value, inputs.price, margins[0], margins.at(-1), prices[0], prices.at(-1))};
        }),
    }));
}

const locationFields = [
    ["sameSiteGrowth", "Existing-site sales growth (%)"],
    ["openingCost", "Capex per net opening"],
    ["otherCapex", "Other capex in base fiscal year"],
    ["capexInflation", "Annual capex inflation (%)"],
    ["referenceMargin", "Operating margin for cash flow (%)"],
];

function locationPlan(inputs, scenario, config, years, growth) {
    const {revenueComponent, startingSites, startingYearSites, recentOpenings, assumptions} = config;
    const {sameSiteGrowth, openingCost, otherCapex, capexInflation, ramp} = assumptions;
    const salesBase = revenueComponent ? inputs.revenueComponents[revenueComponent].value : inputs.revenue;
    const otherRevenue = Object.fromEntries(Object.entries(revenueComponent ? inputs.revenueComponents : {})
        .filter(([metric]) => metric !== revenueComponent).map(([metric, {value}]) => [metric, value]));
    const matureShares = ramp.map(rate => rate / 100);
    const equivalentSites = (sites, cohorts) => cohorts.reduce(
        (equivalent, openings, age) => equivalent - openings * (1 - matureShares[age]), sites);
    let sites = startingSites;
    let cohorts = recentOpenings.slice(0, ramp.length - 1);
    let salesPerMatureSite = salesBase / equivalentSites(sites, cohorts);
    let targetSales = salesBase;
    const plan = [];
    for (let year = 1; year <= years; year++) {
        targetSales *= 1 + growth / 100;
        salesPerMatureSite *= 1 + sameSiteGrowth / 100;
        cohorts = [0, ...cohorts].slice(0, ramp.length - 1);
        const existing = equivalentSites(sites, cohorts);
        const required = (targetSales / salesPerMatureSite - existing) / matureShares[0];
        const openings = Math.max(0, Math.ceil(required - 1e-9));
        if (!Number.isSafeInteger(sites + openings)) {
            throw new RangeError("The growth assumptions produce a location count outside the model’s numeric range.");
        }
        const sales = (existing + openings * matureShares[0]) * salesPerMatureSite;
        const inflation = (1 + capexInflation / 100) ** year;
        const expansionCapex = openings * openingCost * inflation;
        const continuingCapex = otherCapex * (sites / startingYearSites) * inflation;
        sites += openings;
        if (cohorts.length) cohorts[0] = openings;
        for (const key of Object.keys(otherRevenue)) {
            otherRevenue[key] *= 1 + forecastComponentGrowth(scenario, key, year) / 100;
        }
        const revenue = sales + Object.values(otherRevenue).reduce((total, amount) => total + amount, 0);
        plan.push({sales, targetSales, revenue, openings, sites, expansionCapex, continuingCapex,
            capex: expansionCapex + continuingCapex});
    }
    return plan;
}

function locationGrowthPaths(inputs, scenario, config) {
    const {growthRates, horizons, assumptions, recentOpenings} = config;
    const {sameSiteGrowth, openingCost, otherCapex, capexInflation, referenceMargin, ramp} = assumptions;
    if (growthRates.length < 1 || growthRates.length > 15 || growthRates.some((rate, index) =>
        !Number.isFinite(rate) || rate <= -100 || (index > 0 && rate <= growthRates[index - 1]))) {
        throw new RangeError("Enter 1 to 15 increasing sales growth rates above −100%.");
    }
    if (locationFields.some(([key]) => !Number.isFinite(assumptions[key])) || sameSiteGrowth <= -100 ||
        capexInflation <= -100 || openingCost < 0 || otherCapex < 0 || referenceMargin < 0 || referenceMargin > 100) {
        throw new RangeError("Enter nonnegative capex, rates above −100% and a reference margin from 0% to 100%.");
    }
    if (ramp.length > recentOpenings.length + 1 || ramp.at(-1) !== 100 ||
        ramp.some((rate, index) => !Number.isFinite(rate) || rate <= 0 || (index > 0 && rate < ramp[index - 1]))) {
        throw new RangeError("Enter positive, nondecreasing sales ramp percentages ending at 100%. Each earlier step needs a reported opening cohort.");
    }
    const rates = valuationRates(scenario);
    return horizons.flatMap(years => growthRates.map(growth => {
        const plan = locationPlan(inputs, scenario, config, years, growth);
        const revenuePlan = plan.map(({revenue}) => ({revenue}));
        const priceAtMargin = margin => valueMarginPath(inputs, rates, plan, margin).perShare;
        const priceWithRevenueCapex = margin => valueMarginPath(inputs, rates, revenuePlan, margin).perShare;
        return {years, growth, ...valueMarginPath(inputs, rates, plan, referenceMargin),
            projected: quoteMargin(priceAtMargin, inputs.price, 0, 100),
            comparison: quoteMargin(priceWithRevenueCapex, inputs.price, 0, 100)};
    }));
}

function mosaicBenchmark(annualRevenue, profile, assumptions) {
    const changes = {price: 0, volume: 0, inputs: 0};
    let benchmarkRevenue = annualRevenue;
    for (const [key, {externalSales, priceRetention, volumeContribution, drivers}] of Object.entries(profile.segments)) {
        const scenario = assumptions[key];
        if (Object.keys(drivers).some(driver => !Number.isFinite(scenario[driver]) || scenario[driver] <= 0)) {
            throw new RangeError("Enter positive prices, shipment volumes and input costs in the mid-cycle case.");
        }
        const priceChange = externalSales * (scenario.price / drivers.price.history[2] - 1);
        const volumeChange = (externalSales + priceChange) * (scenario.volume / drivers.volume.history[2] - 1);
        benchmarkRevenue += priceChange + volumeChange;
        changes.price += priceChange * priceRetention / 100;
        changes.volume += volumeChange * volumeContribution / 100;
        for (const [driver, {quantity, history}] of Object.entries(drivers)) {
            if (quantity !== undefined) changes.inputs -= quantity * (scenario[driver] - history[2]);
        }
    }
    const benchmarkEbit = profile.anchorEbit + changes.price + changes.volume + changes.inputs;
    if (![benchmarkRevenue, benchmarkEbit].every(Number.isFinite) || benchmarkRevenue <= 0) {
        throw new RangeError("These mid-cycle assumptions produce values outside the model’s numeric range.");
    }
    return {changes, benchmarkRevenue, benchmarkEbit};
}

function normalizedInputs(ltm, annualRevenue, profile, name) {
    const benchmark = mosaicBenchmark(annualRevenue, profile, profile.cases[name]);
    return applyStartingMargin(ltm, benchmark);
}

function phosagroNormalizedInputs(ltm, profile, name, sulphur = profile.cases[name].sulphur) {
    const {price, volume, inputs, priceRetention} = profile.cases[name];
    if ([price, volume, inputs].some(value => !Number.isFinite(value) || value <= -100) ||
        !Number.isFinite(sulphur) || sulphur < -100 ||
        !rateIsValid(priceRetention)) {
        throw new RangeError("Enter price, volume and cost changes above −100%, or −100% for sulphur, and price retention from 0% to 100%.");
    }
    const priceChange = profile.productRevenue * price / 100;
    const volumeChange = (profile.productRevenue + priceChange) * volume / 100;
    const changes = {
        price: priceChange * priceRetention / 100,
        volume: volumeChange * profile.volumeContribution,
        sulphur: -profile.sulphur * sulphur / 100 * (1 + volume / 100),
        inputs: -profile.inputs * inputs / 100 * (1 + volume / 100),
    };
    const benchmarkRevenue = ltm.revenue + priceChange + volumeChange;
    const benchmarkEbit = profile.adjustedEbit + Object.values(changes).reduce((sum, change) => sum + change, 0);
    return applyStartingMargin(ltm, {changes, benchmarkRevenue, benchmarkEbit});
}

function applyStartingMargin(ltm, benchmark) {
    const {benchmarkRevenue, benchmarkEbit} = benchmark;
    const startingMargin = benchmarkEbit / benchmarkRevenue;
    const operatingProfit = ltm.revenue * startingMargin;
    if (![benchmarkRevenue, operatingProfit].every(Number.isFinite) || benchmarkRevenue <= 0) {
        throw new RangeError("These mid-cycle assumptions produce values outside the model’s numeric range.");
    }
    return {
        ...ltm, ...benchmark, periodLabel: "Normalized", operatingProfit, startingMargin,
    };
}

const recoveryPeriods = [[0, "Immediate"], [2, "2 years"], [3, "3 years"], [5, "5 years"]];

function recoveryProgress(year, years) {
    return years === 0 ? 1 : Math.min(year / years, 1);
}

const recoveryCurves = [[0.5, "Front-loaded"], [1, "Even"], [2, "Back-loaded"]];

const phosagroCycleFields = [
    ["price", "Product selling price"],
    ["volume", "Shipment volume"],
    ["sulphur", "Sulphur and sulphuric acid cost per unit"],
    ["inputs", "Other key raw-material cost per unit"],
    ["priceRetention", "Price change retained in operating profit"],
];

function valueRecoveryPaths(ltm, annualRevenue, profile, normalized, scenario, timing) {
    const recent = Object.fromEntries(Object.entries(profile.segments).map(([key, {drivers}]) => [
        key, Object.fromEntries(Object.entries(drivers).map(([driver, {history}]) => [
            driver, driver === "volume" ? history[2] + history[4] - history[3] : history[4],
        ])),
    ]));
    const {changes} = mosaicBenchmark(annualRevenue, profile, recent);
    const gaps = Object.fromEntries(Object.keys(changes).map(key => [key, normalized.changes[key] - changes[key]]));
    return valueOperatingRecovery(ltm, normalized, scenario, timing, gaps, profile.adjustedEbit, profile.careAndMaintenance);
}

function phosagroRecoveryPaths(ltm, profile, normalized, scenario, timing) {
    return valueOperatingRecovery(ltm, normalized, scenario, timing, normalized.changes, profile.adjustedEbit);
}

function valueOperatingRecovery(ltm, normalized, scenario, timing, gaps, trailingEbit, annualCare = 0) {
    const target = valueCompany(normalized, scenario);
    const other = normalized.operatingProfit - trailingEbit - annualCare -
        Object.values(gaps).reduce((sum, gap) => sum + gap, 0);
    const operatingGaps = {...gaps, other};
    const tax = scenario.tax / 100;
    const discount = 1 + scenario.wacc / 100;
    const paths = recoveryPeriods.map(([years, label]) => {
        let cumulativeCash = 0;
        const forecast = target.forecast.map((year, index) => {
            const yearNumber = index + 1;
            const progress = recoveryProgress(yearNumber, years);
            const effects = Object.fromEntries(Object.entries(operatingGaps).map(([key, gap]) => [
                key, -gap * (1 - progress ** (timing[key] ?? 1)) * year.revenue / ltm.revenue,
            ]));
            const careCost = yearNumber <= (timing.careThrough ?? 0) ? annualCare : 0;
            const profitEffect = Object.values(effects).reduce((sum, effect) => sum + effect, 0) - careCost;
            const operatingProfit = year.operatingProfit + profitEffect;
            const cashTax = operatingTaxes(operatingProfit, tax);
            const fcff = year.fcff + profitEffect - (cashTax - year.cashTax);
            cumulativeCash += fcff;
            return {
                effects, careCost, operatingProfit, margin: operatingProfit / year.revenue, cashTax, fcff, cumulativeCash,
            };
        });
        const firstPositive = forecast.findIndex(year => year.fcff > 0);
        const lossYears = firstPositive === -1 ? forecast.length : firstPositive;
        const cashShortfall = lossYears === 0 ? 0 : Math.abs(forecast[lossYears - 1].cumulativeCash);
        const explicitCashChange = forecast.reduce(
            (sum, year, index) => sum + (year.fcff - target.forecast[index].fcff) / discount ** (index + 1), 0,
        );
        const perShare = target.perShare + explicitCashChange / ltm.shares;
        if (![perShare, cashShortfall, cumulativeCash].every(Number.isFinite)) {
            throw new RangeError("These recovery assumptions produce values outside the model’s numeric range.");
        }
        return {
            years, label, perShare, delta: perShare - ltm.price,
            firstPositiveYear: firstPositive === -1 ? null : firstPositive + 1, cashShortfall, forecast,
        };
    });
    return {paths, target, slowest: paths.findLast(path => path.delta >= 0)};
}

const monthIndex = iso => Number(iso.slice(0, 4)) * 12 + Number(iso.slice(5, 7)) - 1;
const rateIsValid = value => Number.isFinite(value) && value >= 0 && value <= 100;
const cashTolerance = 1e-8;

function simulateLiquidity(forecast, profile, assumptions, scheduledCash) {
    const {replacementRate, revolverRate = 0, usableRevolver = 0, dividends, renewRevolver = false} = assumptions;
    const start = monthIndex(profile.asOf);
    const expiry = monthIndex(profile.revolverExpiry);
    const committedLimit = profile.revolverLimit * usableRevolver / 100;
    let cash = profile.cash;
    let replacementDebt = 0;
    let revolver = 0;
    const months = Array.from({length: forecast.length * 12}, (_, offset) => {
        const index = start + offset + 1;
        const calendarYear = Math.floor(index / 12);
        const monthOfYear = index % 12 + 1;
        const month = `${calendarYear}-${String(monthOfYear).padStart(2, "0")}`;
        const year = Math.floor(offset / 12) + 1;
        const limit = index < expiry || renewRevolver ? committedLimit : 0;
        const {legacyInterest, shortFinancing, namedPrincipal, otherPrincipal, refinancing, maturities,
            exitPayment = 0, actualBorrowing = 0, actualPrincipal = 0} = scheduledCash({month, index, year, calendarYear});
        const interest = legacyInterest + (replacementDebt * replacementRate + revolver * revolverRate) / 1200;
        const dividendPayment = monthOfYear % 3 === 0 ? dividends / 4 : 0;
        const fcff = forecast[year - 1].fcff / 12;
        replacementDebt += refinancing;
        cash += fcff - interest - shortFinancing - namedPrincipal - otherPrincipal - exitPayment - dividendPayment +
            refinancing + actualBorrowing - actualPrincipal;

        const expiryRepayment = Math.max(0, revolver - limit);
        cash -= expiryRepayment;
        revolver -= expiryRepayment;
        const draw = Math.max(0, Math.min(-cash, limit - revolver));
        const voluntaryRepayment = Math.max(0, Math.min(cash, revolver));
        revolver += draw - voluntaryRepayment;
        cash += draw - voluntaryRepayment;
        const undrawn = limit - revolver;
        const liquidity = cash + undrawn;
        return {
            month, year, fcff, interest, shortFinancing, namedPrincipal, otherPrincipal, refinancing, exitPayment,
            actualBorrowing, actualPrincipal, replacementDebt, dividendPayment, draw, maturities,
            revolverRepayment: expiryRepayment + voluntaryRepayment, revolver, undrawn, cash, liquidity,
        };
    });
    const minimumLiquidity = Math.min(...months.map(month => month.liquidity));
    const peakRevolver = Math.max(...months.map(month => month.revolver));
    if (![minimumLiquidity, peakRevolver, cash, replacementDebt].every(Number.isFinite)) {
        throw new RangeError("These financing assumptions produce cash flows outside the model’s numeric range.");
    }
    return {months, minimumLiquidity, peakRevolver, peakReplacement: replacementDebt,
        firstGap: months.find(month => month.liquidity < -cashTolerance)};
}

function liquidityForecast(forecast, profile, assumptions) {
    const {legacyRate, replacementRate, revolverRate, usableRevolver, namedRefinancing, shortTermRollover,
        exitCash, dividends} = assumptions;
    if (![legacyRate, replacementRate, revolverRate, usableRevolver, namedRefinancing, shortTermRollover].every(rateIsValid) ||
        ![exitCash, dividends].every(value => Number.isFinite(value) && value >= 0)) {
        throw new RangeError("Enter percentages between 0 and 100 and nonnegative amounts for dividends and exit payments.");
    }
    const maturityByMonth = new Map();
    for (const [due, principal] of profile.namedMaturities) {
        const month = monthIndex(due);
        maturityByMonth.set(month, (maturityByMonth.get(month) ?? 0) + principal);
    }
    let legacyDebt = profile.legacyDebt;
    return simulateLiquidity(forecast, profile, assumptions, ({month, index, year, calendarYear}) => {
        const legacyInterest = legacyDebt * legacyRate / 1200;
        const repaymentShare = month === profile.shortTermPaymentMonth ? 1 - shortTermRollover / 100 : 0;
        const shortDebtRepayment = profile.shortDebt * repaymentShare;
        const shortFinancing = shortDebtRepayment + profile.supplierFinancing * repaymentShare;
        const namedPrincipal = maturityByMonth.get(index) ?? 0;
        const otherPrincipal = (year === 1 ? profile.currentMaturities : profile.otherMaturities[calendarYear] ?? 0) / 12;
        const refinancing = namedPrincipal * namedRefinancing / 100;
        const exitPayment = month === profile.exitMonth ? exitCash : 0;
        legacyDebt -= shortDebtRepayment + namedPrincipal + otherPrincipal;
        return {legacyInterest, shortFinancing, namedPrincipal, otherPrincipal, refinancing, exitPayment};
    });
}

function phosagroLiquidityForecast(forecast, profile, assumptions) {
    const {keyRate, bankRate, unallocatedRate, replacementRate, fxShock, shortBankMonths, unallocatedMonths,
        putExercise, rollover, dividends} = assumptions;
    if (![keyRate, bankRate, unallocatedRate, replacementRate, putExercise, rollover].every(rateIsValid) ||
        !Number.isFinite(fxShock) || fxShock <= -100 || !Number.isFinite(dividends) || dividends < 0 ||
        !Number.isInteger(shortBankMonths) || shortBankMonths < 3 || shortBankMonths > 12 ||
        !Number.isInteger(unallocatedMonths) || unallocatedMonths < 2 || unallocatedMonths > 60) {
        throw new RangeError("Check rates, rollover, FX and maturity months. Use months 3–12 for current bank debt and 2–60 for unallocated borrowing.");
    }
    const contracts = profile.contracts.map(contract => ({...contract, balance: contract.principal}));
    const shortDue = monthIndex(profile.asOf) + shortBankMonths;
    const unallocatedStart = monthIndex(profile.unallocatedStart);
    const unallocatedDue = unallocatedStart + unallocatedMonths;
    const eventCutoff = monthIndex(profile.eventsThrough);
    const leaseTotal = profile.leases[0] + profile.leases[1];
    let leaseBalance = leaseTotal;
    let unallocatedDebt = 0;
    return simulateLiquidity(forecast, profile, assumptions, ({month, index, year}) => {
        const leasePayment = Math.min(leaseBalance, (year === 1 ? profile.leases[0] / 12 : profile.leases[1] / 48));
        let legacyInterest = unallocatedDebt * unallocatedRate / 1200 +
            (leaseBalance / leaseTotal) * profile.leaseInterestNineMonths / 9;
        leaseBalance -= leasePayment;
        if (index === unallocatedStart) legacyInterest += profile.accruedInterest;
        let shortFinancing = 0;
        let namedPrincipal = 0;
        let otherBankPrincipal = 0;
        const maturities = [];
        const recordedBankPayment = profile.historicalRepayments[month] ?? 0;
        let actualPrincipal = recordedBankPayment;
        for (const contract of contracts) {
            const {currency, rateType, rate, issued, currentBank, maturity, put, repurchaseDate, repurchase,
                previousSpread} = contract;
            if (issued && issued.slice(0, 7) >= month) continue;
            const fx = index <= eventCutoff ? profile.septemberFx[currency] :
                profile.quoteFx[currency] * (currency === "RUB" ? 1 : 1 + fxShock / 100);
            const keyRateForMonth = index <= eventCutoff ? profile.assumptions.keyRate : keyRate;
            const spread = repurchaseDate && month < repurchaseDate.slice(0, 7) ? previousSpread : rate;
            const coupon = rateType === "fixed" ? rate : rateType === "bankRate" ? bankRate : keyRateForMonth + spread;
            legacyInterest += contract.balance * fx * coupon / 1200;
            if (currentBank && recordedBankPayment) {
                contract.balance *= 1 - recordedBankPayment / profile.currentBank;
            }
            if (repurchaseDate?.startsWith(month)) {
                contract.balance -= repurchase;
                actualPrincipal += repurchase;
            }
            const maturityDue = currentBank ? index === shortDue : maturity?.startsWith(month);
            const isPut = !maturityDue && put?.startsWith(month);
            const principal = contract.balance * (maturityDue ? 1 : isPut ? putExercise / 100 : 0);
            contract.balance -= principal;
            if (principal > 0 && !currentBank) {
                maturities.push({link: isPut ? contract.putLink : contract.maturityLink, principal: principal * fx});
            }
            if (rateType !== "bankRate") namedPrincipal += principal * fx;
            else if (currentBank) shortFinancing += principal * fx;
            else otherBankPrincipal += principal * fx;
        }
        if (index === unallocatedStart) unallocatedDebt = profile.unallocated;
        if (index === unallocatedDue) {
            otherBankPrincipal += unallocatedDebt;
            if (unallocatedDebt > 0) maturities.push({link: profile.unallocatedLink, principal: unallocatedDebt});
            unallocatedDebt = 0;
        }
        if (shortFinancing > 0) maturities.push({link: profile.currentBankLink, principal: shortFinancing});
        const refinancing = (shortFinancing + namedPrincipal + otherBankPrincipal) * rollover / 100;
        return {legacyInterest, shortFinancing, namedPrincipal, otherPrincipal: otherBankPrincipal + leasePayment, refinancing, maturities,
            actualBorrowing: profile.historicalProceeds[month] ?? 0, actualPrincipal};
    });
}

function phosagroRolloverRange(months) {
    let minimum = 0;
    let maximum = 100;
    let binding = null;
    let upperBinding = null;
    for (const month of months) {
        const {liquidity: cash, rolloverChange} = month;
        if (Math.abs(rolloverChange) <= cashTolerance) {
            if (cash < -cashTolerance) return {minimum: null, binding: month, upperBinding: null};
            continue;
        }
        const limit = -cash / rolloverChange * 100;
        if (rolloverChange > 0 && limit > minimum) {
            minimum = limit;
            binding = month;
        }
        if (rolloverChange < 0 && limit < maximum) {
            maximum = limit;
            upperBinding = month;
        }
    }
    if (minimum - maximum > 1e-9) return {minimum: null, binding: binding ?? upperBinding, upperBinding};
    return {minimum: Math.min(100, minimum), maximum: Math.max(0, maximum), binding, upperBinding};
}

function sulphurValuationThreshold(pathAt, quote) {
    const initial = pathAt(0).forecast;
    const full = pathAt(100).forecast;
    const taxTransitions = initial.map((year, index) => -year.operatingProfit * 100 /
        (full[index].operatingProfit - year.operatingProfit)).filter(relief => relief > 0 && relief < 100);
    const points = [...new Set([0, ...taxTransitions, 100])].sort((a, b) => a - b)
        .map(relief => ({relief, value: pathAt(relief).perShare}));
    const crossesAt = (a, b) => a.value === b.value ? null :
        a.relief + (b.relief - a.relief) * (quote - a.value) / (b.value - a.value);
    const first = points.findIndex(point => point.value >= quote);
    const last = points.findLastIndex(point => point.value >= quote);
    const reliefAtThreshold = first < 0 ? null : first === 0 ? 0 : crossesAt(points[first - 1], points[first]);
    const upperRelief = last >= 0 && last < points.length - 1 ? crossesAt(points[last], points[last + 1]) : null;
    const left = crossesAt(points[0], points[1]);
    const right = crossesAt(points.at(-2), points.at(-1));
    const quoteRelief = reliefAtThreshold === null ?
        (right > 100 ? right : left < 0 ? left : null) :
        (reliefAtThreshold || upperRelief || (left < 0 ? left : reliefAtThreshold));
    return {quoteRelief, reliefAtThreshold, upperRelief};
}

function phosagroBreakEven(ltm, name, scenario, timing, financing, range) {
    const {normalization, liquidity} = ltm;
    const {reliefMin, reliefMax, rolloverMin, rolloverMax} = range;
    if (![reliefMin, reliefMax, rolloverMin, rolloverMax].every(rateIsValid) ||
        reliefMin > reliefMax || rolloverMin > rolloverMax) {
        throw new RangeError("Enter grid bounds from 0% to 100%, with each maximum at least its minimum.");
    }
    if (liquidity.revolverLimit !== 0) {
        throw new RangeError("The break-even funding calculation requires zero modeled committed credit.");
    }
    const axis = (low, high) => low === high ? [low] :
        Array.from({length: 11}, (_, step) => low + (high - low) * step / 10);
    const sulphurAxis = axis(reliefMin, reliefMax);
    const rolloverAxis = axis(rolloverMin, rolloverMax);
    const recoveryByRelief = new Map();
    const recoveryAt = relief => {
        if (!recoveryByRelief.has(relief)) {
            const inputs = phosagroNormalizedInputs(ltm, normalization, name, -relief);
            recoveryByRelief.set(relief, phosagroRecoveryPaths(ltm, normalization, inputs, scenario, timing).paths);
        }
        return recoveryByRelief.get(relief);
    };
    const noRelief = recoveryAt(0);
    const zeroOperatingCash = noRelief[0].forecast.map(() => ({fcff: 0}));
    const [noRollover, fullRollover] = [0, 100].map(rollover =>
        phosagroLiquidityForecast(zeroOperatingCash, liquidity, {...financing, rollover}).months);
    const selectedRelief = -normalization.cases[name].sulphur;
    const [noReliefMargin, fullReliefMargin] = [0, -100].map(sulphur =>
        phosagroNormalizedInputs(ltm, normalization, name, sulphur).startingMargin);
    const paths = noRelief.map((path, index) => {
        const pathAt = relief => recoveryAt(relief)[index];
        const {quoteRelief, reliefAtThreshold, upperRelief} = sulphurValuationThreshold(pathAt, ltm.price);
        const quoteMargin = quoteRelief === null ? null :
            noReliefMargin + (fullReliefMargin - noReliefMargin) * quoteRelief / 100;
        if (quoteRelief !== null && ![quoteRelief, quoteMargin].every(Number.isFinite)) {
            throw new RangeError("These break-even assumptions produce values outside the model’s numeric range.");
        }
        const monthsAt = forecast => {
            let operatingCash = 0;
            return noRollover.map((month, offset) => {
                operatingCash += forecast[month.year - 1].fcff / 12;
                return {...month, liquidity: month.liquidity + operatingCash,
                    rolloverChange: fullRollover[offset].liquidity - month.liquidity};
            });
        };
        const rows = sulphurAxis.map(relief => {
            const {perShare, forecast} = pathAt(relief);
            const months = monthsAt(forecast);
            const meetsQuote = perShare >= ltm.price;
            const cells = rolloverAxis.map(rollover => {
                const minimumLiquidity = Math.min(...months.map(month =>
                    month.liquidity + month.rolloverChange * rollover / 100));
                return {funded: minimumLiquidity >= -cashTolerance, minimumLiquidity};
            });
            return {relief, perShare, meetsQuote, cells};
        });
        return {
            label: path.label, years: path.years, quoteRelief, quoteMargin, reliefAtThreshold, upperRelief,
            fundingAtQuote: reliefAtThreshold === null ? null : phosagroRolloverRange(monthsAt(pathAt(reliefAtThreshold).forecast)),
            fundingAtSelectedRelief: Number.isFinite(selectedRelief) && selectedRelief <= 100 ?
                phosagroRolloverRange(monthsAt(pathAt(selectedRelief).forecast)) : null,
            rows, bothPass: rows.some(row => row.meetsQuote && row.cells.some(cell => cell.funded)),
        };
    });
    return {paths, rolloverAxis, selectedRelief};
}

const financingFields = [
    ["usableRevolver", "Usable committed revolver (%)", 100],
    ["legacyRate", "Existing debt cash interest (%)", 100],
    ["revolverRate", "Revolver cash interest (%)", 100],
    ["dividends", "Annual cash dividends, USD m", null],
    ["exitCash", "December 2026 exit payments, USD m", null],
];

const refinancingFields = [
    ["namedRefinancing", "Named debt maturities refinanced (%)", 100],
    ["shortTermRollover", "Short-term and supplier funding rolled (%)", 100],
    ["replacementRate", "Replacement debt interest (%)", 100],
];

const phosagroFundingFields = [
    ["keyRate", "Bank of Russia key rate (%)", 100],
    ["bankRate", "September bank debt cash rate (%)", 100],
    ["unallocatedRate", "Unallocated October borrowing rate (%)", 100],
    ["fxShock", "Change in RUB per foreign currency (%)", null, -99.99],
    ["shortBankMonths", "Current bank repayment month from September 2026", 12, 3, 1],
    ["unallocatedMonths", "Unallocated borrowing term from October (months)", 60, 2, 1],
    ["putExercise", "June 2028 bond put exercised (%)", 100],
    ["dividends", "Annual cash dividends, RUB m", null],
];

if (typeof module !== "undefined") {
    module.exports = {valueCompany, normalizedInputs, phosagroNormalizedInputs, valueRecoveryPaths, phosagroRecoveryPaths, recoveryProgress,
        liquidityForecast, phosagroLiquidityForecast, phosagroBreakEven, growthMarginForecast, growthMarginMap, locationGrowthPaths};
}

function startValuation() {
    const companies = JSON.parse(document.getElementById("valuation-data").textContent);
    const issuer = document.getElementById("issuer");
    const period = document.getElementById("period");
    const scenarioSelect = document.getElementById("scenario");
    const controls = document.getElementById("assumptions");
    const status = document.getElementById("status");
    const assumedBorrowing = document.getElementById("debt-assumption");
    const growthRatesInput = document.getElementById("map-growth-rates");
    const marginsInput = document.getElementById("map-margins");
    const locationControls = document.getElementById("location-controls");
    const recoverySettings = document.getElementById("recovery-settings");
    const recoverySelection = document.getElementById("recovery-selection");
    const liquiditySettings = document.getElementById("liquidity-settings");
    const financingCase = document.getElementById("financing-case");
    const liquiditySettingsValues = {...companies.mosaic?.liquidity.assumptions, renewRevolver: true};
    const phosagroFinancing = document.getElementById("phosagro-financing-case");
    const phosagroFundingSettings = document.getElementById("phosagro-funding-settings");
    const phosagroFunding = {...companies.phosagro?.liquidity.assumptions};
    const phosagroRecoverySettings = document.getElementById("phosagro-recovery-settings");
    const phosagroRecoverySelection = document.getElementById("phosagro-recovery-selection");
    const phosagroTiming = {price: 1, volume: 1, sulphur: 1, inputs: 1};
    const breakEvenPath = document.getElementById("phosagro-break-even-path");
    const breakEvenRange = {reliefMin: 0, reliefMax: 100, rolloverMin: 0, rolloverMax: 100};
    let customFinancing = false;
    let partialRollover = false;
    const recoveryTiming = {price: 0.5, volume: 2, inputs: 1, careThrough: 1};
    let detailPeriod = 3;
    let phosagroDetailPeriod = 3;
    const money = (number, decimals = 1) => number.toLocaleString("en", {
        maximumFractionDigits: decimals, minimumFractionDigits: decimals,
    });
    const percent = number => `${money(number * 100)}%`;
    const signedPercent = number => `${number > 0 ? "+" : ""}${percent(number)}`;
    const percentList = value => value.split(",").map(part => part.trim() ? Number(part) : NaN);
    const formatThreshold = ({threshold, bound}) =>
        `${bound === "below" ? "≤ " : bound === "above" ? "&gt; " : ""}${money(threshold, 2)}%`;
    const numberCell = value => `<td>${value}</td>`;
    const row = (label, values) => `<tr><th scope="row">${label}</th>${values.map(numberCell).join("")}</tr>`;
    const explainedRow = (label, values, note) => `<tr><th scope="row">${label}</th>${values.map(numberCell).join("")}
        <td class="explanation">${note}</td></tr>`;
    const table = (caption, columns, body) => `<table><caption>${caption}</caption><thead><tr>${columns
        .map(label => `<th scope="col">${label}</th>`).join("")}</tr></thead><tbody>${body}</tbody></table>`;
    const company = () => companies[issuer.value];
    const trailing = () => ({...company(), ...company().ltm, links: {...company().links, ...company().ltm?.links}});
    const selected = () => company().scenarios[scenarioSelect.value];
    const option = (value, label, current) => `<option value="${value}"${value === current ? " selected" : ""}>${label}</option>`;

    function attempt(calculate) {
        try {
            return calculate();
        } catch (error) {
            if (!(error instanceof RangeError)) throw error;
            return error;
        }
    }

    function evaluate(inputs, assumptions) {
        return inputs instanceof RangeError ? inputs : attempt(() => valueCompany(inputs, assumptions));
    }

    function renderPeriodComparison(ltm, normalizedBase) {
        const annual = company();
        const base = annual.scenarios.Base;
        const before = evaluate(annual, base);
        const after = evaluate(ltm, base);
        const labels = ["Metric", annual.periodLabel, ltm.periodLabel, "LTM − FY"];
        const changes = document.getElementById("period-comparison");
        const drivers = document.getElementById("margin-drivers");
        if (before instanceof RangeError || after instanceof RangeError) {
            changes.textContent = "Period comparison requires valid Base case assumptions.";
            drivers.replaceChildren();
            return;
        }
        const points = value => `${value > 0 ? "+" : ""}${money(value * 100)} pp`;
        const valueRows = [
            [`Value per share, ${annual.currency}`, [money(before.perShare, 2), money(after.perShare, 2),
                money(after.perShare - before.perShare, 2)]],
            ["Market-implied terminal margin", [percent(before.impliedMargin), percent(after.impliedMargin),
                points(after.impliedMargin - before.impliedMargin)]],
            ["Starting operating margin", [percent(annual.operatingProfit / annual.revenue),
                percent(ltm.operatingProfit / ltm.revenue),
                points(ltm.operatingProfit / ltm.revenue - annual.operatingProfit / annual.revenue)]],
        ];
        if (normalizedBase) {
            labels.push("Normalized", "Normalized − LTM");
            const normalized = evaluate(normalizedBase, base);
            valueRows[0][1].push(...(normalized instanceof RangeError ? ["N/A", "N/A"] :
                [money(normalized.perShare, 2), money(normalized.perShare - after.perShare, 2)]));
            valueRows[1][1].push(...(normalized instanceof RangeError ? ["N/A", "N/A"] :
                [percent(normalized.impliedMargin), points(normalized.impliedMargin - after.impliedMargin)]));
            valueRows[2][1].push(...(normalizedBase instanceof RangeError ? ["N/A", "N/A"] :
                [percent(normalizedBase.startingMargin), points(normalizedBase.startingMargin - ltm.operatingProfit / ltm.revenue)]));
        }
        changes.innerHTML = table("Base case with the same forecast assumptions", labels,
            valueRows.map(([label, amounts]) => row(label, amounts)).join(""));
        const shocks = [
            ["LTM Base case", {}],
            ["WACC −1 pp", {wacc: base.wacc - 1}],
            ["WACC +1 pp", {wacc: base.wacc + 1}],
            ["Terminal ROIC −2 pp", {terminalRoic: base.terminalRoic - 2}],
            ["Terminal ROIC +2 pp", {terminalRoic: base.terminalRoic + 2}],
        ];
        drivers.innerHTML = table("LTM Base case · one assumption at a time",
            ["Change", "Market-implied terminal margin", "Change from LTM Base"],
            shocks.map(([label, change]) => {
                const test = evaluate(ltm, {...base, ...change});
                return row(label, test instanceof RangeError ? ["N/A", "N/A"] :
                    [percent(test.impliedMargin), points(test.impliedMargin - after.impliedMargin)]);
            }).join(""));
    }

    function editable(label, key, value, index) {
        const offset = index ?? "";
        return `<input type="number" step="any" value="${value}" data-key="${key}" data-year="${offset}" aria-label="${label} (%)">`;
    }

    function renderControls() {
        const assumptions = selected();
        const periods = ["Driver, annual %", "Year 1", "Year 2", "Year 3", "Year 4", "Year 5"];
        const revenue = company().revenueComponents;
        const regularDrivers = revenue ? driverFields.slice(1) : driverFields;
        const drivers = regularDrivers.map(([key, label]) => row(label, assumptions[key].map(
            (value, index) => editable(`${label}, year ${index + 1}`, key, value, index),
        )));
        if (revenue) {
            drivers.unshift(...Object.entries(revenue).map(([key, {label}]) => row(`${label} growth`,
                assumptions.revenueGrowth[key].map((value, index) =>
                    editable(`${label} growth, year ${index + 1}`, key, value, index)),
            )));
        }
        const otherRates = rateFields.map(([key, label]) => `<label>${label} (%)
            ${editable(label, key, assumptions[key])}</label>`);
        controls.innerHTML = `<div class="scroll">${table("Operating drivers", periods, drivers.join(""))}</div>
            <div class="rates">${otherRates.join("")}</div>`;
    }

    function renderSensitivity(base, inputs) {
        const discountRates = [-2, -1, 0, 1, 2].map(offset => base.wacc + offset);
        const growthRates = [-1, -0.5, 0, 0.5, 1].map(offset => base.terminalGrowth + offset);
        const grid = discountRates.map(wacc => row(`${money(wacc, 2)}%`, growthRates.map(terminalGrowth => {
            try {
                const value = valueCompany(inputs, {...base, wacc, terminalGrowth}).perShare;
                return money(value, 2);
            } catch (error) {
                if (!(error instanceof RangeError)) throw error;
                return "N/A";
            }
        })));
        document.getElementById("sensitivity").innerHTML = table(
            `Base case sensitivity · ${inputs.currency}/share`,
            ["WACC ↓ / terminal growth →", ...growthRates.map(value => `${money(value)}%`)], grid.join(""),
        );
    }

    function renderRevenueBridge(inputs, result) {
        const output = document.getElementById("revenue-bridge");
        output.replaceChildren();
        if (!inputs.revenueComponents || result instanceof RangeError) return;

        const forecast = result.forecast;
        const components = Object.entries(inputs.revenueComponents).map(([key, item]) =>
            row(item.label, [item.link, ...forecast.map(year => money(year.componentRevenue[key]))]));
        const totals = forecast.map(year => year.revenue);
        const growth = totals.map((revenue, index) => percent(revenue / (totals[index - 1] ?? inputs.revenue) - 1));
        output.innerHTML = table(`${scenarioSelect.value} case revenue bridge · ${inputs.currency} millions`,
            ["Revenue", inputs.periodLabel, ...totals.map((_, index) => `Year ${index + 1}`)],
            components.join("") + row("Consolidated revenue", [inputs.links.revenue, ...totals.map(value => money(value))]) +
            row("Consolidated revenue growth", ["", ...growth]));
    }

    function renderDebtSensitivity(inputs, assumptions) {
        const debt = inputs.debtAssumption;
        if (!debt) return;
        const amounts = [...new Set([debt.value, ...debt.alternatives])].filter(
            value => Number.isFinite(value) && value >= 0,
        ).sort((a, b) => a - b);
        const rows = amounts.map(value => {
            const result = evaluate({...inputs, debtAssumption: {...debt, value}}, assumptions);
            return row(`${money(value)}${value === debt.value ? " (selected)" : ""}`,
                result instanceof RangeError ? ["N/A", "N/A"] :
                    [money(result.perShare, 2), percent(result.impliedMargin)]);
        });
        document.getElementById("debt-sensitivity").innerHTML = table(
            "Base case borrowing sensitivity · cash and operating assumptions held fixed",
            [`Assumed borrowing, ${inputs.currency} millions`, `${inputs.currency}/share`, "Market-implied terminal margin"],
            rows.join(""));
    }

    function loadGrowthMarginMap(inputs) {
        const config = inputs.growthMarginMap;
        document.getElementById("growth-margin-map").hidden = !config;
        if (!config) return;
        growthRatesInput.value = config.growthRates.join(", ");
        marginsInput.value = config.margins.join(", ");
        const columns = ["Fiscal year", "Period end", "Length", config.label,
            ...(config.revenueComponent ? ["Consolidated revenue"] : []), "Operating profit",
            "Annual growth", "Operating margin"];
        document.getElementById("growth-margin-history").innerHTML = table(
            `Reported history · ${inputs.currency} millions unless shown as %`, columns,
            config.history.map(([year, ...values]) => row(year, values.filter(value => value !== null))).join(""),
        );
    }

    function renderGrowthMarginMap(inputs) {
        const config = inputs.growthMarginMap;
        if (!config) return;
        const base = inputs.scenarios.Base;
        const grid = document.getElementById("growth-margin-grids");
        const references = document.getElementById("growth-margin-reference");
        const message = document.getElementById("map-status");
        const margins = percentList(marginsInput.value);
        const {revenueComponent, horizons} = config;
        const referenceValue = (years, growth, margin) =>
            growthMarginForecast(inputs, base, revenueComponent, years, growth, margin).perShare;
        document.getElementById("growth-margin-context").innerHTML =
            `Target ${inputs.links.price} ${inputs.currency}/share at the ${inputs.priceDate} close. ` +
            `Each path starts at the ${inputs.periodLabel} reported margin. ${config.label} grows at the row’s rate, ` +
            `and operating margin moves in equal annual steps to the column’s margin by the final year. ` +
            (revenueComponent ? "Other revenue lines follow Base and hold their Year 5 growth rate in later years. " : "") +
            `Every path uses the same Base taxes, reinvestment, discount rate and equity bridge. ` +
            `After the final year, the margin stays at the endpoint and consolidated revenue grows at the Base terminal rate.`;
        const maps = attempt(() => growthMarginMap(inputs, base, {
            ...config, margins, growthRates: percentList(growthRatesInput.value),
        }));
        message.textContent = maps instanceof RangeError ? maps.message : "";
        grid.replaceChildren();
        references.replaceChildren();
        if (maps instanceof RangeError) return;
        const rate = value => `${money(value, 2)}%`;
        const axisLabel = value => `${value}%`;
        grid.innerHTML = maps.map(({years, rows}) => {
            const body = rows.map(({growth, prices, threshold, bound}) => {
                const cells = prices.map(price => price >= inputs.price ?
                    `<td class="map-meets-price" title="Reaches the dated close">${money(price, 2)}</td>` :
                    numberCell(money(price, 2))).join("");
                const needed = bound === "below" ? `≤ ${axisLabel(threshold)}` :
                    bound === "above" ? `&gt; ${axisLabel(threshold)}` : rate(threshold);
                return `<tr><th scope="row">${axisLabel(growth)}</th>${cells}<td>${needed}</td></tr>`;
            }).join("");
            return `<div class="scroll" role="region" aria-label="${years}-year growth and margin map" tabindex="0">${table(
                `${years}-year path · ${inputs.currency}/share`,
                [`${config.label} growth ↓ / margin →`, ...margins.map(axisLabel), "Margin needed"], body,
            )}</div>`;
        }).join("");
        const benchmarkRows = ["Base", "Bull"].map(name => {
            const result = evaluate(inputs, inputs.scenarios[name]);
            if (result instanceof RangeError) return row(name, Array(2 + horizons.length).fill("N/A"));
            const firstRevenue = revenueComponent ? inputs.revenueComponents[revenueComponent].value : inputs.revenue;
            const finalYear = result.forecast.at(-1);
            const finalRevenue = revenueComponent ? finalYear.componentRevenue[revenueComponent] : finalYear.revenue;
            const growth = ((finalRevenue / firstRevenue) ** (1 / result.forecast.length) - 1) * 100;
            const margin = result.terminalMargin * 100;
            return row(name, [rate(growth), rate(margin), ...horizons.map(years => {
                const value = attempt(() => referenceValue(years, growth, margin));
                return value instanceof RangeError ? "N/A" : money(value, 2);
            })]);
        });
        references.innerHTML = table("Base and Bull operating assumptions priced on the map using Base financial assumptions",
            ["Case", `${config.label} CAGR`, "Year 5 margin", ...horizons.map(years => `${years} years · ${inputs.currency}/share`)],
            benchmarkRows.join(""));
    }

    function loadLocationControls(inputs) {
        const config = inputs.locationGrowth;
        document.getElementById("location-growth").hidden = !config;
        if (!config) return;
        const {assumptions, growthRates, locationLabel, salesLabel, history, note, references} = config;
        const numbers = locationFields.map(([key, label]) => {
            const unit = key === "openingCost" || key === "otherCapex" ? ` (${inputs.currency} millions)` : "";
            return `<label>${label}${unit}<input type="number" step="any" data-location="${key}" value="${assumptions[key]}"></label>`;
        });
        locationControls.innerHTML = `<div class="rates">${numbers.join("")}</div><div class="selects map-axes">
            <label>Target annual sales growth rates (%)<input type="text" data-location="growthRates"
                value="${growthRates.join(", ")}" aria-describedby="location-input-help"></label>
            <label>New sites’ sales as a share of mature sites (%)<input type="text" data-location="ramp"
                value="${assumptions.ramp.join(", ")}" aria-describedby="location-input-help"></label>
            </div><p id="location-input-help">Separate percentages with commas. The sales ramp starts in the opening year and ends at 100%.</p>`;
        document.getElementById("location-context").innerHTML =
            `The model adds whole locations as needed each year to meet the annual target for ${salesLabel}. ` +
            `New locations take time to reach mature sales. Their share of mature sales covers timing and ramp-up. ` +
            `Opening capex is paid in the opening year. Other capex scales with locations at the start of the year. ` +
            `Both costs rise with capex inflation. The paths share Base financing, taxes, working capital and terminal assumptions. ` +
            `The target price is ${inputs.links.price} ${inputs.currency}/share at the ${inputs.priceDate} close.`;
        document.getElementById("location-note").innerHTML = [note, references.join(" · ")].filter(Boolean).join(" ");
        document.getElementById("location-history").innerHTML = table(
            `Reported location and cash capex history · ${inputs.currency} millions`,
            ["Fiscal year", `${locationLabel} at year-end`, "Net openings", "Cash capex"],
            history.map(([year, ...values]) => row(year, values)).join(""),
        );
    }

    function renderLocationResults(inputs) {
        const config = inputs.locationGrowth;
        if (!config) return;
        const output = document.getElementById("location-results");
        const details = document.getElementById("location-details");
        const capexCheck = document.getElementById("location-capex-check");
        const message = document.getElementById("location-status");
        const paths = attempt(() => locationGrowthPaths(inputs, inputs.scenarios.Base, config));
        output.replaceChildren();
        details.replaceChildren();
        capexCheck.replaceChildren();
        message.textContent = paths instanceof RangeError ? paths.message : "";
        if (paths instanceof RangeError) return;
        const units = `${inputs.currency} millions`;
        const {openingCost, otherCapex, referenceMargin} = config.assumptions;
        const reconstructedCapex = config.startingOpenings * openingCost + otherCapex;
        capexCheck.innerHTML = `${inputs.periodLabel} cash capex check. The assumed split implies ${money(reconstructedCapex)} ${units} ` +
            `against reported ${config.capexLink} ${units}. Difference ${money(reconstructedCapex - config.reportedCapex)} ${units}.`;
        const total = (forecast, key) => forecast.reduce((sum, year) => sum + year[key], 0);
        const name = ({years, growth}) => `${years} years at ${growth}%`;
        output.innerHTML = table(
            `Path comparison at ${referenceMargin}% final margin · ${units} except per-share values`,
            ["Path", "Year 1 net openings", "Total net openings", "Total capex", "Lowest annual cash left",
                "Total cash left", `${inputs.currency}/share`, "Margin needed with location capex", "Margin needed with revenue-share capex"],
            paths.map(path => {
                const {forecast, perShare, projected, comparison} = path;
                return row(name(path), [forecast[0].openings, total(forecast, "openings"), money(total(forecast, "capex")),
                    money(Math.min(...forecast.map(year => year.fcff))), money(total(forecast, "fcff")),
                    money(perShare, 2), formatThreshold(projected), formatThreshold(comparison)]);
            }).join(""),
        );
        details.innerHTML = `<p>Cash left is operating cash after modeled tax, working capital and capex, before debt payments and dividends. ` +
            `The margin needed columns match the quote using the same sales path with two different capex methods.</p>` +
            paths.map(path => `<details><summary>${name(path)} · annual openings and cash flow</summary>
                <div class="scroll" role="region" aria-label="${name(path)} annual forecast" tabindex="0">${table(
                    `Forecast at ${referenceMargin}% final operating margin · ${units}`,
                    ["Year", "Target sales", "Modeled sales", "Net openings", "Locations at year-end", "Opening capex",
                        "Other capex", "Total capex", "Operating margin", "Cash left"],
                    path.forecast.map((year, i) => row(i + 1, [money(year.targetSales), money(year.sales), year.openings,
                        year.sites, money(year.expansionCapex), money(year.continuingCapex), money(year.capex),
                        `${money(year.margin * 100, 2)}%`, money(year.fcff)])).join(""),
                )}</div></details>`).join("");
    }

    function renderResults() {
        const annual = company();
        const ltm = trailing();
        const profile = annual.normalization;
        const isPhosagro = issuer.value === "phosagro";
        const useNormalized = period.value === "normalized";
        const inputs = period.value === "annual" ? annual : ltm;
        const names = Object.keys(annual.scenarios);
        const normalized = profile ? Object.fromEntries(names.map(name => [
            name, attempt(() => isPhosagro ? phosagroNormalizedInputs(ltm, profile, name) :
                normalizedInputs(ltm, annual.revenue, profile, name)),
        ])) : {};
        const inputsForCase = name => useNormalized ? normalized[name] : inputs;
        const results = Object.fromEntries(names.map(
            name => [name, evaluate(inputsForCase(name), annual.scenarios[name])],
        ));
        if (annual.ltm) renderPeriodComparison(ltm, normalized.Base);
        renderReference(inputs, useNormalized ? normalized[scenarioSelect.value] : null);
        if (isPhosagro) {
            if (useNormalized) renderPhosagroCycleResults(profile, normalized);
            const financing = partialRollover ? phosagroFunding : {...phosagroFunding, rollover: 0, replacementRate: 0};
            renderPhosagroFunding(useNormalized ? "Normalized" : inputs.periodLabel, inputs.liquidity, results, financing);
            renderPhosagroRecovery(ltm, normalized[scenarioSelect.value], financing);
            renderPhosagroBreakEven(ltm);
        } else if (profile) {
            if (useNormalized) renderCycleResults(profile, normalized);
            renderRecovery(ltm, normalized[scenarioSelect.value]);
        }
        const current = results[scenarioSelect.value];
        const base = results.Base;
        renderRevenueBridge(inputs, current);
        renderDebtSensitivity(inputs, annual.scenarios.Base);
        renderGrowthMarginMap(annual);
        renderLocationResults(annual);
        document.getElementById("cases").innerHTML = table("Scenario values", ["Case", `${inputs.currency}/share`,
            "Difference from close", "Year 5 margin"], Object.entries(results).map(([name, value]) => row(name,
            value instanceof RangeError ? ["N/A", "N/A", "N/A"] :
                [money(value.perShare, 2), signedPercent(value.perShare / inputs.price - 1), percent(value.terminalMargin)],
        )).join(""));

        status.textContent = current instanceof RangeError ? current.message : [
            current.terminalMargin <= 0 ?
                "Operating profit is zero or negative in Year 5. The terminal value is a stress calculation. A going-concern estimate needs a sustainable profit forecast." : "",
            current.equityValue < 0 ?
                "The arithmetic produces a negative equity residual, which shows how far the operating and other assets fall short of the debt and other claims." : "",
        ].filter(Boolean).join(" ");

        const forecast = document.getElementById("forecast");
        const bridge = document.getElementById("bridge");
        forecast.innerHTML = "";
        bridge.innerHTML = "";
        if (!(current instanceof RangeError)) {
            const fields = [
                ["revenue", "Revenue"], ["operatingProfit", "Operating profit"], ["margin", "Operating margin"],
                ["cashTax", "Cash tax on operating profit"], ["nopat", "After-tax operating profit"],
                ["depreciation", "D&A"], ["capex", "Capex"],
                ["workingCapitalInvestment", "Increase in operating working capital"],
                ["fcff", "Unlevered free cash flow"], ["presentValue", "Present value of cash flow"],
            ];
            forecast.innerHTML = table(
                `${scenarioSelect.value} case projections · ${inputs.currency} millions`,
                ["Metric", "Year 1", "Year 2", "Year 3", "Year 4", "Year 5"],
                fields.map(([key, label]) => row(label, current.forecast.map(
                    year => key === "margin" ? percent(year[key]) : money(year[key]),
                ))).join(""),
            );
            bridge.innerHTML = table(`${scenarioSelect.value} case enterprise-to-equity bridge`, ["Item", "Amount"], [
                ["Present value of forecast cash flows", money(current.explicitValue)],
                ["Terminal-year revenue", money(current.terminalRevenue)],
                ["Terminal reinvestment rate", percent(current.terminalReinvestment)],
                ["Terminal-year unlevered cash flow", money(current.terminalCash)],
                ["Present value of terminal cash flows", money(current.terminalValue)],
                ["Enterprise value", money(current.enterpriseValue)],
                [inputs.debtAssumption ? "Less net debt from reported balances" : "Less valuation net debt", inputs.links.netDebt],
                ...(inputs.debtAssumption ? [["Less assumed borrowing", money(inputs.debtAssumption.value)]] : []),
                ["Less non-controlling interests", inputs.links.noncontrolling],
                ["Plus non-operating investments", inputs.links.investments], ["Equity value", money(current.equityValue)],
                ["Shares outstanding, millions", inputs.links.shares],
                [`Equity value per share, ${inputs.currency}`, money(current.perShare, 2)],
            ].map(([label, value]) => row(label, [value])).join(""));
        }
        const reverse = document.getElementById("reverse");
        if (base instanceof RangeError) {
            reverse.textContent = `Base case unavailable. ${base.message}`;
            document.getElementById("sensitivity").replaceChildren();
        } else {
            reverse.textContent = `A terminal operating margin of ${percent(base.impliedMargin)} would match the dated share price under the Base case assumptions. The Base case projects a ${percent(base.terminalMargin)} terminal margin. The reverse calculation holds the five forecast years, discount rate, terminal growth, terminal ROIC and equity bridge fixed.`;
            renderSensitivity(annual.scenarios.Base, inputsForCase("Base"));
        }
    }

    function renderReference(inputs, normalized) {
        const leaseNote = inputs.links.operatingLeaseDebt ?
            `<p>Valuation net debt excludes ${inputs.links.operatingLeaseDebt} million of operating lease liabilities.
            Their costs remain in the operating forecast.</p>` : "";
        const note = normalized ? "<p>Normalized and trailing valuations use the same revenue, equity bridge and share count. The annual case uses its year-end equity bridge. The quote and forecast assumptions are shared across all starting points.</p>" :
            inputs.periodNote ? `<p>${inputs.periodNote}</p>` : "";
        const summary = normalized ?
            `Normalized ${scenarioSelect.value} case in ${inputs.currency} millions. Starting revenue ${inputs.links.revenue}
            and estimated operating profit ${normalized instanceof RangeError ? "N/A" : money(normalized.operatingProfit)}.
            Reported trailing operating cash flow ${inputs.links.operatingCash ?? "N/A"}.` :
            `${inputs.periodLabel} in ${inputs.currency} millions: revenue ${inputs.links.revenue},
            operating profit ${inputs.links.operatingProfit}, operating cash flow ${inputs.links.operatingCash ?? "N/A"}.`;
        document.getElementById("source-data").innerHTML = `<p>${inputs.market} · ${inputs.ticker} ·
            ${inputs.currency} · ${inputs.accounting}</p>
            <p>Market benchmark: ${inputs.links.price} · ${inputs.priceDate} · ${inputs.priceMarket}</p>
            <p>${summary} Balance sheet at ${inputs.balanceDate} and share count at ${inputs.shareDate}.</p>${leaseNote}${note}`;
    }

    function renderNormalization() {
        const annual = company();
        const profile = annual.normalization;
        const controls = document.getElementById("cycle-controls");
        const year = annual.fiscalYear;
        const names = Object.keys(profile.cases);
        document.getElementById("normalization-bridge").innerHTML = table(
            "Operating profit bridge · USD millions",
            ["Metric", `FY${year}`, `9M ${year + 1}`, `9M ${year}`, "LTM", "Treatment"],
            explainedRow("Reported operating profit", profile.reportedLinks, "Full year plus current nine months less prior nine months.") +
            explainedRow("Net loss on assets sold and held for sale", profile.lossLinks, "The next three rows reconcile to this subtotal.") +
            profile.adjustments.map(({label, amounts, note}) => explainedRow(label, amounts, note)).join("") +
            explainedRow("Operating profit after identified adjustments", ["", "", "", profile.adjustedLink],
                "Reported LTM operating profit plus the net disposal loss and the three separately disclosed exit and goodwill adjustments."),
        );
        document.getElementById("normalization-scope").innerHTML = `The bridge retains about $${profile.careLink} million of
            Araxá and Patrocínio care and maintenance. Mosaic expected these activities to continue through the anticipated sale,
            and Patrocínio remained idle. The filings give segment totals. The bridge makes no separate estimate of revenue and
            recurring costs transferred or saved. The Kronau land sale closed after the trailing period and Mosaic retained the
            mineral rights. The bridge keeps other items such as restructuring, the canceled phosphate project and offsetting
            gains as reported. ${profile.exitLink} · ${profile.otherExpenseLink}`;
        const historyRows = Object.entries(profile.segments).flatMap(([key, segment]) =>
            Object.entries(segment.drivers).map(([driver, {label, historyLinks}]) => {
                const title = `${segment.label} ${label}`;
                const inputs = names.map(name => {
                    const value = profile.cases[name][key][driver];
                    return `<input type="number" step="any" value="${Number.isFinite(value) ? value : ""}"
                        data-case="${name}" data-segment="${key}" data-driver="${driver}" aria-label="${name} ${title}">`;
                });
                return row(title, [...historyLinks, ...inputs]);
            }),
        );
        historyRows.push(row("Operating margin after disposal and goodwill adjustments",
            [...profile.historicalMargins, "—", "—", "—", "—", "—"]));
        controls.innerHTML = table("Mosaic operating history and analyst assumptions",
            ["Driver", `FY${year - 2}`, `FY${year - 1}`, `FY${year}`, `9M ${year}`, `9M ${year + 1}`, ...names], historyRows.join(""));
        document.getElementById("cycle-reference").innerHTML = table(`FY${year} benchmark exposures`,
            ["Segment", "External sales, USD m", "Volume contribution", "Assumed price retention",
                "Ammonia, Mt", "Sulfur, m long tons"],
            Object.values(profile.segments).map(segment => row(segment.label, [
                segment.salesLink, segment.contributionLink, percent(segment.priceRetention / 100),
                segment.drivers.ammonia?.quantityLink ?? "—", segment.drivers.sulfur?.quantityLink ?? "—",
            ])).join(""));
    }

    function renderCycleResults(profile, normalized) {
        const names = Object.keys(normalized);
        const byCase = (label, value) => row(label, names.map(name =>
            normalized[name] instanceof RangeError ? "N/A" : value(normalized[name]),
        ));
        document.getElementById("cycle-results").innerHTML = table("Mid-cycle operating profit · USD millions",
            ["Calculation", ...names], [
                row("FY2025 benchmark operating profit", names.map(() => money(profile.anchorEbit))),
                byCase("Selling price effect", inputs => money(inputs.changes.price)),
                byCase("Shipment volume effect", inputs => money(inputs.changes.volume)),
                byCase("Ammonia and sulfur cost effect", inputs => money(inputs.changes.inputs)),
                byCase("Benchmark revenue at case prices and volumes", inputs => money(inputs.benchmarkRevenue)),
                byCase("Benchmark operating profit at case prices and volumes", inputs => money(inputs.benchmarkEbit)),
                byCase("Estimated mid-cycle operating margin", inputs => percent(inputs.startingMargin)),
                byCase("Mid-cycle adjustment to the adjusted LTM operating profit",
                    inputs => money(inputs.operatingProfit - profile.adjustedEbit)),
                byCase("Normalized operating profit at trailing revenue", inputs => money(inputs.operatingProfit)),
            ].join(""));
    }

    function renderPhosagroNormalization() {
        const {fiscalYear: year, normalization: profile} = company();
        const columns = ["Metric", `FY${year - 2}`, `FY${year - 1}`, `FY${year}`, `9M ${year}`, `9M ${year + 1}`, "LTM"];
        const history = rows => rows.map(([metric, label]) => row(label, profile.history[metric])).join("");
        document.getElementById("phosagro-normalization-bridge").innerHTML = table(
            "Consolidated operating profit bridge · RUB millions", columns, history([
                ["reported", "Reported operating profit"],
                ["fxAdjustment", "Remove operating FX gain or loss"],
                ["adjusted", "Operating profit excluding operating FX"],
                ["margin", "Operating margin excluding operating FX"],
            ]));
        document.getElementById("phosagro-cycle-history").innerHTML = table(
            "Consolidated operating and cash flow history · RUB millions unless indicated", columns, history([
                ["revenue", "Consolidated revenue"],
                ["productRevenue", "Phosphate and nitrogen product sales"],
                ["shipments", "Fertilizer and feed-phosphate shipments, kt"],
                ["unitRevenue", "Product revenue per reported shipped tonne, RUB"],
                ["grossProfit", "Consolidated gross profit"],
                ["sulphur", "Sulphur and sulphuric acid cost"],
                ["ammonia", "Ammonia cost"],
                ["potassiumChloride", "Potassium chloride cost"],
                ["naturalGas", "Natural gas cost"],
                ["ammoniumSulphate", "Ammonium sulphate cost"],
                ["inputs", "Other key raw-material costs, subtotal"],
                ["customs", "Net customs credit or expense"],
                ["receivablesCash", "Cash movement in trade and other receivables"],
                ["workingCapitalCash", "Cash movement in operating working capital"],
                ["operatingCash", "Reported operating cash flow"],
                ["capex", "Cash purchases of PP&E and intangibles"],
                ["cashAfterCapex", "Operating cash flow after cash purchases"],
            ]));
        const names = Object.keys(profile.cases);
        document.getElementById("phosagro-cycle-controls").innerHTML = table(
            "Mid-cycle assumptions · percentages relative to the trailing period", ["Assumption (%)", ...names],
            phosagroCycleFields.map(([key, label]) => row(label, names.map(name => {
                const value = profile.cases[name][key];
                return `<input type="number" step="any" value="${Number.isFinite(value) ? value : ""}"
                    data-case="${name}" data-driver="${key}" aria-label="${name} ${label}, percent">`;
            }))).join(""));
    }

    function renderPhosagroCycleResults(profile, normalized) {
        const names = Object.keys(normalized);
        const byCase = (label, value) => row(label, names.map(name =>
            normalized[name] instanceof RangeError ? "N/A" : value(normalized[name]),
        ));
        document.getElementById("phosagro-cycle-results").innerHTML = table("Mid-cycle operating profit · RUB millions",
            ["Calculation", ...names], [
                row("Trailing operating profit excluding operating FX", names.map(() => profile.history.adjusted.at(-1))),
                byCase("Product selling price effect", inputs => money(inputs.changes.price)),
                byCase("Shipment volume effect", inputs => money(inputs.changes.volume)),
                byCase("Sulphur and sulphuric acid cost effect", inputs => money(inputs.changes.sulphur)),
                byCase("Other key raw-material cost effect", inputs => money(inputs.changes.inputs)),
                byCase("Revenue at case prices and volumes", inputs => money(inputs.benchmarkRevenue)),
                byCase("Operating profit at case prices and volumes", inputs => money(inputs.benchmarkEbit)),
                byCase("Estimated mid-cycle operating margin", inputs => percent(inputs.startingMargin)),
                byCase("Apply the margin to trailing revenue", inputs => money(inputs.operatingProfit - inputs.benchmarkEbit)),
                byCase("Normalized operating profit at trailing revenue", inputs => money(inputs.operatingProfit)),
            ].join(""));
    }

    function renderRecovery(ltm, normalized) {
        const {revenue, normalization: profile, liquidity} = company();
        const scenario = selected();
        const result = normalized instanceof RangeError ? normalized :
            attempt(() => valueRecoveryPaths(ltm, revenue, profile, normalized, scenario, recoveryTiming));
        const comparison = document.getElementById("recovery-comparison");
        const detail = document.getElementById("recovery-forecast");
        const statement = document.getElementById("recovery-summary");
        const basis = document.getElementById("recovery-basis");
        const liquidityStatus = document.getElementById("liquidity-status");
        const liquidityDetail = document.getElementById("liquidity-forecast");
        if (result instanceof RangeError) {
            statement.textContent = result.message;
            comparison.replaceChildren();
            detail.replaceChildren();
            basis.replaceChildren();
            liquidityStatus.replaceChildren();
            liquidityDetail.replaceChildren();
            return;
        }
        const {paths, slowest, target} = result;
        const quote = `${ltm.links.price} on ${ltm.priceDate}`;
        statement.innerHTML = slowest ?
            `<strong>${slowest.label} is the slowest tested recovery that meets the dated close.</strong> The model values it at
            $${money(slowest.perShare, 2)} per share against ${quote}.` :
            `<strong>All four tested paths fall below the dated close.</strong> The model compares them with ${quote}.`;
        basis.innerHTML = `The ${scenarioSelect.value} case starts from $${profile.adjustedLink} million of adjusted trailing
            operating profit. It uses $${profile.careLink} million as the annual care and maintenance assumption through the
            selected year. All four paths use ${money(scenario.wacc)}% WACC, ${money(scenario.terminalGrowth)}% terminal growth,
            ${money(scenario.terminalRoic)}% terminal ROIC, a ${percent(target.terminalMargin)} terminal operating margin and
            the same equity bridge. The present value of terminal cash flows is $${money(target.terminalValue)} million in each path.`;
        const financing = customFinancing ? liquiditySettingsValues :
            {...liquiditySettingsValues, namedRefinancing: 0, shortTermRollover: 0, replacementRate: 0, renewRevolver: false};
        const funding = attempt(() => Object.fromEntries(paths.map(path => [
            path.years, liquidityForecast(path.forecast, liquidity, financing),
        ])));
        const fundingError = funding instanceof RangeError;
        if (!fundingError) {
            const supported = paths.findLast(path => path.delta >= 0 && !funding[path.years].firstGap);
            liquidityStatus.textContent = supported ?
                `${supported.label} is the slowest tested path that meets the dated close and has no modeled funding gap.` :
                "None of the tested paths both meets the dated close and has no modeled funding gap.";
        } else {
            liquidityStatus.textContent = funding.message;
        }
        comparison.innerHTML = table(`${scenarioSelect.value} case recovery comparison`, [
            "Recovery", "DCF value, USD/share", "Gap to close, USD/share",
            "First positive free cash flow", "Cash shortfall before first positive year, USD m",
            "Minimum month-end liquidity, USD m", "Peak credit-line draw, USD m", "First funding gap",
        ], paths.map(path => {
            const figures = fundingError ? ["N/A", "N/A", "N/A"] : fundingCells(funding[path.years]);
            return row(path.label, [
                money(path.perShare, 2), `${path.delta > 0 ? "+" : ""}${money(path.delta, 2)}`,
                path.firstPositiveYear === null ? "None in 5 years" : `Year ${path.firstPositiveYear}`,
                money(path.cashShortfall), ...figures,
            ]);
        }).join(""));
        const path = paths.find(path => path.years === detailPeriod);
        if (fundingError) liquidityDetail.replaceChildren();
        else renderLiquidityDetail(liquidityDetail, path.label, funding[path.years]);
        const rows = [
            ["Selling price effect", year => money(year.effects.price)],
            ["Shipment volume effect", year => money(year.effects.volume)],
            ["Input cost effect", year => money(year.effects.inputs)],
            ["Other operating effects", year => money(year.effects.other)],
            ["Care and maintenance expense", year => money(year.careCost)],
            ["Operating profit", year => money(year.operatingProfit)],
            ["Operating margin", year => percent(year.margin)],
            ["Unlevered free cash flow", year => money(year.fcff)],
            ["Cumulative free cash flow", year => money(year.cumulativeCash)],
        ];
        detail.innerHTML = table(`${scenarioSelect.value} case · ${path.label} · USD millions`,
            ["Metric", ...path.forecast.map((_, index) => `Year ${index + 1}`)],
            rows.map(([label, value]) => row(label, path.forecast.map(value))).join(""));
    }

    function fundingCells({minimumLiquidity, peakRevolver, firstGap}) {
        return [money(minimumLiquidity), money(peakRevolver), fundingGap(firstGap, "$")];
    }

    function fundingGap(gap, unit) {
        return gap ? `Year ${gap.year} · ${gap.month} (${unit}${money(-gap.liquidity)}m)` : "None in 5 years";
    }

    function renderLiquidityDetail(detail, label, {months}, {currency = "USD", showHistoricalFinancing = false} = {}) {
        const financing = showHistoricalFinancing ? [
            ["shortFinancing", "Current bank principal due"],
            ["otherPrincipal", "Other bank and lease principal due"],
            ["actualBorrowing", "Disclosed post-period borrowing proceeds"],
            ["actualPrincipal", "Disclosed post-period principal payments"],
        ] : [
            ["shortFinancing", "Short-term and supplier financing repaid"],
            ["otherPrincipal", "Other debt principal due"],
            ["exitPayment", "Cash exit settlements"],
            ["draw", "Credit-line draws"], ["revolverRepayment", "Credit-line repayments"],
        ];
        const fields = [
            ["fcff", "Unlevered free cash flow"], ["interest", "Cash interest"],
            financing[0], ["namedPrincipal", "Named debt principal due"], ...financing.slice(1),
            ["refinancing", "Replacement debt proceeds"], ["dividendPayment", "Cash dividends"],
        ];
        const annual = Array.from({length: months.length / 12}, (_, year) => months.slice(year * 12, (year + 1) * 12));
        const closing = (name, metric) => row(name, annual.map(year => money(year.at(-1)[metric])));
        const cashFlows = fields.map(([key, name]) =>
            row(name, annual.map(year => money(year.reduce((sum, month) => sum + month[key], 0)))));
        const balances = showHistoricalFinancing ? [closing("Replacement debt outstanding", "replacementDebt")] : [
            closing("Credit line outstanding", "revolver"),
            closing("Undrawn usable credit", "undrawn"),
            closing("Ending liquidity", "liquidity"),
        ];
        detail.innerHTML = table(`Funding schedule · ${label} · ${currency} millions`,
            ["Cash flow or ending balance", ...annual.map(year => year.at(-1).month)], [
                ...cashFlows,
                closing("Cash after funding (negative balance shows a gap)", "cash"),
                ...balances,
            ].join(""));
    }

    function fundingInputs(fields, assumptions) {
        return fields.map(([key, label, max, min = 0, step = "any"]) => {
            const value = assumptions[key];
            return `<label>${label}<input type="number" step="${step}" min="${min}"${max === null ? "" : ` max="${max}"`}
                value="${Number.isFinite(value) ? value : ""}" data-funding="${key}"></label>`;
        }).join("");
    }

    function renderLiquiditySettings() {
        liquiditySettings.innerHTML = `<div class="rates">${fundingInputs(financingFields, liquiditySettingsValues)}</div>${customFinancing ?
            `<div class="rates">${fundingInputs(refinancingFields, liquiditySettingsValues)}</div>
            <label><input class="checkbox" type="checkbox" data-funding="renewRevolver"
                ${liquiditySettingsValues.renewRevolver ? "checked" : ""}> Renew the revolver at expiry</label>` : ""}`;
    }

    function renderPhosagroFundingSettings() {
        const fields = [...phosagroFundingFields, ["replacementRate", "Replacement debt cash interest (%)", 100]];
        if (partialRollover) fields.push(["rollover", "Future loan and bond principal rolled (%)", 100]);
        phosagroFundingSettings.innerHTML = `<div class="rates">${fundingInputs(fields, phosagroFunding)}</div>`;
    }

    function phosagroFundingCells(result) {
        return result instanceof RangeError ? ["N/A", "N/A", "N/A"] :
            [money(result.minimumLiquidity), money(result.peakReplacement), fundingGap(result.firstGap, "₽")];
    }

    const phosagroFinancingLabel = () => partialRollover ? "Partial rollover" : "No refinancing";

    function renderPhosagroFunding(periodLabel, profile, valuations, assumptions) {
        const results = Object.fromEntries(Object.entries(valuations).map(([name, valuation]) => [
            name, valuation instanceof RangeError ? valuation :
                attempt(() => phosagroLiquidityForecast(valuation.forecast, profile, assumptions)),
        ]));
        const selectedName = scenarioSelect.value;
        const current = results[selectedName];
        const detail = document.getElementById("phosagro-funding-detail");
        const status = document.getElementById("phosagro-funding-status");
        if (current instanceof RangeError) {
            status.textContent = current.message;
            detail.replaceChildren();
        } else {
            status.innerHTML = current.firstGap ?
                `<strong>${selectedName} first funding gap: ${fundingGap(current.firstGap, "₽")}.</strong>` :
                `<strong>${selectedName} has no modeled funding gap over five years.</strong>`;
            renderLiquidityDetail(detail, selectedName, current, {currency: "RUB", showHistoricalFinancing: true});
        }
        document.getElementById("phosagro-funding-comparison").innerHTML = table(
            `${periodLabel} forecast · ${phosagroFinancingLabel()} · funding movements through ${profile.eventsThrough}`,
            ["Operating case", "DCF value, RUB/share", "Minimum month-end liquidity, RUB m",
                "Peak replacement borrowing, RUB m", "First funding gap"],
            Object.entries(results).map(([name, result]) => row(name, [
                valuations[name] instanceof RangeError ? "N/A" : money(valuations[name].perShare, 2),
                ...phosagroFundingCells(result),
            ])).join(""));
    }

    function renderPhosagroRecovery(ltm, normalized, financing) {
        const {normalization: profile, liquidity} = ltm;
        const scenario = selected();
        const result = normalized instanceof RangeError ? normalized :
            attempt(() => phosagroRecoveryPaths(ltm, profile, normalized, scenario, phosagroTiming));
        const status = document.getElementById("phosagro-recovery-status");
        const basis = document.getElementById("phosagro-recovery-basis");
        const comparison = document.getElementById("phosagro-recovery-comparison");
        const detail = document.getElementById("phosagro-recovery-forecast");
        const fundingDetail = document.getElementById("phosagro-recovery-funding-detail");
        if (result instanceof RangeError) {
            status.textContent = result.message;
            [basis, comparison, detail, fundingDetail].forEach(element => element.replaceChildren());
            return;
        }
        const {paths, target, slowest} = result;
        const funding = Object.fromEntries(paths.map(path => [
            path.years, attempt(() => phosagroLiquidityForecast(path.forecast, liquidity, financing)),
        ]));
        const path = paths.find(path => path.years === phosagroDetailPeriod);
        const pathFunding = funding[path.years];
        basis.innerHTML = `The ${scenarioSelect.value} case starts from ${profile.history.adjusted.at(-1)} million RUB
            of trailing operating profit excluding operating FX and targets the ${percent(normalized.startingMargin)}
            starting margin in Normalized. All four paths use ${money(scenario.wacc)}% WACC,
            ${money(scenario.terminalGrowth)}% terminal growth, ${money(scenario.terminalRoic)}% terminal ROIC and
            a ${percent(target.terminalMargin)} terminal operating margin. The same terminal cash flows contribute
            ${money(target.terminalValue)} million RUB in present value to each path.`;
        const close = `the ${ltm.priceDate} close of ${ltm.links.price} RUB`;
        const priceResult = slowest ?
            `${slowest.label} is the slowest tested path that meets ${close}.` :
            `All four tested paths fall below ${close}.`;
        const supported = paths.findLast(path => path.delta >= 0 &&
            !(funding[path.years] instanceof RangeError) && !funding[path.years].firstGap);
        status.innerHTML = `${priceResult} ${pathFunding instanceof RangeError ? pathFunding.message :
            supported ? `${supported.label} is the slowest tested path that also has no modeled funding gap.` :
                "No tested path meets the close and avoids a modeled funding gap."}`;
        comparison.innerHTML = table(`${scenarioSelect.value} case · ${phosagroFinancingLabel()}`,
            ["Recovery", "DCF value, RUB/share", "Difference from close, RUB/share",
                "Minimum month-end liquidity, RUB m", "Peak replacement borrowing, RUB m", "First funding gap"],
            paths.map(path => row(path.label, [
                money(path.perShare, 2), `${path.delta > 0 ? "+" : ""}${money(path.delta, 2)}`,
                ...phosagroFundingCells(funding[path.years]),
            ])).join(""));
        detail.innerHTML = table(`${scenarioSelect.value} case · ${path.label} · RUB millions`,
            ["Metric", ...path.forecast.map((_, index) => `Year ${index + 1}`)], [
                ["Selling price effect", year => money(year.effects.price)],
                ["Shipment volume effect", year => money(year.effects.volume)],
                ["Sulphur cost effect", year => money(year.effects.sulphur)],
                ["Other raw-material cost effect", year => money(year.effects.inputs)],
                ["Revenue basis effect", year => money(year.effects.other)],
                ["Operating profit", year => money(year.operatingProfit)],
                ["Operating margin", year => percent(year.margin)],
                ["Unlevered free cash flow", year => money(year.fcff)],
                ["Cumulative unlevered free cash flow", year => money(year.cumulativeCash)],
            ].map(([label, value]) => row(label, path.forecast.map(value))).join(""));
        if (pathFunding instanceof RangeError) fundingDetail.replaceChildren();
        else renderLiquidityDetail(fundingDetail, path.label, pathFunding, {currency: "RUB", showHistoricalFinancing: true});
    }

    const rateText = value => `${money(value, 2)}%`;

    function rolloverThreshold(funding, missing) {
        if (!funding) return missing;
        const {minimum, maximum, binding, upperBinding} = funding;
        const constraint = (month, prefix = "") => {
            if (!month) return "";
            const maturities = month.maturities.map(({link, principal}) => `${link} (₽${money(principal / 1000, 2)}bn)`);
            return `<div>${prefix}${month.month} · ${maturities.join(" · ") || "No loan or bond principal due"}</div>`;
        };
        const limit = minimum === null ? "No feasible rollover" :
            maximum === 100 ? rateText(minimum) : `${rateText(minimum)} minimum · ${rateText(maximum)} maximum`;
        const detail = constraint(binding) + (upperBinding === binding ? "" : constraint(upperBinding, "Upper limit "));
        return `${limit}${detail ? `<div class="map-maturities">${detail}</div>` : ""}`;
    }

    function renderPhosagroBreakEven(ltm) {
        const name = scenarioSelect.value;
        const result = attempt(() => phosagroBreakEven(ltm, name, selected(), phosagroTiming, phosagroFunding, breakEvenRange));
        const basis = document.getElementById("phosagro-break-even-basis");
        const status = document.getElementById("phosagro-break-even-status");
        const thresholds = document.getElementById("phosagro-break-even-thresholds");
        const grid = document.getElementById("phosagro-break-even-grid");
        if (result instanceof RangeError) {
            status.textContent = result.message;
            [basis, thresholds, grid].forEach(element => element.replaceChildren());
            return;
        }
        const {paths, rolloverAxis, selectedRelief} = result;
        const path = paths.find(path => path.years === Number(breakEvenPath.value));
        basis.innerHTML = `These thresholds use the ${name} case, the ${ltm.priceDate} close of ${ltm.links.price} RUB,
            and ${rateText(phosagroFunding.replacementRate)} cash interest on rolled debt. The map varies sulphur relief
            and rollover while holding the other operating and debt assumptions fixed.`;
        const selectedCost = Number.isFinite(selectedRelief) && selectedRelief <= 100 ?
            selectedRelief >= 0 ? `${rateText(selectedRelief)} sulphur relief` :
                `${rateText(-selectedRelief)} sulphur cost increase` : "selected sulphur cost";
        thresholds.innerHTML = table(`${name} case · thresholds for all recovery paths`,
            ["Recovery path", "Quote-equivalent margin", "Sulphur relief for the quote", "Minimum rollover at price threshold",
                `Minimum rollover at ${selectedCost}`, "Both tests pass on the grid"],
            paths.map(rowPath => {
                const {quoteRelief, quoteMargin, reliefAtThreshold, upperRelief, fundingAtQuote, fundingAtSelectedRelief} = rowPath;
                let relief = "0% needed";
                if (reliefAtThreshold === null) {
                    relief = quoteRelief === null ? "Price unreachable" : `${rateText(quoteRelief)} outside range`;
                } else if (upperRelief !== null) {
                    relief = reliefAtThreshold === 0 ? `Up to ${rateText(upperRelief)}` :
                        `${rateText(reliefAtThreshold)} to ${rateText(upperRelief)}`;
                } else if (reliefAtThreshold > 0) {
                    relief = rateText(reliefAtThreshold);
                }
                return row(rowPath.label, [
                    quoteMargin === null ? "N/A" : rateText(quoteMargin * 100),
                    relief,
                    rolloverThreshold(fundingAtQuote, "Price unreachable"),
                    rolloverThreshold(fundingAtSelectedRelief, "N/A"),
                    rowPath.bothPass ? "Yes" : "No sampled point",
                ]);
            }).join(""));
        status.textContent = path.bothPass ? "" : "No displayed combination passes both tests for this path.";
        grid.innerHTML = table(`${name} case · ${path.label} · debt rollover by column`,
            ["Sulphur relief", "DCF, RUB/share", ...rolloverAxis.map(rateText)],
            path.rows.map(({relief, perShare, meetsQuote, cells}) =>
                `<tr><th scope="row">${rateText(relief)}</th><td>${money(perShare, 2)}</td>${cells.map(({funded, minimumLiquidity}) => {
                    const [style, label] = meetsQuote ? funded ? ["both", "Both"] : ["price", "Price only"] :
                        funded ? ["funded", "Funding only"] : ["neither", "Neither"];
                    return `<td class="map-${style}" title="Minimum month-end cash ${money(minimumLiquidity)} RUB m">${label}</td>`;
                }).join("")}</tr>`).join(""));
    }

    function updateCompany() {
        const annual = company();
        const option = document.getElementById("normalized-period");
        option.hidden = option.disabled = !annual.normalization;
        document.getElementById("annual-period").textContent = annual.periodLabel;
        const ltmOption = document.getElementById("ltm-period");
        ltmOption.hidden = ltmOption.disabled = !annual.ltm;
        if ((option.disabled && period.value === "normalized") || (ltmOption.disabled && period.value === "ltm")) {
            period.value = annual.ltm ? "ltm" : "annual";
        }
        const isPhosagro = issuer.value === "phosagro";
        const normalization = document.getElementById("normalization");
        const phosagroNormalization = document.getElementById("phosagro-normalization");
        normalization.hidden = isPhosagro || !annual.normalization || period.value !== "normalized";
        phosagroNormalization.hidden = !isPhosagro || period.value !== "normalized";
        if (!normalization.hidden) renderNormalization();
        if (!phosagroNormalization.hidden) renderPhosagroNormalization();
        document.getElementById("recovery").hidden = isPhosagro || !annual.normalization;
        document.getElementById("phosagro-funding").hidden = !isPhosagro;
        const debt = annual.debtAssumption;
        document.getElementById("debt-analysis").hidden = !debt;
        if (debt) {
            assumedBorrowing.value = debt.value;
            document.getElementById("debt-context").innerHTML = `<strong>${debt.label} requires review.</strong>
                ${debt.reason} ${debt.source}`;
            document.getElementById("debt-components").innerHTML = table(
                `Reported balances used for net debt · ${annual.currency} millions`,
                ["Component", "Amount"], debt.components.map(([label, link]) => row(label, [link])).join(""));
        }
        document.getElementById("ltm-details").hidden = !annual.ltm;
        if (annual.ltm) {
            const flows = annual.ltm.ltmBreakdown;
            document.getElementById("ltm-calculation").innerHTML = table(
                `Trailing calculation · ${annual.currency} millions`,
                ["Metric", annual.periodLabel, "Current nine months", "Prior nine months", annual.ltm.periodLabel],
                [
                    ["Revenue", flows.revenue], ["Operating profit", flows.operatingProfit],
                    ["Operating cash flow", flows.operatingCash], ["Capex outflow", flows.capex],
                ].map(([label, values]) => row(label, values)).join(""),
            );
        }
        renderControls();
        renderResults();
    }

    const presets = structuredClone(companies);
    const fundingProfile = companies.mosaic?.liquidity;
    if (fundingProfile) {
        document.getElementById("liquidity-evidence").innerHTML = table(
            `Mosaic reported funding inputs at ${fundingProfile.asOf} · USD millions unless dated`,
            ["Metric", "Amount or date"], fundingProfile.evidence.map(([label, value]) => row(label, [value])).join(""));
        document.getElementById("liquidity-debt").innerHTML = table("Mosaic maturity schedule · USD millions",
            ["Payment date or year", "Debt", "Principal"], fundingProfile.schedule.map(([due, label, principal]) =>
                row(due, [label, principal])).join(""));
        document.getElementById("liquidity-schedule-note").innerHTML = fundingProfile.refinancingLink;
        renderLiquiditySettings();
    }
    const phosagroProfile = companies.phosagro?.liquidity;
    if (phosagroProfile) {
        document.getElementById("phosagro-funding-evidence").innerHTML = table(
            `PhosAgro funding inputs at ${phosagroProfile.asOf} · RUB millions unless noted`,
            ["Metric", "Reported amount or source"], phosagroProfile.evidence.map(([label, value]) => row(label, [value])).join(""));
        document.getElementById("phosagro-funding-events").innerHTML = table("Post-period financing · RUB millions",
            ["Date", "Movement", "Amount or source"], phosagroProfile.events.map(([date, ...values]) => row(date, values)).join(""));
        document.getElementById("phosagro-funding-debt").innerHTML = table("Debt terms and principal basis",
            ["Instrument", "Currency", "Principal basis", "Legal maturity or proxy", "Next put", "Coupon", "Terms and sources"],
            phosagroProfile.schedule.map(([name, ...values]) => row(name, values)).join(""));
        renderPhosagroFundingSettings();
    }
    financingCase.addEventListener("change", () => {
        customFinancing = financingCase.value === "custom";
        renderLiquiditySettings();
        renderResults();
    });
    liquiditySettings.addEventListener("input", event => {
        const input = event.target;
        liquiditySettingsValues[input.dataset.funding] = input.type === "checkbox" ? input.checked : input.valueAsNumber;
        renderResults();
    });
    phosagroFinancing.addEventListener("change", () => {
        partialRollover = phosagroFinancing.value === "partial";
        renderPhosagroFundingSettings();
        renderResults();
    });
    phosagroFundingSettings.addEventListener("input", event => {
        phosagroFunding[event.target.dataset.funding] = event.target.valueAsNumber;
        renderResults();
    });
    const recoveryControls = (fields, timing) => fields.map(([key, label]) =>
        `<label>${label}<select data-timing="${key}">
            ${recoveryCurves.map(([value, name]) => option(value, name, timing[key])).join("")}
        </select></label>`);
    const recoveryFields = [["price", "Selling price transition"], ["volume", "Shipment volume transition"]];
    recoverySettings.innerHTML = [
        ...recoveryControls([...recoveryFields, ["inputs", "Input cost transition"]], recoveryTiming),
        `<label>Care costs through<select data-timing="careThrough">${Array.from({length: 6}, (_, year) =>
            option(year, year === 0 ? "Exit before Year 1" : `End of Year ${year}`, recoveryTiming.careThrough)).join("")}
        </select></label>`,
    ].join("");
    recoverySelection.innerHTML = recoveryPeriods.map(([years, label]) => option(years, label, detailPeriod)).join("");
    recoverySettings.addEventListener("change", event => {
        recoveryTiming[event.target.dataset.timing] = Number(event.target.value);
        renderResults();
    });
    recoverySelection.addEventListener("change", () => {
        detailPeriod = Number(recoverySelection.value);
        renderResults();
    });
    phosagroRecoverySettings.innerHTML = recoveryControls([
        ...recoveryFields, ["sulphur", "Sulphur cost transition"], ["inputs", "Other raw-material cost transition"],
    ], phosagroTiming).join("");
    phosagroRecoverySelection.innerHTML = recoveryPeriods.map(([years, label]) => option(years, label, phosagroDetailPeriod)).join("");
    phosagroRecoverySettings.addEventListener("change", event => {
        phosagroTiming[event.target.dataset.timing] = Number(event.target.value);
        renderResults();
    });
    phosagroRecoverySelection.addEventListener("change", () => {
        phosagroDetailPeriod = Number(phosagroRecoverySelection.value);
        renderResults();
    });
    breakEvenPath.innerHTML = recoveryPeriods.map(([years, label]) => option(years, label, 5)).join("");
    breakEvenPath.value = "5";
    breakEvenPath.addEventListener("change", () => renderPhosagroBreakEven(trailing()));
    document.getElementById("phosagro-break-even-settings").addEventListener("input", event => {
        breakEvenRange[event.target.dataset.map] = event.target.valueAsNumber;
        renderPhosagroBreakEven(trailing());
    });
    issuer.addEventListener("change", () => {
        loadGrowthMarginMap(company());
        loadLocationControls(company());
        updateCompany();
    });
    period.addEventListener("change", updateCompany);
    scenarioSelect.addEventListener("change", () => {
        renderControls();
        renderResults();
    });
    assumedBorrowing.addEventListener("input", () => {
        company().debtAssumption.value = assumedBorrowing.valueAsNumber;
        renderResults();
    });
    for (const input of [growthRatesInput, marginsInput]) {
        input.addEventListener("input", () => renderGrowthMarginMap(company()));
    }
    locationControls.addEventListener("input", ({target}) => {
        const key = target.dataset.location;
        const config = company().locationGrowth;
        if (key === "growthRates") config.growthRates = percentList(target.value);
        else config.assumptions[key] = key === "ramp" ? percentList(target.value) : target.valueAsNumber;
        renderLocationResults(company());
    });
    controls.addEventListener("input", event => {
        const input = event.target;
        const {key, year} = input.dataset;
        if (year === "") selected()[key] = input.valueAsNumber;
        else (selected().revenueGrowth?.[key] ?? selected()[key])[Number(year)] = input.valueAsNumber;
        renderResults();
    });
    document.getElementById("cycle-controls").addEventListener("input", event => {
        const {case: name, segment, driver} = event.target.dataset;
        company().normalization.cases[name][segment][driver] = event.target.valueAsNumber;
        renderResults();
    });
    document.getElementById("phosagro-cycle-controls").addEventListener("input", event => {
        const {case: name, driver} = event.target.dataset;
        company().normalization.cases[name][driver] = event.target.valueAsNumber;
        renderResults();
    });
    document.getElementById("reset").addEventListener("click", () => {
        const name = scenarioSelect.value;
        company().scenarios[name] = structuredClone(presets[issuer.value].scenarios[name]);
        if (company().normalization) {
            company().normalization.cases[name] = structuredClone(presets[issuer.value].normalization.cases[name]);
            if (period.value === "normalized") {
                if (issuer.value === "phosagro") renderPhosagroNormalization();
                else renderNormalization();
            }
        }
        renderControls();
        renderResults();
    });
    loadGrowthMarginMap(company());
    loadLocationControls(company());
    updateCompany();
}

if (typeof document !== "undefined") {
    startValuation();
}
