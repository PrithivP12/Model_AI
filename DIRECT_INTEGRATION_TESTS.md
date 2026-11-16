# Direct integration verification

The direct interface wraps the existing simulation/finance service; crop equations,
parameters and the HTTP result contract were not changed.

Measured checks for this update:

- `python -m pytest tests/test_direct.py tests/test_api_release.py -q`:
  **24 passed**, one upstream Starlette/httpx deprecation warning, 104.96 seconds.
- Direct controlled execution with outbound socket connections blocked reproduces
  the existing service numerical hash and corn yield **4218.638930668569 kg dry/ha**.
  An unavailable `AGROSIM_URL` and invalid API-token environment value do not
  affect direct execution. No HTTP server is started.
- Reference, earlier experimental and specialized crop dispatch, conservation,
  supplied-input finance, matched comparisons, local artifact consistency,
  overwrite protection and invalid/missing input handling are exercised.
- The actual Node one-shot example lists **88 crops**, produces the same corn
  yield and has maximum reported conservation residual
  **7.275957614183426e-12**. Structured validation errors propagate to Node.
- The built package was installed and executed from outside the repository using
  its packaged controlled inputs. No research archive or running API was used.
- Both new Node modules pass `node --check`.

Reproduce after README installation:

```sh
.venv/bin/python -m pip install pytest
.venv/bin/python -m pytest tests/test_direct.py tests/test_api_release.py -q
AGROSIM_PYTHON="$PWD/.venv/bin/python" node handoff/example-direct.mjs
```

These verify software integration and numerical regression on Python 3.14.7,
not independent field accuracy, automatic regional finance, browser-only Python
execution or production multi-user hosting. Unchanged scientific limitations
still apply. No deployment was performed.
