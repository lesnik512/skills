import json
import re
from datetime import date, timedelta
from decimal import Decimal
from html import escape

from analysis_input import MILLION, Figure, Source, decimal_string, iso_date, source_from_json
from build_report import HERE, annual_facts, calculate, combined, filing_source, ratio, render_figure, total


PROFILES = HERE.parent / "profiles"


def linked_figures(figures):
    return {metric: render_figure(figure, "") for metric, figure in figures.items()}


LTM_METRICS = {"revenue": "revenue", "operatingProfit": "operating_profit",
               "operatingCash": "operating_cash", "capex": "capex"}


def required(figure, metric, end):
    if figure is None:
        raise ValueError(f"{metric} is missing or unresolved for {end}")
    return figure


def interim_snapshot(inputs, annual, year, interim, quote_date):
    end = iso_date(interim["end"])
    start, prior_start, prior_end = (iso_date(interim[key]) for key in ("start", "prior_start", "prior_end"))
    prior_annual = inputs.periods[str(year)]
    if (not prior_start == prior_annual["start"] <= prior_end < prior_annual["end"] < start <= end <= quote_date
            or date.fromisoformat(start) != date.fromisoformat(prior_annual["end"]) + timedelta(days=1)
            or abs((date.fromisoformat(end) - date.fromisoformat(start)).days -
                   (date.fromisoformat(prior_end) - date.fromisoformat(prior_start)).days) > 1):
        raise ValueError("Interim and comparative dates must follow the annual reporting period and precede the quote")
    result, breakdown, flows = {}, {}, {}
    for key, metric in LTM_METRICS.items():
        opening = required(annual.get(key), metric, prior_annual["end"])
        recent = required(inputs.figure(metric, start, end), metric, end)
        comparative = required(inputs.figure(metric, prior_start, prior_end), metric, prior_end)
        result[key] = combined(opening.value + recent.value - comparative.value, opening, recent, comparative)
        breakdown[key] = [render_figure(figure, "") for figure in (opening, recent, comparative, result[key])]
        flows[key] = (recent, comparative)

    balances = {
        metric: required(inputs.figure(metric, None, end), metric, end)
        for metric in (*interim["debtComponents"], "cash", "noncontrolling", "investments")
    }
    if interim.get("operating_lease_components"):
        leases = [required(inputs.figure(metric, None, end), metric, end)
                  for metric in interim["operating_lease_components"]]
        result["operatingLeaseDebt"] = total(*leases)
    debt = total(*(balances[metric] for metric in interim["debtComponents"]))
    cash = balances["cash"]
    result["netDebt"] = combined(debt.value - cash.value, debt, cash)
    for metric in ("noncontrolling", "investments"):
        result[metric] = balances[metric]

    share_date = iso_date(interim["shareDate"])
    if share_date > quote_date:
        raise ValueError("The interim share count date is after the quote")
    result["shares"] = required(inputs.figure("shares_outstanding", None, share_date, "shares"),
                                "shares_outstanding", share_date)
    if interim.get("share_note_source"):
        shares = result["shares"]
        result["shares"] = Figure(shares.value, (*shares.sources, source_from_json(interim["share_note_source"])))
    snapshot = {
        **{metric: figure.value for metric, figure in result.items()},
        "periodLabel": f"LTM to {end}",
        "balanceDate": end,
        "shareDate": share_date,
        "links": linked_figures(result),
        "ltmBreakdown": breakdown,
        "periodNote": escape(interim.get("note", "")),
    }
    return snapshot, {**interim, "flows": flows, "balances": balances}


def mosaic_normalization(company, facts, year, interim, ltm):
    profile = json.loads((PROFILES / "mosaic_normalization.json").read_text(encoding="utf-8"), parse_float=Decimal)
    if profile["annualYear"] != year or profile["interimEnd"] != interim["end"]:
        raise ValueError("Mosaic normalization inputs must match the annual and interim valuation periods")
    annual_date = date(year, 12, 31)
    interim_date = date.fromisoformat(interim["end"])
    sources = {}
    for period, end, url, filing in (
        ("annual", annual_date, company["filings"][str(year)]["url"], "Form 10-K"),
        ("interim", interim_date, interim["url"], "Form 10-Q"),
    ):
        sources[period] = {
            key: Source(end.year, key, f"{url}#{anchor}", f"Mosaic {filing}, {end}, {title}")
            for key, (anchor, title) in profile[f"{period}Sources"].items()
        }

    def figure(value, period, key):
        return Figure(Decimal(value), (sources[period][key],))

    def render(figures):
        return [render_figure(item, "") if item is not None else "—" for item in figures]

    def bridge_row(item, annual_key, current_key, prior_key="income"):
        figures = [
            figure(value, period, key) if value is not None else None
            for value, period, key in zip(item["amounts"], ("annual", "interim", "interim"),
                                          (annual_key, current_key, prior_key), strict=True)
        ]
        annual, current, prior = (item.value if item is not None else 0 for item in figures)
        adjustment = combined(annual + current - prior, *(item for item in figures if item is not None))
        references = [sources[period][key].link(label) for period, key, label in item["references"]]
        return {
            "label": item["label"],
            "amounts": render([*figures, adjustment]),
            "value": adjustment.value,
            "note": escape(item["note"]) + " " + " · ".join(references),
        }, adjustment

    prior_loss, current_loss = profile["nineMonthDisposalLoss"]
    fy_loss = profile["annualDisposalLoss"][-1]
    loss = [
        figure(fy_loss, "annual", "income"),
        figure(current_loss, "interim", "income"),
        figure(prior_loss, "interim", "income"),
    ]
    net_loss = combined(fy_loss + current_loss - prior_loss, *loss)
    disposal_rows = [
        bridge_row(item, "disposalLine", "assetLoss") for item in profile["disposalAdjustments"]
    ]
    for index, reported_loss in enumerate((fy_loss, current_loss, prior_loss)):
        if sum(item["amounts"][index] for item in profile["disposalAdjustments"]) != reported_loss:
            raise ValueError("Mosaic disposal adjustments do not reconcile to the reported line")
    exit_rows = [
        bridge_row(item, "income", "exit") for item in profile["exitAdjustments"]
    ]
    current_profit, prior_profit = interim["flows"]["operatingProfit"]
    reported = [facts[year, "operating_profit"], current_profit, prior_profit]
    trailing_profit = combined(ltm["operatingProfit"], *reported)
    adjusted = total(trailing_profit, net_loss, *(adjustment for _, adjustment in exit_rows))
    historical_margins = []
    for fiscal_year, disposal_loss, goodwill in zip(range(year - 2, year + 1), profile["annualDisposalLoss"],
                                                   profile["annualGoodwillImpairment"], strict=True):
        sales = facts[fiscal_year, "revenue"]
        profit = total(facts[fiscal_year, "operating_profit"],
                       figure(disposal_loss, "annual", "income"), figure(goodwill, "annual", "income"))
        historical_margins.append(render_figure(combined(profit.value / sales.value, profit, sales), "%"))
    for key, segment in profile["segments"].items():
        segment["salesLink"] = render_figure(figure(segment["externalSales"], "annual", "sales"), "")
        contribution = figure(segment["volumeContribution"], "annual", key)
        segment["contributionLink"] = render_figure(combined(contribution.value / 100, contribution), "%")
        for driver in segment["drivers"].values():
            driver["historyLinks"] = render([
                figure(value, period, key)
                for value, period in zip(driver["history"], ("annual",) * 3 + ("interim",) * 2, strict=True)
            ])
            if "quantity" in driver:
                driver["quantityLink"] = render_figure(figure(driver["quantity"], "annual", driver["quantitySource"]), "")
    return {
        "segments": profile["segments"],
        "cases": profile["cases"],
        "anchorEbit": reported[0].value + fy_loss + profile["annualGoodwillImpairment"][-1],
        "historicalMargins": historical_margins,
        "reportedLinks": render([*reported, trailing_profit]),
        "lossLinks": render([*loss, net_loss]),
        "adjustments": [row for row, _ in (*disposal_rows, *exit_rows)],
        "adjustedEbit": adjusted.value,
        "adjustedLink": render_figure(adjusted, ""),
        "careAndMaintenance": profile["careAndMaintenance"],
        "careLink": render_figure(figure(profile["careAndMaintenance"], "interim", "exit"), ""),
        "exitLink": sources["interim"]["sales"].link("Kronau disposal note"),
        "otherExpenseLink": sources["interim"]["assetLoss"].link("Other operating expense"),
    }


def phosagro_normalization(company, facts, year, interim):
    profile = json.loads((PROFILES / "phosagro_normalization.json").read_text(encoding="utf-8"), parse_float=Decimal)
    if profile["annualYear"] != year or profile["interimEnd"] != interim["end"]:
        raise ValueError("PhosAgro normalization inputs must match the annual and interim valuation periods")

    def trailing_series(values):
        annual, prior, current = values[-3:]
        ltm = combined(annual.value + current.value - prior.value, annual, prior, current)
        return [*values, ltm]

    def interim_source(page):
        return Source(year + 1, str(page), f'{interim["url"]}#page={page}',
                      f'PhosAgro consolidated interim IFRS, {interim["end"]}, PDF page {page}')

    series = {}
    for metric, (section, values) in profile["series"].items():
        comparative_page, annual_page, interim_page = profile["sources"][section]
        comparative = Source(year - 1, str(comparative_page), f'{profile["fy2023Comparative"]}#page={comparative_page}',
                             f"PhosAgro FY{year - 1} consolidated IFRS, PDF page {comparative_page}")
        annual = filing_source(company, year, str(annual_page))
        recent = interim_source(interim_page)
        series[metric] = trailing_series([
            Figure(value, (source,))
            for value, source in zip(values, (comparative, annual, annual, recent, recent), strict=True)
        ])

    for metric, fact in (("revenue", "revenue"), ("reported", "operating_profit"),
                         ("operatingCash", "operating_cash"), ("capex", "capex")):
        current, prior = interim["flows"]["operatingProfit" if metric == "reported" else metric]
        series[metric] = trailing_series([
            *(facts[fiscal_year, fact] for fiscal_year in range(year - 2, year + 1)),
            prior, current,
        ])
    series["fxAdjustment"] = [combined(-fx.value, fx) for fx in series["operatingFx"]]
    series["adjusted"] = [total(profit, fx) for profit, fx in zip(series["reported"], series["fxAdjustment"], strict=True)]
    series["margin"] = [combined(profit.value / sales.value, profit, sales)
                        for profit, sales in zip(series["adjusted"], series["revenue"], strict=True)]
    series["inputs"] = [total(*entries) for entries in zip(
        *(series[key] for key in ("ammonia", "potassiumChloride", "naturalGas", "ammoniumSulphate")), strict=True,
    )]
    series["workingCapitalCash"] = [total(*entries) for entries in zip(
        *(series[key] for key in ("receivablesCash", "inventoryCash", "payablesCash")), strict=True,
    )]
    series["cashAfterCapex"] = [total(cash, capex) for cash, capex in zip(
        series["operatingCash"], series["capex"], strict=True,
    )]
    shipments_source = Source(year, "shipments", profile["volumeSource"], "PhosAgro integrated report, sales volumes")
    series["shipments"] = [Figure(value, (shipments_source,)) for value in profile["shipmentsKt"]] + [None] * 3
    series["unitRevenue"] = [
        combined((sales.value * 1000 / volume.value).quantize(Decimal(1)), sales, volume) if volume else None
        for sales, volume in zip(series["productRevenue"], series["shipments"], strict=True)
    ]
    sales = series["revenue"][-1]
    gross_profit = series["grossProfit"][-1]
    contribution = combined(gross_profit.value / sales.value, gross_profit, sales)
    return {
        "cases": profile["cases"],
        "adjustedEbit": series["adjusted"][-1].value,
        "productRevenue": series["productRevenue"][-1].value,
        "sulphur": series["sulphur"][-1].value,
        "inputs": series["inputs"][-1].value,
        "volumeContribution": contribution.value,
        "contributionLink": render_figure(contribution, "%"),
        "history": {
            metric: [render_figure(figure, "%" if metric == "margin" else "") for figure in figures]
            for metric, figures in series.items()
        },
    }


def mosaic_liquidity(company, year, interim):
    profile = json.loads((PROFILES / "mosaic_liquidity.json").read_text(encoding="utf-8"), parse_float=Decimal)
    if profile["asOf"] != interim["end"]:
        raise ValueError("Mosaic liquidity inputs must match the interim balance sheet date")
    urls = {"annual": company["filings"][str(year)]["url"], "interim": interim["url"]}
    sources = {
        key: Source(year if origin == "annual" else date.fromisoformat(profile["asOf"]).year,
                    key, urls.get(origin, origin) + (f"#{anchor}" if anchor else ""), title)
        for key, (origin, anchor, title) in profile["sources"].items()
    }
    balance = Source(date.fromisoformat(profile["asOf"]).year, "balance",
                     f'{interim["url"]}#{interim["sections"]["balance"]}', "Mosaic 10-Q balance sheet")
    balances = interim["balances"]
    cash = balances["cash"].value
    short_debt = balances["shortDebt"].value
    current_maturities = balances["currentMaturities"].value
    long_debt = balances["longDebt"].value + current_maturities
    facility = profile["revolver"]
    restricted = sum(profile["restrictedCash"])
    charges = profile["exitCharges"]
    exit_cash = charges["contractTermination"] + charges["severance"]

    def field(label, value, *refs):
        return [label, render_figure(Figure(Decimal(value), refs), "")]

    evidence = [
        field("Opening cash and cash equivalents", cash, balance, sources["cashFlow"]),
        field("Restricted cash held separately", restricted, sources["cashFlow"]),
        field("Committed revolver", facility["commitment"], sources["revolver"], sources["updatedSchedule"]),
        ["Revolver maturity", sources["revolver"].link(facility["expires"])],
        field("Commercial paper outstanding", facility["commercialPaper"], sources["workingCapital"]),
        field("Short-term debt", short_debt, balance),
        field("Inventory financing included in short-term debt", profile["inventoryFinancing"], sources["workingCapital"]),
        field("Structured supplier payables", profile["supplierFinancing"], sources["workingCapital"]),
        field("Current maturities of long-term debt", current_maturities, balance),
        field("Long-term debt including current maturities", long_debt, balance),
        field("Nine-month cash interest paid, net of capitalized interest", profile["cashInterestNineMonths"], sources["cashFlow"]),
        field("Nine-month cash dividends paid", profile["dividendsNineMonths"], sources["cashFlow"]),
        field("Exit charges for contract termination", charges["contractTermination"], sources["exit"]),
        field("Exit charges for severance and employee costs", charges["severance"], sources["exit"]),
    ]
    maturities = []
    schedule = []
    for due, label, principal, keys in profile["maturities"]:
        maturities.append([due, principal])
        schedule.append([due, label, render_figure(Figure(Decimal(principal), tuple(sources[key] for key in keys)), "")])
    other = {fiscal_year: total_due - named for fiscal_year, total_due, named in profile["olderSchedule"]}
    for fiscal_year, amount in other.items():
        schedule.append([f"{fiscal_year} estimate", "Other maturity proxy, annual amount",
                         render_figure(Figure(amount, (sources["oldMaturities"],)), "")])
    return {
        "asOf": profile["asOf"], "cash": cash,
        "revolverLimit": facility["commitment"] - facility["commercialPaper"],
        "revolverExpiry": facility["expires"], "legacyDebt": short_debt + long_debt,
        "shortDebt": short_debt, "supplierFinancing": profile["supplierFinancing"],
        "shortTermPaymentMonth": profile["shortTermPaymentMonth"],
        "currentMaturities": current_maturities, "namedMaturities": maturities, "otherMaturities": other,
        "exitMonth": charges["paymentMonth"],
        "assumptions": {**profile["assumptions"], "exitCash": exit_cash,
                        "dividends": profile["dividendsNineMonths"] * 4 / 3},
        "evidence": evidence, "schedule": schedule,
        "refinancingLink": sources["updatedSchedule"].link("Updated corporate debt maturity chart"),
    }


def phosagro_liquidity(interim, quote_date):
    profile = json.loads((PROFILES / "phosagro_liquidity.json").read_text(encoding="utf-8"), parse_float=Decimal)
    as_of = profile["asOf"]
    if as_of != interim["end"] or profile["eventsThrough"] != quote_date:
        raise ValueError("PhosAgro liquidity inputs must match the interim balance sheet and valuation dates")
    as_of_year = date.fromisoformat(as_of).year
    sources = {
        key: Source(as_of_year, key, f'{interim["url"]}#page={locator}' if locator.isdecimal() else locator, title)
        for key, (locator, title) in profile["sources"].items()
    }

    def linked(value, *keys):
        return render_figure(Figure(Decimal(value), tuple(sources[key] for key in keys)), "")

    def links(*keys):
        return " · ".join(sources[key].link(sources[key].title) for key in keys)

    balances = interim["balances"]
    cash = balances["cash"].value
    current_debt, noncurrent_debt = (balances[key].value for key in ("shortDebt", "longDebt"))
    leases = [balances[key].value for key in ("shortLease", "longLease")]
    initial_debt = current_debt + noncurrent_debt
    bank = profile["bank"]
    currency_debt = profile["debtByCurrency"]
    book_bonds = sum(row[2] for row in profile["namedBonds"])
    if (book_bonds + bank["current"] + bank["noncurrent"] + bank["accruedInterest"] -
        bank["currentFees"] - bank["noncurrentFees"] != initial_debt or
        sum(currency_debt.values()) != initial_debt or sum(profile["maturityBands"]) != initial_debt):
        raise ValueError("PhosAgro loans, bonds and maturity totals do not reconcile to interim debt")

    put = profile["bondPut"]
    repurchase_principal = Decimal(put["repurchasedUnits"] * put["facePerUnit"]) / 1_000_000
    september_fx = profile["fxSeptember"]
    quote_fx = profile["fxQuote"]
    contracts = []
    debt_rows = []
    for name, currency, book, maturity, rate_type, rate, keys in profile["namedBonds"]:
        has_put = name == put["name"]
        fields = {"name": name, "currency": currency, "principal": Decimal(book) / september_fx[currency],
                  "maturity": maturity, "rateType": rate_type, "rate": rate, "maturityLink": sources["bonds"].link(name)}
        if has_put:
            fields.update(put=put["nextPut"], repurchase=repurchase_principal, repurchaseDate=put["repurchaseDate"],
                          previousSpread=put["previousSpread"], putLink=sources["put201"].link(f"{name} put"))
        contracts.append(fields)
        rate_display = f"Key rate + {rate} pp" if rate_type == "keyRate" else f"{rate}% fixed"
        debt_rows.append([name, currency, linked(book, "bonds") + " RUB m", maturity,
                          sources["put201"].link(put["nextPut"]) if has_put else "—", rate_display, links(*keys)])

    october = profile["october"]
    unidentified_issue = Decimal(october["borrowed"] - october["relatedPartyRoundTrip"])
    historical_repayments = october["repaid"] - october["relatedPartyRoundTrip"]
    event_rows = [
        ["Through 2026-10-21", "Borrowing reported in the interim note", linked(october["borrowed"], "events", "authorization")],
        ["Through 2026-10-21", "Repayments reported in the interim note", linked(-october["repaid"], "events")],
        ["Through 2026-10-21", "Related-party loan raised and repaid, each leg", linked(october["relatedPartyRoundTrip"], "events")],
    ]
    historical_proceeds = {}
    for name, currency, face, maturity, coupon, issue_date, fx, keys in profile["issues"]:
        proceeds = face * fx
        contracts.append({"name": name, "currency": currency, "principal": face, "maturity": maturity,
                          "rateType": "fixed", "rate": coupon, "issued": issue_date,
                          "maturityLink": sources[keys[1]].link(name)})
        debt_rows.append([name, currency, linked(face, keys[0]) + f" {currency} m", maturity, "—",
                          f"{coupon}% fixed", links(*keys)])
        if issue_date <= october["authorization"]:
            unidentified_issue -= proceeds
            event_rows.append([issue_date, f"{name} issue included in the interim note total", linked(proceeds, *keys)])
        else:
            month = issue_date[:7]
            historical_proceeds[month] = historical_proceeds.get(month, 0) + proceeds
            event_rows.append([issue_date, f"{name} proceeds after the interim note cutoff", linked(proceeds, *keys)])
    month = october["authorization"][:7]
    historical_proceeds[month] = historical_proceeds.get(month, 0) + october["borrowed"] - october["relatedPartyRoundTrip"]
    event_rows.append([put["repurchaseDate"], f'{put["name"]} principal bought back under the November put',
                       linked(-repurchase_principal, "repurchase")])
    event_rows.append([month, "Borrowing with unallocated terms within the interim note", linked(unidentified_issue, "events", "issue301", "fxOctober9")])
    event_rows.append([put["reportedPut"], "Put reference date used for the September current-debt classification",
                       sources["bonds"].link("September financial statement")])
    if unidentified_issue < 0 or historical_repayments > bank["current"]:
        raise ValueError("PhosAgro October cash movements exceed their modeled source balances")

    cny_bank = currency_debt["CNY"] - sum(row[2] for row in profile["namedBonds"] if row[1] == "CNY")
    eur_current = currency_debt["EUR"] - bank["noncurrent"]
    usd_residual = bank["current"] - cny_bank - eur_current
    bank_contracts = [("CNY", cny_bank, True), ("EUR", eur_current, True), ("USD", usd_residual, True),
                      ("EUR", bank["noncurrent"], False)]
    for currency, book, current in bank_contracts:
        contracts.append({"currency": currency, "principal": Decimal(book) / september_fx[currency],
                          "rateType": "bankRate", "currentBank": current,
                          "maturity": None if current else f"{as_of_year + 2}-09-30",
                          "maturityLink": sources["balances"].link("Noncurrent bank loan estimate")})
        debt_rows.append(["Bank loan allocation", currency, linked(book, "balances", "bonds", "events") + " RUB m",
                          "Editable within Year 1" if current else "September 2028 estimate", "—",
                          "Editable cash rate", links("balances", "bonds", "events")])
    debt_rows.append(["Unallocated October borrowing", "RUB assumed", linked(unidentified_issue, "events", "issue301") + " RUB m",
                      "Editable", "—", "Editable cash rate", links("events", "authorization")])
    debt_rows.sort(key=lambda row: row[3])

    evidence = [
        ["Reported opening cash and cash equivalents", linked(cash, "cash")],
        ["Current loans and bonds", linked(current_debt, "balances")],
        ["Noncurrent loans and bonds", linked(noncurrent_debt, "balances")],
        ["Bank loans due within 12 months", linked(bank["current"], "balances")],
        ["Accrued interest included in current debt", linked(bank["accruedInterest"], "balances")],
        ["Reported maturity buckets, up to 1 year / 1–2 / 2–3", " / ".join(linked(value, "bonds") for value in profile["maturityBands"])],
        ["Debt denominated in RUB / USD / CNY / EUR", " / ".join(linked(currency_debt[currency], "bonds")
                                                                  for currency in ("RUB", "USD", "CNY", "EUR"))],
        ["Current and noncurrent lease principal", " / ".join(linked(value, "leases") for value in leases)],
        ["Nine-month lease interest paid", linked(profile["leaseInterestNineMonths"], "leases")],
        ["Disclosed credit lines", links("credit")],
        ["Bank of Russia reference key rate", linked(profile["assumptions"]["keyRate"], "keyRate") + "%"],
        *[[f"RUB per {currency}, September 30 / November 20",
           f'{linked(september_fx[currency], "fxSeptember")} / {linked(quote_fx[currency], "fxQuote")}']
          for currency in ("USD", "CNY", "EUR")],
    ]
    return {
        "asOf": as_of, "eventsThrough": quote_date, "cash": cash, "revolverLimit": 0, "revolverExpiry": as_of,
        "septemberFx": september_fx, "quoteFx": quote_fx, "contracts": contracts,
        "historicalProceeds": historical_proceeds, "historicalRepayments": {month: historical_repayments},
        "currentBank": bank["current"], "accruedInterest": bank["accruedInterest"],
        "currentBankLink": sources["balances"].link("Current bank loans (modeled date)"),
        "unallocated": unidentified_issue, "unallocatedStart": month,
        "unallocatedLink": sources["events"].link("Unallocated October borrowing (modeled date)"),
        "leases": leases, "leaseInterestNineMonths": profile["leaseInterestNineMonths"],
        "assumptions": profile["assumptions"], "evidence": evidence, "schedule": debt_rows, "events": event_rows,
    }


def net_debt_bridge(inputs, year, bridge):
    end = inputs.periods[str(year)]["end"]
    add, subtract = bridge["add"], bridge["subtract"]
    assumed = bridge["unquantified"]
    metric = assumed["metric"]
    record = inputs.records.get((metric, None, end), {})
    if record.get("status") != "Missing" or record.get("decision") is not None or record.get("unit") != inputs.currency:
        raise ValueError("An assumed borrowing requires an unresolved missing balance in the reporting currency")
    if not add or not subtract or len(set([*add, *subtract, metric])) != len(add) + len(subtract) + 1:
        raise ValueError("Net debt requires distinct reported debt and cash components")

    components = {key: required(inputs.annual(year, key, balance=True), key, end) for key in (*add, *subtract)}
    if any(figure.value < 0 for figure in components.values()):
        raise ValueError("Net debt bridge needs unsigned balances")
    subtotal = sum(components[key].value for key in add) - sum(components[key].value for key in subtract)
    amounts = [decimal_string(value) / MILLION for value in [assumed["default"], *assumed["alternatives"]]]
    if any(value < 0 for value in amounts) or not assumed["reason"].strip() or len(set(amounts)) != len(amounts):
        raise ValueError("Supply distinct, nonnegative borrowing assumptions and a reason")
    source = source_from_json(assumed["source"])
    return combined(subtotal, *components.values()), {
        "label": escape(record["label"]),
        "value": amounts[0],
        "alternatives": amounts[1:],
        "reason": escape(assumed["reason"]),
        "source": source.link("Filing disclosure"),
        "components": [
            [f'{sign} {escape(inputs.records[key, None, end]["label"])}', render_figure(components[key], "")]
            for keys, sign in ((add, "Add"), (subtract, "Subtract")) for key in keys
        ],
    }


def revenue_components(inputs, year, metrics, total_revenue):
    if (not isinstance(metrics, list) or len(metrics) < 2 or len(set(metrics)) != len(metrics)
            or any(not re.fullmatch(r"[a-z][a-z0-9_]*", metric) for metric in metrics)):
        raise ValueError("Identify at least two distinct revenue components")
    period = inputs.periods[str(year)]
    components = {metric: required(inputs.annual(year, metric), metric, period["end"]) for metric in metrics}
    if any(figure.value <= 0 for figure in components.values()) or sum(figure.value for figure in components.values()) != total_revenue:
        raise ValueError("Positive revenue components must reconcile to total reported revenue")
    return {metric: {
        "label": escape(inputs.records[metric, period["start"], period["end"]]["label"]),
        "value": figure.value, "link": render_figure(figure, ""),
    } for metric, figure in components.items()}


def finite_rate(number):
    return isinstance(number, (int, Decimal)) and not isinstance(number, bool) and Decimal(number).is_finite()


def validate_scenarios(scenarios, components):
    if set(scenarios) != {"Bear", "Base", "Bull"}:
        raise ValueError("Supply Bear, Base and Bull scenarios")
    rate_fields = {"fixedCostGrowth", "variableShare", "tax", "daShare", "capexShare", "workingCapital",
                   "wacc", "terminalGrowth", "terminalRoic"}
    driver_fields = {"volumeGrowth", "variableCostGrowth"}
    revenue_field = "revenueGrowth" if components else "priceGrowth"

    for name, scenario in scenarios.items():
        if set(scenario) != rate_fields | driver_fields | {revenue_field}:
            raise ValueError(f"Invalid fields in {name} assumptions")
        if any(not finite_rate(scenario[field]) for field in rate_fields):
            raise ValueError(f"Non-numeric rates in {name} assumptions")
        annual_rates = {field: scenario[field] for field in driver_fields}
        if components:
            growth = scenario[revenue_field]
            if set(growth) != set(components):
                raise ValueError(f"{name} growth rates must match the revenue components")
            annual_rates.update(growth)
        else:
            annual_rates[revenue_field] = scenario[revenue_field]
        for field, values in annual_rates.items():
            if not isinstance(values, list) or len(values) != 5 or any(not finite_rate(value) for value in values):
                raise ValueError(f"{name} {field} needs five numeric rates")


def forecast_axes(config, components):
    component = config.get("revenue_component")
    if component is not None and component not in components:
        raise ValueError("The growth axis must name a modeled revenue component")
    horizons = config["horizons"]
    if (not isinstance(horizons, list) or not horizons
            or any(type(horizon) is not int or not 1 <= horizon <= 30 for horizon in horizons)
            or horizons != sorted(set(horizons))):
        raise ValueError("Forecast horizons must be distinct increasing whole years from 1 to 30")
    return component, horizons


def growth_margin_data(inputs, year, components, config):
    if set(config) - {"horizons", "revenue_component", "growth_rates", "margins"}:
        raise ValueError("Unknown growth and margin map setting")
    component, horizons = forecast_axes(config, components)
    growth_rates, margins = config["growth_rates"], config["margins"]
    for values in (growth_rates, margins):
        if (not isinstance(values, list) or not 2 <= len(values) <= 15
                or any(not finite_rate(value) for value in values) or values != sorted(set(values))):
            raise ValueError("Supply 2 to 15 distinct increasing numeric values for each map axis")
    if growth_rates[0] <= -100 or margins[0] < 0 or margins[-1] > 100:
        raise ValueError("Map growth must exceed −100% and sustainable margins must be from 0% to 100%")

    history = []
    metric = component or "revenue"
    for label, period in sorted(inputs.periods.items()):
        fiscal_year = int(label)
        if fiscal_year > year or period["end"] > inputs.periods[str(year)]["end"]:
            continue
        sales = inputs.annual(label, metric)
        revenue = inputs.annual(label, "revenue")
        operating_profit = inputs.annual(label, "operating_profit")
        previous = inputs.periods.get(str(fiscal_year - 1))
        prior = None
        if previous and date.fromisoformat(previous["end"]) + timedelta(days=1) == date.fromisoformat(period["start"]):
            prior = inputs.annual(fiscal_year - 1, metric)
        growth = ratio(sales, prior) if sales and prior else None
        margin = ratio(operating_profit, revenue) if operating_profit and revenue else None
        days = (date.fromisoformat(period["end"]) - date.fromisoformat(period["start"])).days + 1
        length = f"{days // 7} weeks" if days % 7 == 0 else f"{days} days"
        history.append([
            f"FY{label}", period["end"], length, render_figure(sales, ""),
            render_figure(revenue, "") if component else None, render_figure(operating_profit, ""),
            render_figure(combined(growth.value - 1, growth), "%", 2) if growth else "N/A",
            render_figure(margin, "%", 2) if margin else "N/A",
        ])
    return {
        "revenueComponent": component,
        "label": components[component]["label"] if component else "Consolidated revenue",
        "horizons": horizons, "growthRates": growth_rates, "margins": margins, "history": history,
    }


def location_growth_data(inputs, year, components, config):
    fields = {"horizons", "revenue_component", "growth_rates", "location_label", "assumptions",
              "assumption_note", "assumption_references"}
    if set(config) - fields:
        raise ValueError("Unknown location growth setting")
    component, horizons = forecast_axes(config, components)
    growth_rates = config["growth_rates"]
    if (not isinstance(growth_rates, list) or not 1 <= len(growth_rates) <= 15
            or any(not finite_rate(rate) or rate <= -100 for rate in growth_rates)
            or growth_rates != sorted(set(growth_rates))):
        raise ValueError("Supply 1 to 15 distinct increasing location sales growth rates above −100%")
    assumptions = config["assumptions"]
    rates = {"sameSiteGrowth", "capexInflation", "referenceMargin"}
    costs = {"openingCost", "otherCapex"}
    if set(assumptions) != rates | costs | {"ramp"} or any(not finite_rate(assumptions[key]) for key in rates):
        raise ValueError("Supply the location sales, capex, and margin assumptions")
    amounts = {key: decimal_string(assumptions[key]) / MILLION for key in sorted(costs)}
    if (min(amounts.values()) < 0 or min(assumptions["sameSiteGrowth"], assumptions["capexInflation"]) <= -100
            or not 0 <= assumptions["referenceMargin"] <= 100):
        raise ValueError("Use nonnegative capex, rates above −100%, and a reference margin from 0% to 100%")
    ramp = assumptions["ramp"]
    if (not isinstance(ramp, list) or not ramp or any(not finite_rate(rate) or rate <= 0 for rate in ramp)
            or ramp != sorted(ramp) or ramp[-1] != 100):
        raise ValueError("Use positive nondecreasing location sales ramp percentages ending at 100%")

    end = inputs.periods[str(year)]["end"]
    history, counts, openings = [], {}, {}
    for label, period in sorted(inputs.periods.items()):
        fiscal_year = int(label)
        if fiscal_year > year or period["end"] > end:
            continue
        count = inputs.annual(label, "location_count", balance=True, unit="locations")
        additions = inputs.annual(label, "net_location_openings", unit="locations")
        capex = inputs.annual(label, "capex")
        if count is not None:
            if count.value != count.value.to_integral_value() or count.value <= 0:
                raise ValueError(f"FY{label} needs a positive whole end-of-year location count")
            counts[fiscal_year] = int(count.value)
        if additions is not None:
            if additions.value != additions.value.to_integral_value():
                raise ValueError(f"FY{label} needs a whole net opening count")
            openings[fiscal_year] = int(additions.value)
        if capex is not None and capex.value > 0:
            raise ValueError(f"FY{label} cash capex must be a signed outflow")
        spending = combined(-capex.value, capex) if capex is not None else None
        if fiscal_year == year:
            base_capex = required(spending, "cash capex", end)
        history.append([f"FY{label}", render_figure(count, ""), render_figure(additions, ""),
                        render_figure(spending, "")])

    if year not in counts or year not in openings or openings[year] < 0:
        raise ValueError("The location forecast needs accepted location and net opening counts for the annual base")
    recent = []
    for fiscal_year in range(year, year - len(inputs.periods), -1):
        if fiscal_year not in openings or openings[fiscal_year] < 0:
            break
        period = inputs.periods.get(str(fiscal_year))
        previous = inputs.periods.get(str(fiscal_year - 1))
        consecutive = previous and date.fromisoformat(previous["end"]) + timedelta(days=1) == date.fromisoformat(period["start"])
        if consecutive and fiscal_year in counts and fiscal_year - 1 in counts:
            if counts[fiscal_year] - counts[fiscal_year - 1] != openings[fiscal_year]:
                raise ValueError(f"FY{fiscal_year} net openings must reconcile consecutive year-end location counts")
        recent.append(openings[fiscal_year])
        if not consecutive:
            break
    starting_sites = counts[year]
    starting_year_sites = starting_sites - openings[year]
    if starting_year_sites <= 0 or sum(recent) > starting_sites or len(ramp) > len(recent) + 1:
        raise ValueError("The sales ramp needs one nonnegative consecutive opening cohort per step before 100%")

    references = [source_from_json(item) for item in config.get("assumption_references", [])]
    return {
        "revenueComponent": component,
        "salesLabel": components[component]["label"] if component else "Consolidated revenue",
        "locationLabel": escape(config.get("location_label", "Locations")),
        "horizons": horizons, "growthRates": growth_rates,
        "startingSites": starting_sites, "startingYearSites": starting_year_sites,
        "startingOpenings": openings[year], "recentOpenings": recent,
        "reportedCapex": base_capex.value, "capexLink": render_figure(base_capex, ""),
        "history": history, "assumptions": {**assumptions, **amounts},
        "note": escape(config.get("assumption_note", "")),
        "references": [source.link(source.title) for source in references],
    }


def valuation_data(inputs, facts=None):
    settings = inputs.payload.get("valuation")
    if settings is None:
        raise ValueError("Supply a dated quote, an equity bridge and editable nonfinancial DCF assumptions")
    if settings["model"] != "nonfinancial-fcff":
        raise ValueError("The packaged DCF supports nonfinancial operating companies")
    year = int(settings["fiscal_year"])
    end = inputs.periods[str(year)]["end"]
    quote = settings["quote"]
    quote_date = iso_date(quote["date"])
    share_date = iso_date(settings.get("share_date", end))
    if not end <= quote_date or not share_date <= quote_date:
        raise ValueError("The quote date must be on or after the financial period and share-count dates")

    facts = annual_facts(inputs) if facts is None else facts
    figures = calculate(facts, year)
    anchors = {key: figures[metric] for key, metric in LTM_METRICS.items() if figures.get(metric) is not None}
    for key in ("revenue", "operatingProfit"):
        required(anchors.get(key), key, end)
    explicit_debt = ("valuation_net_debt", None, end) in inputs.records
    bridge = settings.get("net_debt_bridge")
    debt_assumption = None
    if bridge is not None:
        if explicit_debt or "interim" in settings:
            raise ValueError("The reported net debt bridge applies to a single annual base")
        net_debt, debt_assumption = net_debt_bridge(inputs, year, bridge)
    else:
        net_debt = inputs.annual(year, "valuation_net_debt", balance=True) if explicit_debt else figures["net_debt"]
    anchors["netDebt"] = required(net_debt, "net debt", end)
    for metric in ("noncontrolling", "investments"):
        anchors[metric] = required(inputs.annual(year, metric, balance=True), metric, end)
    anchors["shares"] = required(inputs.figure("shares_outstanding", None, share_date, "shares"),
                                 "shares_outstanding", share_date)
    anchors["price"] = required(inputs.figure("market_price", None, quote_date, f"{inputs.currency}/share"),
                                "market_price", quote_date)
    lease_debt = inputs.annual(year, "operatingLeaseDebt", balance=True)
    if ("operatingLeaseDebt", None, end) in inputs.records and not explicit_debt:
        lease_debt = required(lease_debt, "operatingLeaseDebt", end)
        anchors["operatingLeaseDebt"] = lease_debt
        if bridge is None:
            anchors["netDebt"] = combined(net_debt.value - lease_debt.value, net_debt, lease_debt)
    if any(anchors[key].value <= 0 for key in ("revenue", "shares", "price")):
        raise ValueError("The DCF requires positive revenue, shares outstanding and share price")
    components = {}
    if "revenue_components" in settings:
        if "interim" in settings:
            raise ValueError("Revenue components apply to a single annual base")
        components = revenue_components(inputs, year, settings["revenue_components"], anchors["revenue"].value)
    validate_scenarios(settings["scenarios"], components)

    issuer = inputs.issuer
    data = {
        "name": inputs.company,
        **{field: escape(issuer.get(field, "")) for field in ("market", "ticker", "accounting", "currency")},
        **{metric: figure.value for metric, figure in anchors.items()},
        "fiscalYear": year, "periodLabel": f"FY{year}", "balanceDate": end, "shareDate": share_date,
        "priceDate": quote_date, "priceMarket": escape(quote["market"]), "scenarios": settings["scenarios"],
        "links": linked_figures(anchors), "assumption_note": settings.get("assumption_note", ""),
    }
    if components:
        data["revenueComponents"] = components
    if "growth_margin_map" in settings:
        data["growthMarginMap"] = growth_margin_data(inputs, year, components, settings["growth_margin_map"])
    if "location_growth" in settings:
        data["locationGrowth"] = location_growth_data(inputs, year, components, settings["location_growth"])
    if debt_assumption is not None:
        data["debtAssumption"] = debt_assumption
    references = [source_from_json(item) for item in settings.get("assumption_references", [])]
    data["assumption_references"] = [source.link(source.title) for source in references]
    adapter = settings.get("adapter")
    if adapter not in (None, "mosaic", "phosagro") or (adapter is not None and issuer.get("id") != adapter):
        raise ValueError("The normalization and liquidity adapter must match the issuer")
    if "interim" in settings:
        ltm, interim = interim_snapshot(inputs, anchors, year, settings["interim"], quote_date)
        data["ltm"] = ltm
    elif adapter is not None:
        raise ValueError("The selected pilot adapter requires comparable interim figures")
    if adapter == "mosaic":
        data["normalization"] = mosaic_normalization(issuer, facts, year, interim, ltm)
        data["liquidity"] = mosaic_liquidity(issuer, year, interim)
    elif adapter == "phosagro":
        data["normalization"] = phosagro_normalization(issuer, facts, year, interim)
        data["liquidity"] = phosagro_liquidity(interim, quote_date)
    return {adapter or "company": data}


def render_valuation(data):
    script = (HERE / "valuation.js").read_text(encoding="utf-8")
    payload = json.dumps(data, default=float).replace("<", "\\u003c")
    companies = "".join(f'<option value="{escape(key)}">{escape(item["name"])}</option>' for key, item in data.items())
    first = next(iter(data.values()))
    title = escape(first["name"])
    assumption_note = "".join(f"<p>{escape(paragraph)}</p>" for paragraph in first["assumption_note"].split("\n\n")
                              if paragraph)
    references = " · ".join(first["assumption_references"])
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title} valuation</title>
<style>
:root {{ color-scheme: light; font: 16px/1.55 system-ui, sans-serif; color: #142724; background: #f3f6f4; }}
body {{ margin: 0; }}
main {{ max-width: 76rem; margin: auto; padding: clamp(1rem, 4vw, 3rem); }}
h1 {{ font-size: clamp(2rem, 5vw, 3.2rem); letter-spacing: -.04em; line-height: 1.1; margin: 0; }}
h2 {{ font-size: 1.3rem; }}
p {{ margin: .6rem 0; max-width: 85ch; }}
a {{ color: #145749; text-underline-offset: .17em; }}
header {{ padding-bottom: 1rem; border-bottom: 2px solid #16463c; }}
.selects {{ display: flex; flex-wrap: wrap; align-items: end; gap: .75rem; margin: 1rem 0; }}
.selects label, .rates label {{ display: grid; gap: .25rem; align-content: space-between; }}
input, select, button {{
  box-sizing: border-box; min-height: 2.5rem; font: inherit; color: inherit;
  background: white; border: 1px solid #8caaa0; border-radius: .35rem; padding: .35rem .65rem;
}}
input {{ width: min(100%, 6rem); font-variant-numeric: tabular-nums; }}
.checkbox {{ width: auto; min-height: auto; }}
button {{ cursor: pointer; }}
:focus-visible {{ outline: 2px solid #176758; outline-offset: 2px; }}
.rates {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(10rem, 1fr)); gap: 1rem; margin: 1rem 0; }}
.rates label {{ font-size: .9rem; }}
.scroll {{ overflow-x: auto; border: 1px solid #d8e4de; border-radius: .7rem; background: white; margin: 1rem 0; }}
.scroll:empty {{ display: none; }}
table {{ width: 100%; border-collapse: collapse; font-variant-numeric: tabular-nums; }}
caption {{ text-align: left; padding: .65rem .9rem; background: #16463c; color: white; font-weight: 650; }}
th, td {{ text-align: right; padding: .55rem .75rem; border-bottom: 1px solid #dfe8e3; white-space: nowrap; }}
th:first-child {{ text-align: left; }}
.explanation {{ text-align: left; white-space: normal; min-width: 18rem; }}
thead {{ background: #e8f0ec; }}
#recovery-comparison thead th, #phosagro-funding-comparison thead th, #phosagro-recovery-comparison thead th,
#phosagro-break-even-thresholds thead th {{
  white-space: normal; min-width: 6rem;
}}
.map-maturities {{ white-space: normal; min-width: 12rem; margin-top: .25rem; font-size: .87rem; }}
.map-maturities div + div {{ margin-top: .2rem; }}
.map-both {{ background: #d0ecdb; color: #10452c; font-weight: 700; }}
.map-price {{ background: #fff0cc; color: #5e410c; }}
.map-funded {{ background: #e1eef7; color: #1d4560; }}
.map-neither {{ color: #51685f; }}
.map-meets-price {{ background: #d0ecdb; color: #10452c; font-weight: 650; }}
.map-axes label {{ flex: 1 1 16rem; }}
.map-axes input {{ width: 100%; }}
tr:last-child > * {{ border-bottom: 0; }}
.source {{ background: #edf5f0; padding: .08em .3em; border-radius: .25em; }}
.model-note, details {{ padding: .85rem 1.15rem; background: #e8f0ec; border-left: 3px solid #44776b; }}
#status, #map-status, #location-status {{ color: #8b391c; font-weight: 600; }}
#status:empty, #map-status:empty, #location-status:empty {{ display: none; }}
#location-details details {{ margin-block: .65rem; }}
summary {{ cursor: pointer; font-weight: 650; }}
</style>
</head>
<body><main>
<header>
<h1>{title} equity valuation</h1>
<div id="source-data"></div>
</header>
<p class="model-note">Year 1 starts at the dated quote using the selected financial inputs as its starting run rate. The period comparison keeps the same quote and forecast assumptions. Assumptions can be edited and reset on reload.</p>
<div class="selects">
<label>Company <select id="issuer">{companies}</select></label>
<label>Financial inputs <select id="period"><option id="annual-period" value="annual">Annual</option><option id="ltm-period" value="ltm" selected>Trailing 12 months</option><option value="normalized" id="normalized-period" hidden disabled>Normalized</option></select></label>
<label>Case to edit <select id="scenario"><option>Bear</option><option selected>Base</option><option>Bull</option></select></label>
<button id="reset" type="button">Reset selected case</button>
</div>
<div id="period-comparison" class="scroll" role="region" aria-label="Effect of updated financials" tabindex="0"></div>
<div id="cases" class="scroll" role="region" aria-label="Scenario values" tabindex="0"></div>
<section id="phosagro-normalization" hidden aria-labelledby="phosagro-normalization-heading">
<h2 id="phosagro-normalization-heading">PhosAgro earnings normalization</h2>
<div id="phosagro-normalization-bridge" class="scroll" role="region" aria-label="Reported and FX-neutral consolidated operating profit" tabindex="0"></div>
<p>The bridge sets the reported operating FX gain or loss to zero. Financing FX sits below operating profit. The model retains receivables revaluations tied to fertilizer price and the reported cost of raw materials. The FY2025 customs credit related to 2024 contracts is shown for context. The bridge keeps the small net trailing customs expense as reported.</p>
<div id="phosagro-cycle-controls" class="scroll" role="region" aria-label="Editable PhosAgro mid-cycle assumptions" tabindex="0"></div>
<div id="phosagro-cycle-results" class="scroll" role="region" aria-label="PhosAgro mid-cycle operating profit and margin" tabindex="0"></div>
<p>Price and volume changes apply to trailing phosphate and nitrogen product sales. Price retention is the share of extra or lost sales that reaches operating profit. Volume changes use the trailing consolidated gross margin as a contribution proxy. The two raw-material cost changes are per unit of output at the assumed shipment volume. The DCF applies the resulting margin to reported trailing revenue.</p>
<details>
<summary>Historical sales, raw-material costs and cash conversion</summary>
<div id="phosagro-cycle-history" class="scroll" role="region" aria-label="Consolidated filing figures and shipment history" tabindex="0"></div>
<p>Product revenue per shipped tonne is a price and mix indicator based on reported product revenue and fertilizer and feed-phosphate shipments. The numerator and denominator have different product coverage. The interim IFRS filing provides consolidated product sales without shipment tonnage. The audit and interim review reports qualify their opinions or conclusions for omitted IFRS 8 operating segment disclosures.</p>
<p>Bear holds sulphur costs at the trailing level with weaker prices and shipments. Base allows partial sulphur relief and modest volume growth. Bull combines larger cost relief with stronger sales. Historical costs reflect purchase prices, sourcing and quantities. The forecasts include no release of accumulated receivables. Working capital and capex are editable in Forecast assumptions. The cash-after-capex history excludes separately reported capitalized interest and right-of-use advances.</p>
</details>
</section>
<section id="phosagro-funding" hidden aria-labelledby="phosagro-funding-heading">
<h2 id="phosagro-funding-heading">PhosAgro liquidity and refinancing</h2>
<div class="selects"><label>Financing case
<select id="phosagro-financing-case"><option value="none">No refinancing</option><option value="partial">Partial rollover</option></select>
</label></div>
<div id="phosagro-funding-settings"></div>
<p id="phosagro-funding-status" role="status"></p>
<div id="phosagro-funding-comparison" class="scroll" role="region" aria-label="DCF and funding by operating case" tabindex="0"></div>
<p>Minimum liquidity is the lowest modeled month-end cash balance. The filings describe credit lines without a verified available commitment, so modeled credit-line capacity is zero. Replacement debt is borrowing assumed under the rollover case. The first gap shows the month and size of the initial cash shortfall.</p>
<details>
<summary>Funding cash flows for the selected operating case</summary>
<div id="phosagro-funding-detail" class="scroll" role="region" aria-label="Annual funding cash flow" tabindex="0"></div>
</details>
<h3>PhosAgro recovery paths</h3>
<p id="phosagro-recovery-basis"></p>
<div class="selects" id="phosagro-recovery-settings"></div>
<p id="phosagro-recovery-status" role="status"></p>
<div id="phosagro-recovery-comparison" class="scroll" role="region" aria-label="Recovery path DCF, liquidity and funding gaps" tabindex="0"></div>
<details>
<summary>Cash flows for the selected recovery path</summary>
<div class="selects"><label>Recovery path <select id="phosagro-recovery-selection"></select></label></div>
<div id="phosagro-recovery-forecast" class="scroll" role="region" aria-label="Annual recovery cash flows" tabindex="0"></div>
<div id="phosagro-recovery-funding-detail" class="scroll" role="region" aria-label="Annual funding cash flows under the selected recovery path" tabindex="0"></div>
<p>Each path closes the gap between FX-neutral trailing operating profit and the selected normalized case. Even closes the gap in equal steps. Front-loaded uses the square root of elapsed time as a share of the recovery period. Back-loaded squares that share. The revenue basis adjustment closes evenly. Revenue and reinvestment follow the same forecast in all paths. The funding check spreads each path’s annual unlevered cash flow over the 12 months from the September balance sheet.</p>
</details>
<section aria-labelledby="phosagro-break-even-heading">
<h3 id="phosagro-break-even-heading">PhosAgro break-even map</h3>
<p id="phosagro-break-even-basis"></p>
<div id="phosagro-break-even-thresholds" class="scroll" role="region" aria-label="Valuation and funding thresholds across all four paths" tabindex="0"></div>
<p>The quote-equivalent margin is the normalized starting margin that makes the DCF equal the close. A required sulphur reduction above 100% is a theoretical equivalent. A 100% reduction represents zero modeled sulphur cost. For funding at the price threshold, the calculation uses 0% relief if that already clears the quote. Each binding month is the month that limits the rollover, with any loan and bond maturities shown at their modeled ruble principal.</p>
<div class="selects"><label>Map recovery path <select id="phosagro-break-even-path"></select></label></div>
<div id="phosagro-break-even-settings" class="rates">
<label>Minimum sulphur relief (%) <input type="number" min="0" max="100" step="any" value="0" data-map="reliefMin"></label>
<label>Maximum sulphur relief (%) <input type="number" min="0" max="100" step="any" value="100" data-map="reliefMax"></label>
<label>Minimum rollover (%) <input type="number" min="0" max="100" step="any" value="0" data-map="rolloverMin"></label>
<label>Maximum rollover (%) <input type="number" min="0" max="100" step="any" value="100" data-map="rolloverMax"></label>
</div>
<p id="phosagro-break-even-status" role="status"></p>
<div id="phosagro-break-even-grid" class="scroll" role="region" aria-label="Sulphur relief and rollover grid for the selected recovery path" tabindex="0"></div>
<p>Rows vary sulphur costs per unit relative to the trailing period. Columns vary the share of eligible principal rolled. Both means the DCF reaches the dated close and month-end cash stays nonnegative through Year 5. The grid samples each axis at 11 evenly spaced points. The thresholds above use the full 0% to 100% range and unrounded monthly cash flows.</p>
</section>
<details>
<summary>Debt terms, funding movements and source links</summary>
<div id="phosagro-funding-evidence" class="scroll" role="region" aria-label="Reported liquidity and balance-sheet inputs" tabindex="0"></div>
<div id="phosagro-funding-events" class="scroll" role="region" aria-label="Debt movements through the quote date" tabindex="0"></div>
<div id="phosagro-funding-debt" class="scroll" role="region" aria-label="Currency, coupon, put and maturity schedule" tabindex="0"></div>
<p>The forecast spreads each year's operating cash flow across the 12 months from the September balance sheet. The cash schedule includes the disclosed October financing and November bond repurchase. The October 9 issue is included in the interim note's borrowing total. The October 23 issues follow the October 21 authorization. Their proceeds use issue-date FX rates and assume the foreign-currency cash is available at its ruble equivalent. The model uses disclosed financing movements and estimates operating cash after September.</p>
<p>October repayments excluding the related-party round trip reduce current bank loans pro rata. Loan currency allocations use the residual of disclosed currency totals and bond balances. Bank principal paid within the first year and the unallocated borrowing have editable payment months. The small noncurrent bank balance uses a September 2028 payment proxy. The September bond balances are carrying-value proxies converted to denomination currency at September exchange rates. The November put uses the actual principal repurchased. The June 2028 put exercises the selected share and the rest reaches legal maturity. The reset coupon is carried forward for any remaining balance.</p>
<p>After November, the FX shock changes ruble equivalents of USD, CNY and EUR debt service from the valuation-day rates. The key-rate input changes linked coupons, while bank borrowing, unallocated financing and replacement debt have separate interest assumptions. The model spreads interest evenly and includes the September accrued interest as an October payment. Current lease principal runs off over Year 1 and the remaining balance over Years 2–5, with interest scaled from the nine-month disclosure. The operating forecast carries its existing tax assumptions.</p>
<p>Rollover applies to future loan and bond principal at maturity or modeled put exercise. The replacement balance stays outstanding through Year 5. The funding check assumes month-end settlement, continues any deficit without pricing the missing funding, and excludes undisclosed financing movements, refinancing fees, credit covenant testing and operating FX effects. Annual dividends are editable and default to zero.</p>
</details>
</section>
<section id="recovery" hidden aria-labelledby="recovery-heading">
<h2 id="recovery-heading">Mosaic recovery paths</h2>
<p id="recovery-summary" aria-live="polite"></p>
<div class="selects" id="recovery-settings"></div>
<p id="recovery-basis"></p>
<h3>Liquidity and refinancing</h3>
<div class="selects">
<label>Financing case <select id="financing-case"><option value="none">No refinancing</option><option value="custom">Custom refinancing</option></select></label>
</div>
<div id="liquidity-settings"></div>
<p id="liquidity-status" role="status"></p>
<div id="recovery-comparison" class="scroll" role="region" aria-label="Recovery timing and cash shortfall comparison" tabindex="0"></div>
<p>The cash shortfall is the undiscounted operating cash deficit before the first positive free cash flow year, or through Year 5 if every year is nonpositive. It uses unlevered cash flow. Opening cash, financing and settlements of prior exit charges are outside this calculation.</p>
<p>The liquidity check starts from the latest modeled balance sheet. Minimum liquidity is the lowest month-end cash plus undrawn usable credit. Peak draw is the largest outstanding revolver balance. A funding gap appears when cash and usable credit fall short of the month's obligations.</p>
<details>
<summary>Annual cash flow and recovery assumptions</summary>
<div class="selects"><label>Recovery path <select id="recovery-selection"></select></label></div>
<div id="recovery-forecast" class="scroll" role="region" aria-label="Year-by-year recovery forecast" tabindex="0"></div>
<div id="liquidity-forecast" class="scroll" role="region" aria-label="Funding schedule using the selected recovery path" tabindex="0"></div>
<p>The starting benchmark uses the latest nine-month selling prices and input costs, and trailing shipments calculated from the filing history in Normalized. The model estimates the operating profit gap for each driver using the same benchmark as the mid-cycle cases. Other operating effects reconcile those estimates to adjusted trailing profit before care costs.</p>
<p>The four paths share the normalized revenue, reinvestment and terminal cash flows. Each year, the open profit gaps scale with forecast revenue. An even transition closes the selected gap in equal steps. Front-loaded uses the square root of elapsed time as a share of the recovery period. Back-loaded squares that share. Other operating effects follow an even transition.</p>
<p>The care and maintenance assumption applies through the selected year in all four paths, including Immediate. The filing gives a historical cost. The amount and exit year are forecast assumptions. The model compares unrounded values with the dated close.</p>
</details>
<details>
<summary>Liquidity inputs and filing links</summary>
<div id="liquidity-evidence" class="scroll" role="region" aria-label="Reported cash, credit and financing figures" tabindex="0"></div>
<div id="liquidity-debt" class="scroll" role="region" aria-label="Debt maturity dates and amounts" tabindex="0"></div>
<p id="liquidity-schedule-note"></p>
<p>Each 12-month recovery forecast is spread evenly from the September balance sheet date. The model schedules named maturities by month, spreads reported current long-term debt maturities over the first 12 months, and then uses the FY2025 residual schedule after removing the bonds and term loans listed there. This estimates smaller maturities through 2030. The latest filing gives the current maturities as one amount.</p>
<p>No refinancing repays short-term debt and structured supplier financing by March 2027 and repays the revolver balance at expiry in May 2030. Existing committed credit may be drawn before expiry. The custom case uses the selected rollover and replacement shares. Rolled short-term balances and replacement notes are assumed to remain outstanding through Year 5.</p>
<p>Cash interest uses opening monthly debt balances and editable rates. Unlevered operating taxes carry through without an interest tax shield. Dividends use the recent cash payments annualized by default and are paid quarterly. The December 2026 exit payment is an editable proxy based on the disclosed termination and severance charges. Care and maintenance is already in the recovery cash flows.</p>
<p>The check assumes access to the selected share of committed capacity while covenants are met. Seasonality, exact payment days, proceeds from asset sales, uncommitted credit, and refinancing fees are outside the calculation. Commercial paper and the facility share one limit. After a funding gap, the model carries the deficit forward without assigning a financing cost to it.</p>
</details>
</section>
<section id="normalization" hidden aria-label="Mosaic earnings normalization">
<h2>Mosaic earnings normalization</h2>
<p>The bridge reverses the reported asset-sale line, specific exit charges and the goodwill write-down. The mid-cycle cases then estimate operating margin from prices, shipments and input costs.</p>
<div id="normalization-bridge" class="scroll" role="region" aria-label="Operating profit adjustments and filing sources" tabindex="0"></div>
<p id="normalization-scope"></p>
<details open>
<summary>Historical drivers and editable mid-cycle cases</summary>
<div id="cycle-controls" class="scroll" role="region" aria-label="Filing history and editable case inputs" tabindex="0"></div>
<div id="cycle-reference" class="scroll" role="region" aria-label="Mid-cycle benchmark exposures" tabindex="0"></div>
<p>The FY2025 benchmark starts with consolidated operating earnings after reversing the net disposal loss and goodwill impairment. Each segment’s external sales are repriced and scaled with shipment assumptions. The model retains the stated share of the price change in operating profit and applies FY2025 segment gross margins to the revenue change from shipments. Potash price retention allows for resource taxes and royalties. Brazil’s allows for higher costs of purchased fertilizer for resale.</p>
<p>Ammonia and sulfur cost changes use fixed FY2025 quantities as exposure proxies, including North American sulfur purchases. Other costs and corporate sales stay at the benchmark. Historical segment price and shipment series are proxies for external sales. Some shipments include intersegment sales. Nine-month volumes are shown as filed and help assess the pace of shipments.</p>
<p>Bear assumes weaker volumes and more persistent input pressure. Base assumes partial recovery, with input costs above FY2025. Bull assumes stronger pricing and cheaper inputs. Potash and Brazil shipments stay below FY2025 in all three defaults. The resulting margin is applied to trailing consolidated revenue for the DCF.</p>
<p>The five-year forecast starts from the estimated run rate immediately. The history covers three full fiscal years and the latest comparable nine-month periods.</p>
</details>
<div id="cycle-results" class="scroll" role="region" aria-label="Mid-cycle operating margin and profit by case" tabindex="0"></div>
</section>
<h2>Forecast assumptions</h2>
{assumption_note}
{f'<p aria-label="Sources for assumptions">{references}</p>' if references else ""}
<div id="assumptions"></div>
<div id="revenue-bridge" class="scroll" role="region" aria-label="Revenue components and consolidated growth" tabindex="0"></div>
<p id="status" role="status"></p>
<div id="forecast" class="scroll" role="region" aria-label="Forecast cash flows" tabindex="0"></div>
<div id="bridge" class="scroll" role="region" aria-label="Enterprise-to-equity bridge" tabindex="0"></div>
<section id="debt-analysis" hidden>
<h2>Unquantified borrowing sensitivity</h2>
<p id="debt-context"></p>
<label>Assumed borrowing in reporting-currency millions <input id="debt-assumption" type="number" min="0" step="1"></label>
<div id="debt-components" class="scroll" role="region" aria-label="Net debt components" tabindex="0"></div>
<div id="debt-sensitivity" class="scroll" role="region" aria-label="Per-share value at different assumed borrowings" tabindex="0"></div>
</section>
<h2>Market-implied margin</h2>
<p id="reverse"></p>
<div id="sensitivity" class="scroll" role="region" aria-label="Base case sensitivity" tabindex="0"></div>
<div id="margin-drivers" class="scroll" role="region" aria-label="Market-implied margin drivers" tabindex="0"></div>
<section id="growth-margin-map" aria-labelledby="growth-margin-heading" hidden>
<h2 id="growth-margin-heading">Market-implied growth and margin</h2>
<p id="growth-margin-context"></p>
<div class="selects map-axes">
<label>Annual growth rates (%)
<input id="map-growth-rates" type="text" spellcheck="false" aria-describedby="map-axis-format"></label>
<label>Sustainable operating margins (%)
<input id="map-margins" type="text" spellcheck="false" aria-describedby="map-axis-format"></label>
</div>
<p id="map-axis-format">Enter increasing percentages separated by commas. Highlighted values reach the dated close. The last column estimates the margin needed to reach it for each growth rate.</p>
<p id="map-status" role="status"></p>
<div id="growth-margin-grids"></div>
<div id="growth-margin-reference" class="scroll" role="region" aria-label="Base and Bull operating assumptions" tabindex="0"></div>
<div id="growth-margin-history" class="scroll" role="region" aria-label="Filing-linked growth and margin history" tabindex="0"></div>
<p>Historical growth uses reported fiscal totals. A missing comparison appears as N/A. Operating margin uses consolidated revenue in both history and forecast.</p>
</section>
<section id="location-growth" aria-labelledby="location-growth-heading" hidden>
<h2 id="location-growth-heading">Location growth and capital spending</h2>
<p id="location-context"></p>
<p id="location-note"></p>
<div id="location-history" class="scroll" role="region" aria-label="Filing-linked location and capital spending history" tabindex="0"></div>
<div id="location-controls"></div>
<p id="location-status" role="status"></p>
<p id="location-capex-check"></p>
<div id="location-results" class="scroll" role="region" aria-label="Location growth path comparison" tabindex="0"></div>
<div id="location-details"></div>
</section>
<details id="ltm-details">
<summary>Trailing-12-month calculation and filing links</summary>
<p>Trailing figures use the last full year plus current year-to-date minus the prior year-to-date comparative. Historical cash flow provides context. Forecast cash flow uses the editable operating and reinvestment assumptions.</p>
<div id="ltm-calculation" class="scroll" role="region" aria-label="Trailing-12-month calculation" tabindex="0"></div>
</details>
<details>
<summary>Model and source notes</summary>
<p>Revenue grows with selling price and volume or with separately modeled revenue components when supplied. Variable costs grow with volume and cost per unit. Fixed costs grow at their own rate. The starting variable cost share is an assumption.</p>
<p>Unlevered free cash flow equals after-tax operating profit plus D&amp;A less capex and the increase in operating working capital. Capex represents investment in operating assets, including replacements for capitalized leases where relevant. The model charges cash tax on positive operating profit and uses zero cash tax in loss years. It assumes zero cash value for any refunds and loss carryforwards.</p>
<p>Terminal reinvestment is terminal growth divided by terminal ROIC. Terminal free cash flow is after-tax operating profit after that reinvestment. The model divides that cash flow by WACC less growth, then discounts it back five years at the nominal WACC in each company’s currency. A terminal operating loss makes this calculation a stress result. A sustainable going-concern value requires a credible long-run cash flow.</p>
<p>Equity value subtracts valuation net debt and non-controlling interests, then adds investments outside the operating forecast. The bridge uses reported carrying amounts for investments and non-controlling interests. Any unquantified borrowing entered as an assumption appears separately. Capitalized leases are treated as debt when their costs are modeled as depreciation and financing.</p>
<p><a href="https://pages.stern.nyu.edu/~adamodar/New_Home_Page/valquestions/termvalueexreturns.htm">Terminal value and reinvestment</a></p>
</details>
<noscript>Enable JavaScript to edit assumptions and calculate the valuation.</noscript>
</main>
<script id="valuation-data" type="application/json">{payload}</script>
<script>{script}</script>
</body>
</html>
"""
