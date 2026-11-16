# Publication status

This is the verified V1.0 application handoff, published at the owner’s explicit request. The owner selected MIT after the earlier readiness report. That license decision supersedes the historical pending-license wording; all scientific limitations remain.

The functioning API, model dispatch, supplied-input finance, durable jobs and tested offline examples are included. Automatic regional financial benchmark selection and some optional/public-deployment work remain unfinished. Docker execution has not been tested on the originating host. The recorded 546 backend/60 legacy tests were run in the original research workspace; the included HTTP suite and offline examples run without that research archive.

This publication is not a claim that every prior mission item is finished or that predictions have independent farmer-level validation. No frontend, deployment, private data, credentials or original research Git history is included.

## Direct integration update

The package now exposes `agrosim.direct.AgroSim`, `python -m agrosim.direct` and a server-side Node child-process adapter. No owner-hosted URL or process is required. Python/numerical dependencies run on the collaborator's own machine. The HTTP interface remains optional. Scientific limitations and conditional financial support are unchanged.
