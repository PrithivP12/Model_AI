# AGROSIM V1 RELEASE READINESS

> Publication update: the owner subsequently selected MIT and requested publication to https://github.com/PrithivP12/Model_AI. This repository is a clean allowlisted copy without the original research Git history. Historical pending-license/no-push statements below describe the earlier verification pass; see PUBLICATION_STATUS.md for the current publication scope. Scientific and unfinished engineering limitations remain unchanged.

**Local developer handoff: READY with explicit limitations. Public GitHub publication: BLOCKED pending owner license/source-rights and history review.**

This release adds an integrated application interface around existing conserved scientific engines. It does not promote scientific accuracy, publish a repository, deploy a service, rebuild any simulator, change the existing frontend or start another research phase.

## Branch and actual changes

Branch: `agrosim-v1-backend-handoff`. No commits, staging, push, public repository creation or history rewrite were performed.
New source: `agrosim/api/{__init__,__main__,app,models,settings,fixtures,service,jobs,worker}.py` and packaged `data/living_fixture.json`; `tests/test_api_release.py`; release build/smoke/E2E/finalization scripts; `handoff/client.ts`, executable example, package lock and exported OpenAPI; `examples/api` requests and actual results.
Existing root files changed: `README.md`, `pyproject.toml` (1.0.0 packaging, optional API dependencies/entrypoint/data) and `.gitignore`. Added configuration/data/license/publication/limitations/integration docs, dependency lock, optional Docker/compose and `.env.example`.
All pre-existing `agrosim`, `configs` and `tests` file hashes captured at mission start remain unchanged. Original V1, old 68-crop runtime, final20 physiology, living ecology and finance code were not edited. User edits and all archived failures/raw data remain preserved. `reports/v1-release/source_change_audit.json` records exact source changes.

## Startup and frontend use

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements-release.lock
.venv/bin/python -m pip install --no-deps .
cp .env.example .env
set -a
. ./.env
set +a
.venv/bin/python -m agrosim.api --host 127.0.0.1 --port 8000
```

Tested with CPython 3.14.7. Base URL: `http://127.0.0.1:8000/api/v1`; documentation: `http://127.0.0.1:8000/docs`; OpenAPI: `http://127.0.0.1:8000/openapi.json`. Keep one API process per private job directory. No live credentials or raw research datasets are required for the controlled sample.
No environment variable is mandatory for loopback/offline startup. Defaults are listed in `.env.example` and CONFIGURATION.md. Configure storage, exact CORS origins/hosts, optional operator resources and optional shared bearer token. The token is required by the CLI for non-loopback binding; authentication alone is not public multi-user authorization.

```sh
curl http://127.0.0.1:8000/api/v1/crops
curl -X POST http://127.0.0.1:8000/api/v1/simulations -H 'Content-Type: application/json' --data-binary @examples/api/offline-corn.json
```

The submission returns 202 with a random durable run ID. Poll `/simulations/{id}`; retrieve `/results` only after `completed`. `examples/api/offline-corn.json` adopts hypothetical controlled soil/weather/management explicitly; location does not become an inferred field profile.
Actual executed result excerpt (assigned ID/time omitted here only):

```json
{
  "schema_version": "1.0",
  "selected_engine": "preserved_reference",
  "mode": "standard",
  "product": {
    "crop_id": "corn",
    "product_id": "corn_grain",
    "harvested": "2020-06-23",
    "product_dry_kg_ha": 4218.638930668569,
    "total_product_dry_kg": 4218.638930668569,
    "product_N_kg_ha": 59.060945029359964,
    "saleable_mass_kg": null,
    "cohort_id": null
  },
  "finance_status": "financial_data_unavailable",
  "independent_accuracy": null,
  "farmer_recommendation": null
}
```

Full actual payloads are in `examples/api/actual-corn-result.json` and the local release evidence directory. JSON schemas explain nulls and units. Products are never summed across identities into a fictitious comparable yield/profit.
For frontend integration, use FRONTEND_INTEGRATION.md and import `AgroSimClient` from `handoff/client.ts`. The executable Node example runs with `cd handoff && npm ci && npm run build && npm run example`; set AGROSIM_URL if using a different port. No existing frontend repository was modified.

## Unified behavior and job reliability

All 88 canonical choices are available. The service dispatches original reference profiles, the 65 earlier experimental profiles and the 20 specialized pathways to their existing runtimes. Three identities require explicit subtypes. Rice field/N configuration, CAM VPD/induction, strawberry flowering and mature perennial stocks remain explicit inputs; no generic grain substitution exists.
Standard is the default. Experimental living mode requires explicit adoption of the retained hypothetical network. Finance is separately optional, uses actual executed product exports and preserves dated lots, source-basis conversions, recovery, sold/stored/unsold/lost stock, cost coverage and cash timing. Supplied management/price/cost data are supported. Automatic regional benchmark adoption is not implemented in the unified API; it is rejected rather than silently extrapolated.
SQLite is the durable job record. A process lock and single scheduler bound numerical execution to one child process. Input snapshots are immutable per UUID, results atomically published, and idempotency avoids duplicate button actions. Queued jobs persist; a restarted process explicitly fails an interrupted running job. Queued/running cancellation and timeout never expose an incomplete result as completed. Tests exercise queue bounds, failure, cancellation/idempotency/restart and no-result behavior. No percent completion is invented.
The local V1 service has no tenant ownership model or distributed worker queue. Public deployment needs a real authorization boundary, rate limits, TLS and per-user quotas. Shared bearer mode is a local/shared-service safeguard; CORS is not authorization. Retention is explicit manual operator archival, with a bounded stored-run count and no automatic deletion. Private farm/log/artifact data must remain outside static/public hosting.

## Data, package and publication evidence

A clean virtualenv installed the pinned release requirements and application wheel, then launched from a separate empty working directory. A wheel built from the allowlisted handoff itself was subsequently installed into that clean environment and passed the same offline HTTP smoke from a second empty working directory. The actual offline corn sample became ready, submitted, completed and matched the frozen corn numerical value without research files, API keys or hidden laptop paths. The wheel packages catalog and hypothetical fixtures. Scientific reproduction archives remain local optional developer resources.
NASA POWER support reuses the existing parser over an operator-controlled cache, validates point/units/coverage and labels historical replay explicitly. No API request can load arbitrary files or URLs. SMAP/GPM/SRTM/Landsat/SoilGrids are reported as not integrated in the release service. Missing optional resources degrade honestly; they are not readiness requirements for the offline demo.
Current candidate runtime source scan found no key/token/private-key patterns, oversized files or actual local private paths. The handoff scanner flags two generic path-detection/test literals in the scan script and HTTP test; those are rejection assertions, not personal absolute paths. A broader current tracked-small-file scan checked 1,201 of 1,289 tracked entries and found no candidate secrets but 83 local-path references. This is not an exhaustive binary/history or legal audit. `.gitignore` does not untrack earlier files. Use the allowlisted handoff builder; do not publish the old working tree/history wholesale.
No project license was invented. Public publication remains blocked on owner license selection and review of catalog/source rights/attribution. The 252-file, approximately 3.4 MB allowlisted handoff can be shared privately according to owner authorization; it excludes `.git`, research archives, credentials, logs, generated runs, caches and virtualenvs. See LICENSE_STATUS.md and GIT_PUBLICATION_CHECKLIST.md.
Dockerfile/compose are supplied as optional integration assets; Docker was not installed here, so container build/start is UNTESTED. The tested startup is the Python path.

## Executed verification

- Full backend regression: 546 passed; legacy: 60 passed.
- Dedicated final HTTP/API tests: 18 passed. They cover reference/experimental/special crop dispatch, subtype rejection, living mode, actual annual/repeated finance, rotation/comparison, invalid coordinates/area/soil/calendar/market/conversion, unsafe fields/paths, request caps, CORS/auth, missing run IDs, worker failure, queue limits, idempotency, cancellation/recovery and numerical repeat.
- Eight additional real network HTTP cases completed: corn, barley, three-year corn/soybean/wheat, apple, rice, pineapple, banana and repeated guava finance. A separately measured living-corn HTTP scenario also completed.
- TypeScript compiled with strict type checking and executed against the running API. Runtime OpenAPI exactly matched the exported schema.
- Fresh installed-package smoke outside the repository source succeeded. `reports/v1-release/fresh-install-smoke.json` retains the exact result.
- Original 272 crop/context summaries and three V1 rotations matched; original 179-day living checkpoint matched. The old platform verifier retains its old 68-crop/accuracy boundaries and therefore still lists intentional partial scientific gates; expanded support is through the new unified dispatch. No numerical regression failure was hidden.
- Repeated identical HTTP numerical hashes and agriculture are exact; request IDs/timestamps are excluded. Final20’s previous 478-artifact reproduction remains preserved; this release does not rebrand it as independent accuracy.
- API dependency audit: zero known findings in the final checked pinned requirements; pip check passed. Four older network dependencies were upgraded following the initial scan. This is advisory-database evidence, not a guarantee of vulnerability absence.
- One existing upstream test-client deprecation warning remains (`httpx` usage by Starlette TestClient). Tests pass; the actual TypeScript and requests-based network smoke do not rely on TestClient.

A final interface correction populates saleable product mass only from explicitly supplied conversion/actual lot accounting. The full numerical/scientific regression preceded that isolated response-field change; the final 18 API tests recheck the updated interface. Existing scientific engine sources remain unchanged.

## Measured local performance

| Operation | Seconds |
|---|---:|
| Catalog HTTP | 0.0170 |
| corn submission → result | 5.733 |
| barley submission → result | 5.931 |
| rotation submission → result | 6.926 |
| apple submission → result | 6.658 |
| rice submission → result | 6.277 |
| pineapple submission → result | 8.022 |
| banana submission → result | 11.059 |
| guava submission → result | 8.446 |

Measurements include subprocess initialization and polling on this host; concurrent verification activity affects timings. No distributed throughput or low-latency production claim is made. Serialization was measured separately in the raw timing report. No physics was changed for speed.

## Scientific limits and remaining engineering scope

No newly independent field accuracy is established. Original layer-water/rotation weaknesses, unpromoted V1.1 failure, largely C/D crop parameters and large hypothetical ecology effects remain visible in each result. Real field initialization, calibrated uncertainty and matched rotation outcomes are not manufactured. No recommended optimum is returned.
Remaining scope: owner licensing/source-rights and old-history review before public GitHub publication; external deployment authz/rate limits/TLS; untested Docker execution; optional automatic regional benchmark selection; advanced coproduct/grade/FX financial interfaces; integration of other environmental products and independently matched biological observations. None is silently claimed complete. The local service, offline examples, supported explicit simulations and documented developer integration are working.

## Release gates

| Gate | Status | Executed evidence / boundary |
|---|---|---|
| A | PASS | Actual HTTP catalog and TypeScript client return 88 unique canonical IDs. |
| B | PASS | Real HTTP/reference runs dispatch corn, barley, apple, rice, pineapple, banana and subtype fixtures to the existing appropriate engines. |
| C | PASS | Original 272 crop/context values, three original V1 rotations and material ledgers reproduce; numerical corn reference fixed at 4218.638930668569 kg dry/ha. |
| D | PASS | Living HTTP/test cases execute the preserved network only after explicit adoption; result identifies hypothetical ecology. |
| E | PASS | Actual corn and repeated guava exports enter the existing dated financial ledger; analytical revenue/cost assertions pass. |
| F | PASS | Orchard/rice/pineapple/banana HTTP runs plus 25 existing special-physiology tests pass without changing those source files. |
| G | PASS | Farm requirements endpoint returns soil/weather/management/subtype gaps and available reference scenarios; no field inference. |
| H | PASS | Structured field/code/message validation tests cover identity, coordinates, area, soil, calendars, conversions, market support and unsafe fields. |
| I | PASS | SQLite metadata, one locked child worker, queued/running/terminal states, idempotency, cancellation, interrupted-worker recovery and safe failure tests pass. |
| J | PASS | Pydantic normalized result/job/validation/comparison models match exported OpenAPI and actual HTTP response validation. |
| K | PASS | Three-year annual reference sequence and persistent special-perennial plans execute; annual overlap and perennial replacement slots are refused. |
| L | PASS | Explicit management, soil/initial stocks and supplied product price/cost/access inputs can override reference assumptions; actual finance tests pass. |
| M | PASS | Missing weather/cache/field data produce explicit issues; absent/unsupported economics remains nullable/unavailable. |
| N | PASS | Compiled TypeScript fetch client listed crops, requested requirements, validated, submitted, polled and fetched an actual running-server result. |
| O | PASS | Runtime /openapi.json exactly equals handoff/openapi.json in executed E2E test. |
| P | PASS | Clean virtualenv installed pinned dependencies and built wheel; API started outside source workspace and completed the README offline sample. |
| Q | PASS | Installed-package offline execution used packaged catalog/fixtures only; no local research archive, secret or NASA API was required. |
| R | PASS | One child process; bounded pending/stored jobs, request size, horizon/comparison size and execution timeout; queue-bound tests pass. |
| S | PASS | Strict fields/units, no request paths/code/URLs, safe UUID directories, shared bearer, restricted CORS/hosts, body caps and safe-error tests pass for local scope. |
| T | PARTIAL | Allowlisted handoff scan excludes raw/private/large/run/history files and detects no candidate secrets; the original tracked tree has 83 local-path files and history is not exhaustively cleared. Do not publish it wholesale. |
| U | BLOCKED | Runtime fixtures/optional external resources are documented, but the owner has not selected a code license or approved all catalog/source redistribution terms. Public publication remains blocked on this decision. |
| V | PASS | 546 backend and 60 legacy tests pass, including original agriculture/ecology/finance/crop/physiology and new HTTP tests; platform verifier reference checks executed. |
| W | PASS | Eight additional real HTTP scenarios plus living mode, direct HTTP client and fresh-install smoke complete; explicit failure never returns a fabricated result. |
| X | PASS | Repeated identical HTTP simulations return equal agricultural content/numerical SHA256 excluding assigned IDs/times; preserved scientific reference values unchanged. |
| Y | PASS | Every result includes mode/engine/provenance/evidence/warnings; 85 experimental profiles and earlier rotation/living failures remain visible. |
| Z | PASS | No farmer recommendation or independent accuracy percentage is produced; conditional finance is explicitly supplied-input economics. |
| AA | PASS | Isolated TypeScript client and HTTP contract integrate without editing any existing frontend files. |
| AB | PASS | Local CLI on independent host/port plus environment-configured exact frontend origins; tested live server. |
| AC | PASS | README, API/reference/configuration/data/security/license guides, OpenAPI, TS example and actual request/result/error fixtures delivered. |
| AD | PASS | This report separates working local handoff, incomplete optional source adoption, untested Docker/public deployment and owner publication decisions. |

## Reproduction and handoff

```sh
.venv/bin/python -m pytest tests/test_api_release.py -q
.venv/bin/python scripts/release_smoke.py --output new-smoke.json
.venv/bin/python scripts/build_release_handoff.py --output /new/private/agrosim-handoff
```

The original workspace additionally runs `.venv/bin/python -m pytest tests -q`, legacy unittest discovery and the two preserved verification commands with their research inputs. Release evidence, exact logs, dependency scans, source audit, actual HTTP results and installed-wheel proof are retained in `reports/v1-release`. The handoff directory is generated locally without modifying/staging the original working tree.
**Developer integration can proceed. No public GitHub publication or farmer-ready scientific release is authorized or claimed. This finite backend mission stops after verified handoff packaging.**
