# AgroSim platform verification

This is a nonvisual, bounded backend evaluation. V1 remains the default. The
versioned V1.1 temperature-development candidate is experimental and unpromoted.
The crop registry contains 88 choices: 3 reference profiles, 65 experimental
profiles and 20 blocked choices. Execution does not establish field accuracy.

From the repository root, use a **new** output directory:

```sh
.venv/bin/python -m agrosim verify-platform --config configs/platform_validation.yaml --output-dir reports/platform-readiness/my-run
```

The command executes all 272 frozen crop/context cases, compares their numerical
summaries to the previous crop-expansion cohort, checks 68 continuous/resumed
daily traces, reproduces three original V1 rotations and the original 179-day
living checkpoint, executes ten integrated scenarios, evaluates six actual
harvest streams financially, evaluates the bounded V1.1 diagnostic, verifies
preserved source/results and runs backend plus legacy tests. A process lock
prevents overlapping verifier runs. Existing output directories are refused.

The ten integrations comprise three rotations under identical cached NASA
weather in standard/living modes, a repeated tomato harvest and a persistent
three-year alfalfa scenario in both modes. Historical weather is replayed onto
a declared future calendar. Soil and management are hypothetical. This is
neither an observed future weather forecast nor a local field reconstruction.

Outputs include `report.json`, `READINESS.md`, `crop_support.json`,
`parameter_evidence_audit.json`, `crop_execution.json`,
`reference_regression.json`, `integrations.json`, `financial_integrations.json`,
`rotation_evidence.json`, `v11/report.json`, `tests.json`,
`preservation_check.json`, source hashes and an artifact manifest. Daily traces
are executed and hashed; compact summaries avoid duplicating the older full
daily archives. Test logs retain real elapsed times and are the only volatile
artifacts excluded from deterministic hashes.

For fresh-directory reproduction, run the same command into a second new
directory, then:

```sh
.venv/bin/python scripts/verify_platform_reproduction.py reports/platform-readiness/my-run reports/platform-readiness/my-second-run --output reports/platform-readiness/my-reproduction.json
```

The comparison rehashes every claimed deterministic file, detects missing/extra
files and reports differences. Reproduction assumes identical source, existing
data, Python/dependencies and configuration in the same repository location.
It does not certify statistical generalization. Gate AC in each individual run
remains partial until this separate comparison succeeds.

## Dated product-lot finance

The original `finance-evaluate` command and single-harvest adapters are retained.
The additional Python interface is:

```python
from agrosim.finance.events import (
    bind_product_events, allocate_season_costs, evaluate_product_events,
)

# actual_result comes from agrosim.crops.runtime.run, with explicit plantings.
lots = bind_product_events(actual_result['product_harvests'], plantings)
accounts, water, allocation = allocate_season_costs(
    lots, supplied_costs_by_crop_and_year,
    irrigation_by_year=actual_irrigation_m3_by_year,
    source=declared_allocation_provenance,
)
financial = evaluate_product_events(
    harvests=lots, product_specs=explicit_product_specs,
    costs_by_lot=accounts, irrigation_by_lot=water, allocation=allocation,
    **existing_farm_price_sale_market_scenario_and_horizon_arguments,
)
```

This example describes the interface; the named inputs must be supplied.
Crop/year cost keys are `"tomato:2020"`, for example. The allocation helper
divides area/total expenses by actual produced dry mass, charging the annual
account once. Sale, storage and irrigation unit rates remain unchanged and
use actual lot quantities. Alternatively supply explicit lot accounts directly.
Shared annual irrigation across multiple harvested crops requires a separately
documented crop/lot allocation; it is not inferred.

Every lot needs an establishment date, harvest date, product identity and
executed product dry mass. Product specifications require `product_id`, `crop`,
`commodity_class`, `grade`, `marketed_fraction`, `dry_matter_fraction` and
`source`. Sales may select `lot_id` for picking-specific prices or use existing
FIFO inventory rules. Buyer capacity applies across all lots. Unsold inventory
never becomes cash. Multiple grades or coproducts from one crop require a
future supported specification; this interface refuses ambiguous identities.

The verifier's financial inputs are explicitly **synthetic accounting
fixtures**, including product dry fraction and price. They demonstrate software
integration with real simulated exports, not observed saleable moisture,
regional market coverage or farm profit. Missing production accounts, prices,
conversion provenance and buyer access continue to produce unavailable or
partial results. Finance never modifies agronomic states.
