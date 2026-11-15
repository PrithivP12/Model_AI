# Frontend developer handoff

Clone `https://github.com/PrithivP12/Model_AI.git` or use the reviewed handoff directory. Use README's installation and
startup commands. Backend and frontend are separate processes; no changes in
the existing frontend repository are necessary to start the API.

Use `http://127.0.0.1:8000` as the client origin and `/api/v1` as the endpoint
prefix. Set `AGROSIM_CORS_ORIGINS` to your exact development origins before
starting the backend, for example `http://localhost:3000`. Wildcards are refused.
When a shared token is configured, send `Authorization: Bearer ...`. Do not embed
a private deployment-wide secret in a public browser bundle: use your backend
gateway or authenticated server-side proxy. Add user authorization and rate
limits before public multi-user use.

## Farmer workflow

1. Fetch `GET /api/v1/crops`. It returns exactly 88 stable canonical IDs.
2. Send location, area and selected IDs to `POST /api/v1/farms/requirements`.
   It reports missing soil/weather/management, subtype menus and compatible
   controlled scenarios; it does not invent a field profile.
3. Collect advanced inputs or explicitly adopt a returned controlled reference.
   Experimental profiles require `allow_experimental_profiles: true`. Select
   named subtypes for scallion/Brachiaria/ryegrass.
4. Validate at `/simulations/validate`; display each issue's field/code/message.
5. Submit at `/simulations`; poll the returned run ID until terminal. Progress
   is null because the worker does not measure a defensible percentage.
6. Retrieve `/results` after completion. Render products by product/year, never
   add unlike crops into a single sale volume or profit. Display mode, engine,
   parameter evidence, provenance and warnings next to results.
7. Request bounded `/timeseries?aggregation=monthly` or daily pages as needed.
   Research archives are optional `/artifacts` downloads, not default results.

Example requests are in `examples/api`. The offline scenario is explicitly
hypothetical. A location does not make it local measured weather/soil.

## TypeScript

Copy/import `handoff/client.ts` into an application service layer. It uses native
`fetch`, no React/Next dependency. The request schema remains authoritative in
`handoff/openapi.json`; advanced objects have intentionally open TypeScript
record types while the server enforces strict nested schemas.

```ts
import {AgroSimClient, AgroSimError} from './client';
const api = new AgroSimClient('http://127.0.0.1:8000');
const crops = await api.listCrops();
const farm = {
  location: {latitude: 40, longitude: -88, country: 'US', state: 'IL'},
  area: 1, area_unit: 'ha' as const,
};
await api.requirements(farm, ['corn']);
const request = {
  farm, reference_scenario_id: 'controlled_corn_v1',
  adopt_reference_assumptions: true, scenario_id: 'offline_corn',
};
const check = await api.validate(request);
if (!check.valid) throw new Error(JSON.stringify(check.errors));
try {
  const job = await api.submit(request, 'my-button-action-unique-key');
  const done = await api.wait(job.run_id);
  if (done.status === 'completed') {
    const results = await api.results(job.run_id);
    console.log(results);
  } else console.error(done.error ?? done.status);
} catch (error) {
  if (error instanceof AgroSimError) console.error(error.status, error.issues);
  else throw error;
}
```

For the executable Node example: `cd handoff && npm ci && npm run build && npm
run example`. `AGROSIM_URL` optionally changes the URL. The script actually
lists crops, requests requirements, validates, submits, polls and retrieves.
Do not reuse one idempotency key for different inputs; the server returns 409.

## Modes, comparisons and finance

Standard is the default. Living mode requires both
`mode: experimental_living` and
`living: {fixture_id: hypothetical_functional_network_v1, assumptions:
hypothetical_assumptions_adopted}`. It adopts a declared experimental network;
it is not a diagnosis of real pest abundance. No new crop host effects are
silently enabled.

Comparisons accept 2–4 full requests with identical farm, horizon, initial soil
and weather. Different management is explicit and prevents a causal
rotation-effect interpretation. Perennial plans retain structure and cannot be
passed as annual replacement slots. Result products remain separate. A
conditional financial ordering requires complete compatible currency/discount
and cost coverage; no scientific optimum is returned.

Optional `finance` supplies explicit product dry fraction, recovery, grade,
prices, costs, access and sale plan. Missing inputs produce
`financial_data_unavailable`. Raw pods are not processed beans; dry stem is not
refined sugar. The API currently supports supplied prices/costs directly;
automatic regional benchmark adoption is not implemented and is rejected with
a structured issue, while existing source adapters/data remain preserved.

## Errors and operational handling

`{errors: [{field, code, message}]}` is consistent for public errors. 422 means
invalid/unsupported inputs; 404 missing crop/run; 409 result unavailable or key
conflict; 413 body too large; 415 wrong media type; 429 queue/retention full;
401 missing shared authorization; 503 unready. Failed jobs have safe error
codes, not stack traces or private paths. A legitimately zero mature harvest is
a completed scenario with a warning; a failed job has no fabricated result.

Cancellation is a separate POST with `{}` JSON. Queued jobs cancel immediately;
active jobs transition after their child process stops. On API restart, queued
jobs persist and interrupted running jobs become failed. Resubmit deliberately.
Retain run IDs in frontend state; do not assume a browser reload cancels a job.
