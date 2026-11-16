# AgroSim Global V1.0 backend

A versioned Python/HTTP backend for explicit crop–soil, rotation, experimental ecosystem and conditional financial scenarios. All 88 canonical crop choices are discoverable through one API: 3 preserved reference profiles, 65 earlier experimental profiles and 20 specialized experimental pathways. Scallion, Brachiaria and ryegrass require a named subtype.

**Functional execution is not independent field accuracy.** The rotation model has failed important simple-baseline comparisons. New crop physiology and living-population assumptions are largely experimental. No optimal-rotation, guaranteed-profit or farmer-ready accuracy claim is made.

## Quick start — no model URL or separate server

Your collaborator can run this package on their own computer or application
backend. Nothing needs to run on the owner's Mac. No AgroSim URL, API token,
`.env`, NASA credentials or research download is needed for the controlled example.

```sh
git clone https://github.com/PrithivP12/Model_AI.git
cd Model_AI
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements-release.lock
.venv/bin/python -m pip install --no-deps .
.venv/bin/python -m agrosim.direct simulate --input examples/api/offline-corn.json
```

Read [DIRECT_INTEGRATION.md](DIRECT_INTEGRATION.md) for direct Python calls,
server-side Node integration, comparisons and optional local artifact output.
The calculation runs in the caller's process or a one-shot worker. It uses the
same actual simulation and financial code as the HTTP service. Real field
scenarios still need defensible environmental and management inputs; the
controlled example explicitly adopts hypothetical assumptions.

For Node, after the install above:

```sh
AGROSIM_PYTHON="$PWD/.venv/bin/python" node handoff/example-direct.mjs
```

## Optional HTTP service

Use this only if your application chooses a separately hosted API. The direct
integration above requires no service or URL.

```sh
.venv/bin/python -m agrosim.api --host 127.0.0.1 --port 8000
```

API: `http://127.0.0.1:8000/api/v1`; interactive docs: `/docs`.
The HTTP integration and operational settings are documented in
[FRONTEND_INTEGRATION.md](FRONTEND_INTEGRATION.md). A URL here refers to your own
hosted backend, never a required service on the owner's computer.

## Architecture and handoff

- `agrosim.api.service.AgroSimService` unifies crop discovery, requirements, validation, dispatch, actual harvest finance and comparisons.
- FastAPI/Pydantic provide `/api/v1`, OpenAPI and bounded JSON contracts.
- SQLite stores job metadata; a locked single scheduler starts at most one bounded numerical child process. Completed runs have immutable input/result artifacts. Restart marks interrupted active jobs failed; queued jobs resume.
- Existing reference/crop/physiology runtimes perform all biological computation. Living ecology is opt-in; finance consumes actual harvested products without modifying agronomic states.
- Summary results keep products separate. Optional daily/monthly time series are bounded; compressed research artifacts remain explicitly requested detail.

Read [FRONTEND_INTEGRATION.md](FRONTEND_INTEGRATION.md), [API_REFERENCE.md](API_REFERENCE.md), [CONFIGURATION.md](CONFIGURATION.md), [DATA_MANIFEST.md](DATA_MANIFEST.md), [KNOWN_LIMITATIONS.md](KNOWN_LIMITATIONS.md) and [AGROSIM_V1_RELEASE_READINESS.md](AGROSIM_V1_RELEASE_READINESS.md). `handoff/client.ts` is an isolated TypeScript fetch client; it does not change your frontend.

## Tests

```sh
.venv/bin/python -m pip install pytest
.venv/bin/python -m pytest tests/test_direct.py tests/test_api_release.py -q
```

The original development workspace additionally supports `pytest tests -q`, legacy `unittest discover`, `verify-platform` and `verify-crop-physiology`. Those scientific reproduction suites require the locally preserved research archives described in the data manifest; they are not hidden runtime dependencies of the offline API example.

## Configuration and safety

Defaults bind to loopback and allow only explicit localhost frontend origins. `.env.example` documents job storage, request limits, retention and optional resource directory. Missing configured NASA/market resources return unavailable metadata. The API never fetches arbitrary request URLs or reads request-specified paths.

Optional `AGROSIM_API_TOKEN` enables a shared bearer boundary. Non-loopback startup requires it. Public multi-user deployment still needs tenant authorization, TLS, rate limits, request quotas and an authenticated gateway. CORS is not authorization. SQLite/output directories contain private user farm inputs; protect them and archive/remove terminal runs deliberately. No automatic deletion occurs.

Docker assets are optional and provided for review; the release report states whether container execution was actually tested.

## Git and licensing

This repository contains the allowlisted application handoff. Large research/private archives and the original research Git history are excluded. See [GIT_PUBLICATION_CHECKLIST.md](GIT_PUBLICATION_CHECKLIST.md). The project has known scientific and unfinished optional integration limits; publication does not remove them.

Project code is licensed under MIT as selected by the owner; see [LICENSE](LICENSE) and [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md). Third-party archives retain their own rights and are not bundled. Raw research files remain local.
