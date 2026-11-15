# AgroSim financial intelligence backend

Finance is optional and separate from agriculture. It reads existing harvest reports and does not change crop physiology, prices received, soil states, NASA forcing, crop support or prior benchmarks. No frontend or visual output is added.

The working Illinois reference evaluates **36 preserved physical scenarios × 6 financial scenarios =216 evaluations**. Its prices and production expenses are source-backed references adopted under explicit planning assumptions, not actual farm profits or future prices. The original physical model remains unvalidated for farmer recommendations.

## Run and reproduce

From the existing repository environment:

```sh
.venv/bin/python -m agrosim finance-evaluate \
  --config configs/finance_illinois.yaml \
  --output-dir /path/to/new/finance-results
```

The directory must not exist. Evaluation is offline; no API request or hidden refresh occurs. Python dependencies already used by AgroSim are retained; the optional `finance` dependency adds `pypdf>=6` for rebuilding source budget normalization. Normal financial evaluation itself reads JSON and does not need a PDF parser.

A second configuration demonstrates local price entry on the same actual simulated harvests:

```sh
.venv/bin/python -m agrosim finance-evaluate \
  --config configs/finance_farmer_entry_example.yaml \
  --output-dir /path/to/new/price-entry-example
```

**Those example sale prices are explicitly synthetic fixtures, not actual farmer-provided prices or local contracts.** To use real farmer entries, supply the actual fields and change their provenance to `farmer_supplied`. Unchanged fields retain their reference values and provenance.

Offline source normalization reproduces from the preserved small raw downloads:

```sh
.venv/bin/python scripts/prepare_finance_illinois.py \
  --raw-dir reports/finance-intelligence/raw \
  --output-dir /path/to/new/normalized-data

.venv/bin/python scripts/finance_examples.py /path/to/new/synthetic-examples
.venv/bin/python -m pytest tests/test_finance.py -q
```

The self-contained synthetic example proves that higher dry mass need not mean higher profit and that an entered price can reverse the financial preference. It never claims the fixtures are observed market values.

## Sources and what actually works

| Source | Implemented and recovered | Scope and gaps |
|---|---|---|
| NASS Quick Stats |243 monthly producer-price records,81 per product,2020–September2026; original JSON/query/source hash preserved | Corn and soybean state averages are Illinois. Spring wheat excluding durum is North Dakota, an explicitly transferred regional proxy. No exact field grade, local contract or production/demand estimate. Latest `load_time` is conservative current-version availability, not an invented original publication date. |
| ERS Commodity Costs and Returns | Three2025 regional accounts normalized with detailed-item reconciliation | Corn/soybean Heartland accounts; wheat Northern Great Plains mixed wheat account.2026 release/version adopted unchanged for future scenario costs. Marketing/storage are outside ERS crop account scope. Regional costs are not actual field costs. |
| Illinois farmdoc2027 budgets | Four Central Illinois high-productivity corn/soybean sequence budgets, source line items and published totals checked | Actual table columns are explicitly parsed. Prior-crop identity determines matched budget only when known. Illinois wheat budget is not silently applied to spring wheat. Budget depreciation is not a cash capital purchase. |
| AMS Market News | Credential-safe request adapter and explicit report/section/field-map normalization | No configured AMS key; no live price records retrieved. Integration remains partial. Report discovery and a report-specific mapping are required; wholesale/shipping-point/terminal quotes cannot silently become farmgate quotes. |
| Farmer inputs | Schemas, price field overrides, item-level cost overrides, contracts, capacities, delivery, fees, inventory and dated payments | No actual user farm quote, buyer or contract has been provided. The delivered examples are labeled synthetic or planning assumptions. |

Primary documentation was checked:

- [AMS API authentication](https://mymarketnews.ams.usda.gov/mymarketnews-api/authentication): HTTPS and API key as Basic-auth username with empty password. No anonymous prices are invented.
- [NASS Quick Stats API](https://quickstats.nass.usda.gov/api): key required, filtered queries, maximum50,000 returned rows. This pass retrieved only243 small price records.
- [ERS accounting definitions](https://www.ers.usda.gov/data-products/commodity-costs-and-returns/documentation): operating costs and allocated overhead differ from cash expenses; capital recovery includes capital cost; crop marketing/storage excluded. ERS crop revenue and secondary-product/straw values are not imported into AgroSim sales.
- [Illinois2027 budget source page](https://farmdoc.illinois.edu/handbook/2027-budgets-for-all-regions): crop system and regional projected accounts, not a crop-physiology calibration.
- [USDA trade bushel conventions](https://www.ams.usda.gov/sites/default/files/media/Book2.pdf) and [UF/IFAS sale-moisture conversion context](https://ask.ifas.ufl.edu/publication/AG442).

Public-source downloads and source definitions remain separate from farmer/planning assumptions. The raw acquisition manifest does not store API keys, authenticated request URLs or exception text containing credentials. No credentials or account settings are changed.

## Canonical schemas and APIs

`agrosim.finance.schema` provides validated dataclasses:

- `Farm`: country/state/county, explicit area/unit, currency/reference year, equipment, irrigation access, soil/production context and source.
- `Product`: crop versus saleable product, commodity class, harvest field, marketed fraction, sale moisture, grade, storage requirements and provenance for each conversion assumption.
- `Price`: quote type, geography, period, current-version availability/publication date, quality, currency/unit/basis, source record ID, fees and evidence origin.
- `CostItem`: crop, category and coverage, quantity/unit price/basis, incurrence/payment dates, cash/noncash/unknown class, accounting class, source and parent inclusion.
- `MarketAccess`: independently classified confirmed/observed activity/regional proxy/unknown, actual buyer identifier/location when supplied, compatible grade/class, delivery interval, volume cap, contract status and supported freight assumptions.

The calculation API is `agrosim.finance.accounting.evaluate_rotation`. It accepts schema objects, explicit harvests, sale plans, itemized costs, farm/horizon, scenarios and financial policies. `reconcile_costs` applies reference → explicit farmer override → additional expense precedence. `price_override` changes only supplied fields and retains the original reference quote in provenance. `market.usable_price` performs quote compatibility checks; `market.history` keeps incompatible series separate.

`finance.sources.fetch_json` is an **explicit online-only** helper, separate from evaluation. Callers supply an API key at runtime or use the existing private `fieldshift.acquisition.credential` loader. It returns `missing_credentials`, `unavailable`, `report_and_section_required` or a payload; network errors never create prices. `nass_prices`, `ers_costs` and `farmdoc_budgets` normalize supported source formats. `ams_prices` requires the exact selected report's field map and constants; it does not guess report fields, midpoints, units, grades or market types. An incompatible/new source schema fails visibly.

## Harvest-to-product accounting

The current AgroSim harvest key `grain_dry_kg_ha` is grain/seed **dry matter**, not total standing biomass. Only that explicit grain key is priced by the initial adapter. Root/shoot residue and ERS secondary products receive no sale value. Grain dry matter per hectare is multiplied by the explicitly configured area, then:

`marketable mass at sale moisture = grain dry mass × marketed recovery / (1 − sale moisture wet-basis)`.

The reference declares95% recovery as an illustrative assumption, not a measured cleaning/quality result. Adopted sale-moisture standards are corn15.5%, soybean13%, wheat13.5%. These are not observed moisture tests or guaranteed buyer specifications. Grade remains `unspecified` to match the source averages; a specific-grade quote must have an explicit matching product specification.

Trade weight per U.S. bushel is corn56lb; soybean/wheat60lb. One pound is0.45359237kg; one hectare is1/0.40468564224acres. USD/bu, USD/metric tonne, USD/lb and USD/kg normalize using compatible product weights. No universal bushel factor is used. Cross-currency conversion requires a matching from/to rate, decision date and source. No inferred FX rate is used.

A crop outside the executable profiles cannot acquire an AgroSim yield through finance. An explicitly separate `manual_yield_not_simulated` input format can carry a supplied grain-harvest scenario without falsely claiming simulation support. Its results are excluded from modeled-rotation recommendations. This first product adapter does not implement a generic fresh-vegetable, forage, quality-grading or repeated-harvest conversion.

## Prices, access and inventory

The reference explicitly adopts each latest available price observation as a constant **assumed future sale price**. Its observation stays preserved; the future scenario is not relabeled public observation. Observations unavailable at the decision or stale beyond the configured window cannot be silently adopted. Regional proxy use must be explicitly enabled.

A state price series is not a guaranteed commercial outlet. No actual buyer has been identified. The Illinois reference uses `regional_proxy` and explicitly assumes harvest sale/offtake. Hypothetical on-farm pickup and zero additional freight/handling/marketing fees are declared. Actual haul costs are not computed from invented distances. If a route is provided, distance × freight per tonne-km uses its explicit currency and source; a price netback already covering freight cannot also receive a duplicate freight charge. A wholesale/terminal/shipping-point quote requires a documented adjustment and currency before use as a netback price.

Lots are dated and product-specific. Buyer limits cap sales. Unsold production remains unsold. Explicit storage fraction, capacity and daily loss rate are required for carryover. Stored mass loses material at its declared rate and can later be sold; remaining stored inventory is not an immediate sale or cash receipt. Loss-adjusted kg-days support storage expenses. Product stock identity reconciles produced = sold + stored + unsold + losses.

The initial reference has no planned stored inventory. Published benchmark storage expenses remain declared reference enterprise costs, rather than a claim that the physical scenario stored grain. To replace them with actual storage costs, explicitly override the covered storage category; overlapping charges are rejected.

## Cost precedence and financial meaning

For a crop-year the runner chooses one compatible cost account: a known previous-crop farmdoc account when available, otherwise the matching ERS regional reference. No previous crop is invented in the first year. An unrepresented previous crop uses the explicitly reported generic fallback. Complete farmdoc and ERS budgets are never added together. Subtotals/totals validate details and are never counted again.

`covers` identifies cost coverage. A farmer replacement removes only fully covered reference items. Partial replacement of an aggregate, such as removing fuel from a combined fuel/utilities amount without its breakdown, is rejected. Capital recovery blocks overlapping depreciation/capital-interest additions; cash capital purchases are separately marked non-economic so they do not become both cash capex and economic depreciation. Items explicitly included in a parent total are not charged twice.

Economic costs and cash flows differ. Expense-incurrence dates drive economic NPV; actual/assumed payment dates drive cash. Noncash depreciation, owned-land opportunity costs and operating-capital opportunity cost are not cash payments. Unknown cash classification makes a complete cash balance unavailable. Loan draws/principal and owner contributions/withdrawals affect only cash; interest requires a separate explicit cost item. Future payments remain obligations; they are not hidden in horizon cash totals.

The reference's0.20USD/m³ incremental water price is explicitly illustrative, not an observed tariff. It replaces existing purchased-water charges; source energy remains in the benchmark fuel/electricity account. The simulator's preserved gross irrigation-supply quantity is allocated by actual annual net-applied fractions and scaled to farm area; no economic cost changes biological water use. Terrain costs, satellite proxies, soil carbon credits, subsidies, insurance indemnities and tax benefits are not fabricated.

**“Full economic costs” means all economic items within the supplied and disclosed account coverage.** It does not establish that an actual farm's full expenses are known. In particular, farmdoc's operator-and-land return does not independently price all operator labor. The output exposes this limitation in `cost_coverage_declaration`.

## Metrics and constraints

Outputs include sales revenue, variable costs, contribution margin, operating costs, return above operating and supplied full economic costs, annual/cumulative returns, per-ha/per-acre returns, dated economic/cash NPV, cash changes/balances/obligations, sold-stock break-even prices/yields, water-related expenses and returns relative to irrigation volume/cost.

Variable contribution includes variable production and marketing expenses. Operating-cost classification preserves declared/ERS operating categories; ownership/overhead costs remain separate. No claim is made that ERS operating cost equals cash expenditure.

Break-even selling price is total economic cost/sold mass. Break-even saleable sold quantity uses fixed-equivalent expenses divided by sale price less supported per-unit sales expenses. These are local algebraic calculations holding the declared cost structure fixed; they are not crop yield forecasts. Zero sales and nonpositive unit margins return unavailable break-even quantities. A different capacity/storage/cost regime requires rerunning.

Hard constraints cover cost budget, maximum financial exposure, irrigation availability/volume, crop set, equipment, confirmed contract requirement, storage capacity and minimum return. Violations prevent conditional rankings. A missing competitor is not quietly dropped to rank the rest. No farmer recommendation is emitted.

Each environmental history and financial scenario is compared separately under matched area/horizon/discount rate. The backend also reports named-downside order and financial water efficiency. Ecological stocks are displayed alongside returns; no universal sustainability score or unsupported carbon value is constructed.

## Risk, histories and limits

Reference/downside/upside plus separate price-down, expense-up and marketability-down sensitivities are explicitly hypothetical. Marketability sensitivity changes saleable recovery only, not crop growth. Joint downside combines price, expense and recovery stress without claiming independent risks or estimated likelihood. No loss probabilities or prediction intervals are produced.

Monthly historical mean, median, sample dispersion, coefficient of variation, seasonal-month summaries, recent/prior means and gaps are calculated within compatible product/class/geography/grade/quote/basis/currency/unit series. Duplicate identical periods are removed; conflicting duplicates require a version decision. Future-available observations are excluded. Current-version `load_time` limits historical reconstruction; this is not a vintage release database.

No production/acreage trends or movement statistics were recovered in this bounded pass; therefore no unmet demand is inferred. No local buyer access is guaranteed. The North Dakota wheat price and mixed-wheat ERS budget are declared proxies, not Illinois spring-wheat market validation. USDA references do not describe global local markets. International extensions can supply same-schema prices/costs/FX/location; this mission does not download global data.

## Artifacts

The command creates `financial_report.json`, `rotation_finances.csv`, `annual_crop_finances.csv`, `cost_breakdown.csv`, `market_prices.csv`, `price_provenance.json`, `market_access.json`, `break_even_analysis.json`, `risk_scenarios.json`, `financial_assumptions.json`, `data_quality.json`, `FINANCIAL_FINDINGS.md` and byte-hash `manifest.json`. Additional files preserve normalized economic inputs, source integrity, dated historical price analysis, cost reconciliation, unchanged agronomic provenance and implementation hashes.

The phase report contains all16 acceptance statuses and the measured final test/reproduction evidence. Earlier source-parser failures and the first incorrect period-key assumption remain in logs; neither changed public data or simulated yields.
