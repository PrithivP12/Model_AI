# Run AgroSim in your own application

Nothing needs to run on the owner's Mac. Clone this repository onto your own
computer or backend host and install it there. Direct execution needs no
`AGROSIM_URL`, HTTP listener, API token, background service, or owner credentials.
Python and the numerical dependencies must run on the machine doing computation.
GitHub stores the code; it does not host a running prediction service.

## Install once

```sh
git clone https://github.com/PrithivP12/Model_AI.git
cd Model_AI
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements-release.lock
.venv/bin/python -m pip install --no-deps .
```

The lockfile reproduces the tested Python 3.14 environment. Other supported
Python versions/platforms need their own numerical checks. No downloaded
research archive, live NASA key, or `.env` file is needed for the packaged
controlled scenarios.

## Python backend: call the model directly

```python
import json
from agrosim.direct import AgroSim

engine = AgroSim()
request = json.load(open('examples/api/offline-corn.json'))
validation = engine.validate(request)
if not validation['valid']:
    raise ValueError(validation['errors'])
result = engine.simulate(request)
print(result['agriculture']['products'])
print(result['finance'])
```

`engine.crops()` returns all 88 choices and their current support.
`engine.compare({'scenarios': [request_a, request_b]})` uses the same matched-
environment checks and engines as the HTTP backend. An optional
`output_dir='new-run-directory'` saves local artifacts; an existing directory is
refused so previous results cannot be overwritten. Without that argument the
call creates no job database or run directory. Result `timeseries_url` is null;
`artifacts` contains local paths only when output was requested. Financial
calculations still require explicit compatible price/cost/product assumptions.
Missing observations and unsupported predictions remain unavailable.

Direct calls are synchronous. Use your backend's bounded job/worker pool for
long runs and user isolation. The SDK is a trusted server-side interface, not
an authentication or multi-tenant hosting service.

## Command line: no server

```sh
.venv/bin/python -m agrosim.direct crops
.venv/bin/python -m agrosim.direct validate --input examples/api/offline-corn.json
.venv/bin/python -m agrosim.direct simulate --input examples/api/offline-corn.json
# Optional persisted outputs in a NEW directory:
.venv/bin/python -m agrosim.direct simulate --input examples/api/offline-corn.json --output local-run-001
```

Successful commands print one JSON value to stdout. Errors print structured
JSON to stderr and return nonzero. JSON may also be supplied on stdin. A compare
request uses the `compare` command. Optional `--resources` points to your own
local, documented environmental inputs; no automatic field data is invented.

## Node / Next.js backend

Copy `handoff/direct.mjs` to a server-only module. Install Python and AgroSim on
the same backend machine, then:

```js
import {runAgroSim} from './direct.mjs';
const result = await runAgroSim(request, {python: '/your/app/.venv/bin/python'});
```

This launches a bounded one-shot Python worker, exchanges JSON over standard
input/output, and exits. There is no HTTP request, daemon, or model URL. The
adapter refuses oversized inputs/outputs and kills timed-out workers. Use a
bounded queue for concurrent users. It requires a Node host that permits child
processes and includes Python; it is not a browser/edge-runtime module. A static
browser-only site cannot execute this Python backend by importing JavaScript.

Run the actual Node example without npm dependencies or a running server:

```sh
AGROSIM_PYTHON="$PWD/.venv/bin/python" node handoff/example-direct.mjs
# Or from handoff, with the installed Python on PATH: npm run example
```

`AGROSIM_PYTHON` selects an executable, not a network address. The example lists
88 crops and computes the controlled corn result. The older HTTP client remains
available as `handoff/client.ts` and `npm run example:http` for applications that
choose to host a separate service on their own infrastructure.

This integration changes how computations are invoked. It does not change crop
equations, scientific accuracy, or the documented recommendation limitations.
