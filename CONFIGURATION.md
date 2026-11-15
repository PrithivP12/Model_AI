# Runtime configuration

The server reads environment variables, not arbitrary request YAML/files.
Use `.env.example` and explicitly export it as shown in README. No live external
credentials are required by the shipped controlled examples. Existing protected
source-acquisition credentials are not copied, altered or read by the API.

| Variable | Default / meaning |
|---|---|
| `AGROSIM_DATA_DIR` | `var/agrosim`; private SQLite and per-run snapshots/results |
| `AGROSIM_RESOURCES_DIR` | unset; optional operator-only `power.json` and benchmark resources |
| `AGROSIM_CORS_ORIGINS` | exact localhost:3000 origins, comma-separated |
| `AGROSIM_ALLOWED_HOSTS` | localhost,127.0.0.1,testserver; configure actual hostnames deliberately |
| `AGROSIM_API_TOKEN` | unset local demo; at least 32 chars for shared bearer mode |
| `AGROSIM_MAX_PENDING` | 20, bounded 1–100 |
| `AGROSIM_MAX_STORED` | 500, bounded 1–10000 |
| `AGROSIM_MAX_BODY_BYTES` | 2000000, bounded 4096–5000000 |
| `AGROSIM_JOB_TIMEOUT_SECONDS` | 300, bounded 5–3600 |

Run one API process per data directory. Multiple Uvicorn workers are unsupported
and rejected by the worker lock. The server binds loopback by default; public
binding requires a shared token but does not supply tenant authorization or
gateway rate limits. CORS is only browser origin control. Do not expose private
worker logs, SQLite or research artifacts through a static file server.

To archive/delete terminal runs, stop the API, back up the private data directory,
then operate on the SQLite record and its matching UUID directory together.
Do not remove queued/running jobs or edit simulation inputs in place. No
automatic deletion is performed in V1.0. If maintenance is not available,
increase the documented stored limit within its bound or adopt a new private
directory after archiving the old one. This is a single-host local handoff,
not a multi-tenant production service.

Optional Docker assets require an explicit API token. Container execution is
optional; consult the release report for actual test evidence. No Docker/cloud
deployment is performed automatically.
