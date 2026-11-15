# AgroSim HTTP V1 contract

Base prefix `/api/v1`; JSON only. Source of truth: generated
`handoff/openapi.json` and `agrosim/api/models.py`. Unknown object fields,
nonfinite/bool numeric values, unsafe IDs and unsupported units are rejected.
Dates are ISO `YYYY-MM-DD`. Coordinates use degrees; area uses ha or acre.

| Method/path | Behavior |
|---|---|
| GET `/health` | Version and process liveness; no auth required |
| GET `/ready` | Catalog, offline fixture, SQLite and worker checks; source availability separately reported |
| GET `/crops` | 88 canonical entries; optional lifecycle/status/family filters |
| GET `/crops/{crop_id}` | Identity/subtypes, correct engine, products, requirements and scientific support |
| POST `/farms/requirements` | `{farm,crops}`; missing data, sources and explicit reference options |
| POST `/simulations/validate` | Full simulation request; typed errors/warnings without running physiology |
| POST `/simulations` | 202 durable job; optional `Idempotency-Key` |
| GET `/simulations/{id}` | Durable job state; null progress |
| POST `/simulations/{id}/cancel` | `{}` body; safe process cancellation |
| GET `/simulations/{id}/results` | Completed normalized simulation/comparison only |
| GET `/simulations/{id}/timeseries` | `aggregation=daily|monthly`, start/end, offset, limit<=366; comparison `scenario=0..3` |
| GET `/simulations/{id}/artifacts` | Compressed raw research JSON; comparison `scenario=0..3` |
| POST `/comparisons` | 2–4 matched-environment scenarios; optional conditional finance ordering |
| GET `/metadata` | Modes, units, evidence tiers, sources, references and limits |

## Simulation setup

`farm` requires location and positive area. Either explicitly adopt a supported
`reference_scenario_id` or supply `start`, `end`, `soil`, `weather`, `plantings`
and `management` (empty list means deliberately no events). Maximum duration
is 1831 days, 20 crop slots, 10 soil layers and 2000 management events. Numerical
values have physically/operationally bounded schema ranges.

Soil needs geometry/hydraulics and initial water, mineral N, humus/residue C/N,
temperature and provenance. Per-layer vector dimensions must match. Weather
needs every contiguous day of the exact horizon, temperature, precipitation,
solar radiation and optional ET0. Sources distinguish observations, user inputs,
references, regional proxies and hypotheses.

`cached_nasa_power` uses only operator-configured `power.json`, validates the
cached point against the request and requires a source start year. It performs
explicit historical calendar replay and refuses missing days; no arbitrary
download URLs or file paths are accepted. Controlled reference weather requires
explicit adoption and is not location-derived.

Plantings use canonical IDs and explicit subtype if needed. Annual windows
cannot overlap and must complete within the horizon. An already established
special perennial requires all six real organ dry/N stocks, age, provenance and
a planting date before the horizon. Initial fruit stocks are refused without
supported initial cohorts. Perennial plans are continuous, not annually reset.
Rice requires a paddy/nonflooded field configuration; pineapple requires sourced
night VPD; strawberry requires a flowering type and, for short-day types,
declared chilling. Pollination is not automatically satisfied.

## Results and time series

Every normalized result includes schema/engine versions, selected engine/mode,
crop/subtype identities, area, units, provenance, scientific evidence, warnings,
products by date/year, soil endpoints, water fluxes, conservation residuals,
survival/termination, optional finance/ecology and a deterministic numerical
hash. Timestamps/run IDs are deliberately excluded from that hash. Finance is
conditional; independent accuracy and farmer recommendation remain null.

Product dry mass uses kg dry/ha; total mass multiplies by actual hectares.
No fresh mass is inferred. When supplied, financial sale-basis conversion is
recorded in the financial detail, with sold/stored/unsold/lost inventory. Cost
coverage is explicit; a supplied seed-only account is not a verified complete
farm budget. Stock variables in monthly series use means; physical daily fluxes
use sums. Default detail is 120 monthly rows, daily maximum 366 rows per page.

Finance uses one product/grade per crop. Supply decision date, currency, valid
conversion, dated quotes, crop-year cost lines, markets and sales. Cross-currency
conversion, automatic regional source adoption, multiple grades/coproducts and
unmatched processing conversions are not exposed in this V1 API. Missing
economics returns nullable unavailable values rather than invented profits.

## Jobs, errors and limits

One API process owns a file lock and one SQLite-backed scheduler. At most one
numerical child runs at once; pending count, stored count, body size and execution
time are bounded. Job IDs are random hex UUIDs; IDs never become arbitrary paths.
Inputs are atomically snapshotted. No shell/user Python is executed. Identical
idempotency key+input returns the same run; changed input with the same key is
409. There is no automatic cross-request numerical cache.

Errors are `{errors:[{field,code,message}]}`; submitted request bodies, secrets,
tracebacks and private paths are not echoed. Interrupted jobs fail explicitly;
queued jobs recover. Logs/artifacts remain private operator files. No automatic
retention deletion; the bounded stored-count limit returns 429 until the
operator archives/removes completed records using documented maintenance.
