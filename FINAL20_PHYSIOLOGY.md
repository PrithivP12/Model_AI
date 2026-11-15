# Opt-in final 20 crop physiology

The original V1, original 68-crop registry/runtime, living ecosystem and finance
engines remain unchanged. The new `agrosim.physiology` entry point adds declared
identity subtypes and shared perennial, paddy and CAM pathways. This is
experimental software coverage, not independent crop prediction validation.
The rejected V1.1 temperature-development candidate is not used.

## Verification

From the repository root, choose a fresh directory:

```sh
.venv/bin/python -m agrosim verify-crop-physiology --config configs/final20_physiology.yaml --output-dir reports/final20-physiology/my-run-a
.venv/bin/python -m agrosim verify-crop-physiology --config configs/final20_physiology.yaml --output-dir reports/final20-physiology/my-run-b
.venv/bin/python scripts/verify_platform_reproduction.py reports/final20-physiology/my-run-a reports/final20-physiology/my-run-b --output reports/final20-physiology/my-reproduction.json
```

The verifier runs the frozen 272 original crop/context cases; all 20 additions
in favorable, drought, heat, cold and living modes; intermittent/nonflooded
rice; alternate identity subtypes; short-day strawberry overwintering; original
V1 and living reference continuations; and backend/legacy tests. Perennial
favorable scenarios span three actual years. State and daily artifacts are
compressed, deterministic and accompanied by explicit inputs. Original daily
archives are retained. A process lock prevents simultaneous verifier runs.

Every proposed new executable crop must pass positive assimilation/product,
conservation, lifecycle, living integration, unavailable-economics and complete
continuous/resumed-state gates. A loaded profile alone is not acceptance.
`report.json`, `crop_coverage.csv/json`, `new_crop_cases.json`, per-crop inputs
and trajectories, `legacy_68_regression.json`, `reference_regression.json`,
`financial_demonstration.json`, `tests.json` and `manifest.json` provide evidence.
Test logs contain volatile elapsed times and are excluded from artifact hashes.

## Selection and simulation

```python
from agrosim.physiology.profiles import coverage, resolve
from agrosim.physiology.runtime import run

catalog = coverage({
    'scallion': 'fistulosum',
    'brachiaria': 'brizantha',
    'ryegrass': 'perenne',
})
profile = resolve('apple')
result = run(
    physical, soil, daily_weather, explicit_plantings, management_events,
    ['apple'], crop_options=declared_crop_options,
    initial=explicit_initial_plant_stocks,
)
```

The named field/weather/management inputs must be supplied. The verifier's
`inputs.json` files and `agrosim.physiology.scenarios.scenario` provide fully
specified controlled examples. A location alone does not identify soil stocks,
cultivar, biological age, management or local prices.

Old-only crop selections delegate directly to `agrosim.crops.runtime.run`.
Their old configuration interface stays unchanged. Selections containing a new
crop use the additive kernel, with the original organ and material ledgers.
Living mode additionally accepts the existing `living_config`; unverified new
host associations remain disabled unless an explicitly sourced Tier D
compatibility declaration is supplied. Such associations are hypothetical.

The original registry continues to describe its original 68 executable models;
use this opt-in catalog/coverage report for expanded support. No existing caller
is silently switched to experimental physiology.

## Identity choices

| Crop-use choice | Supported explicit subtypes |
|---|---|
| Scallion | `fistulosum`: Allium fistulosum; `cepa_green`: immature Allium cepa shoot use |
| Brachiaria | `brizantha`: Urochloa brizantha; `ruziziensis`: Urochloa ruziziensis |
| Ryegrass | `perenne`: Lolium perenne; `multiflorum`: annual/Italian Lolium multiflorum cutting system |

No subtype is silently selected by the runtime. Unknown hybrids and other
interpretations remain unsupported. The demonstration configuration selects
three named subtypes explicitly and separately tests the other three.

## Persistent organs and development

The six existing organ accounts remain the authoritative inventory:
green foliage, persistent structure, roots, carbohydrate/mobile-N reserve,
reproductive material and a crown-stolon account where applicable. Stem,
successor and fruit subdivisions reconcile to these accounts and are never
counted twice. Stocks persist through the same ecosystem checkpoints.

New assimilation uses intercepted PAR, net RUE, cardinal temperature response,
rooted actual soil water and actual mineral-N/mobile-N availability. Allocation
fractions sum to one. Age and minimum persistent structure gate establishment;
there is no age-to-yield multiplier or desired-yield lookup. Initial mature
orchards in demonstrations have explicitly declared age, organs, C/N and prior
shoot stocks. They are not newly planted trees that instantly become mature.

Deciduous trees lose seasonal foliage, accumulate a daily-min/max reconstructed
chilling proxy, then forcing before budbreak. Retained prior shoot material
supports reproductive initiation. Evergreen induction responds to declared
cool/dry exposure where appropriate; tropical continuously reproductive crops
do not inherit apple chilling. Pruning moves actual selected structure to
residue, including during dormancy. Leaf N resorption transfers existing N to
mobile reserves; leaf/fine-root turnover returns actual donor C/N to soil.

Fruit cohorts advance through flowers, explicit reproductive support, fruit
development and maturity. Only actual mature cohort dry mass/N is exported;
each cohort is harvested once. Missing pollination causes floral abortion.
Date palm requires a declared female plant and reproductive support; papaya
requires compatible sex/support. These are external assumptions, not simulated
pollinator abundances or measured fruit-set probabilities.

Strawberry uses crown/stolon material. Select `day_neutral` or `short_day` in
`crop_options['strawberry']['flowering_type']`. The short-day variant initiates
buds during shortening days, retains actual bud material over winter and
requires explicit `short_day_chill_hours` before spring reactivation. Neither
threshold is claimed as a measured universal cultivar requirement.

Banana uses persistent basal/corm structure, separately reconciled pseudostems,
reserve-funded suckers and one reproductive event per stem. After a bunch is
harvested, its parent structure/associated foliage enters residue while basal
material and successors persist. Pineapple similarly retains an explicitly
resource-funded successor rosette. Papaya and palms retain stems and multiple
reproductive cohorts. Alternate bearing, detailed architecture, cold
acclimation, organ-specific pathogens and field fruit counts remain unvalidated
or unavailable; no corresponding yield multipliers were added.

## Rice

Rice requires an explicit `paddy` configuration. It adds pond storage within
the existing water inventory and separately records infiltration, percolation,
managed drainage, overflow and pond evaporation. `initial_pond_mm`, bund height,
infiltration/percolation limits, initial NH4 fraction, fertilizer form, reaction
rates and provenance are required. Nonflooded and intermittent scenarios use
the same declared field component with appropriate pond/irrigation assumptions.
No ordinary dryland-cereal alias is called paddy rice.

Ammonium and nitrate partition the existing mineral-N inventory. They are not
extra N. Nitrification transfers NH4 to NO3; a bounded wetness-history proxy
modulates experimental aerobic/anaerobic rates. Nitrate gas loss consumes
explicit residue C at an idealized 60/56 kg C per kg N stoichiometry, with the
associated residue N mineralized to NH4. Leaching transports actual ion stocks.
Urea hydrolysis, floodwater N/pH chemistry, NH3 volatilization, N2O speciation,
methane, phosphorus and measured Eh are unavailable. The fertilizer scenario
explicitly assumes soil-placed ammonium-equivalent N. This is a reduced process
model, not a calibrated paddy biogeochemistry package.

## Pineapple CAM

The daily step contains night/day carbon accounts. Night stomatal conductance,
CO2 gradient and explicitly supplied nighttime VPD determine potential gas and
water exchange. Root water, carbohydrate carrier and malate capacity constrain
fixation. Three existing carbohydrate carbons plus one atmospheric carbon enter
a four-carbon malate account. Daylight limits turnover/reassimilation; carrier
carbon recycles and is not a second atmospheric input. Structural growth draws
from real carbohydrate stocks with real N demand. Working substrate is retained
for subsequent nights. There is no fixed CAM yield/water-efficiency bonus.

`night_VPD_kPa` and `night_VPD_source` are required. Night temperature and
daylength are daily approximations. Induction is cool exposure or a declared
management date, after age/structure/development gates. The model does not claim
validated hourly CAM phases, measured malate concentrations, full gas exchange,
respiratory energy chemistry or independently validated field water efficiency.

## Products and economics

```python
from agrosim.physiology.finance import (
    product_support, finance_events, evaluate_optional_finance,
)
metadata = product_support('guava')
lots = finance_events(result, explicit_plantings)
status = evaluate_optional_finance(result, explicit_plantings)
# status['status'] == 'financial_data_unavailable' without economic inputs.
```

The default has no fresh/dry conversion, recovery, grade, price, buyer, storage
or complete enterprise budget. Cacao exports whole pods, coconut whole fruit
and rice rough grain; these cannot silently become beans, copra or milled rice.
The adapter aggregates only identical products harvested on the same date,
retaining source-cohort IDs, then uses existing dated lot accounting. Supplying
explicit compatible `financial_inputs` enables the existing financial engine.
It never modifies agronomic states. The demonstration prices raw dry products
with hypothetical prices/costs; it invents no fresh moisture conversion.

## Scientific support

`agrosim.physiology.profiles.SOURCES` links the physiological references.
References support structures and mechanisms. New numerical coefficients are
Tier C transfers or explicit Tier D scenarios, each with units/applicability;
they are not crop-specific measurements or fitted cultivar parameters. Linked
public documentation is cited; source code and large datasets were not copied.
All original accuracy failures and reserved test boundaries remain preserved.
