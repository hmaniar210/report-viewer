# Best practices

Conventions for working on the WDIO Report Viewer. The goal is a small,
predictable codebase that stays easy to test and extend.

## Environment

- **Use Python 3.12.** It's the version the pinned dependencies are validated
  against. Python 3.14 has been observed to segfault (exit 139) inside
  `pyarrow` during Streamlit's dataframe-to-Arrow serialization.
- **Work inside a virtualenv:**
  ```sh
  python3.12 -m venv .venv
  source .venv/bin/activate
  pip install -r requirements-dev.txt
  ```

## Dependencies

- **Keep runtime deps pinned.** [`requirements.txt`](../requirements.txt) pins
  `streamlit`, `numpy`, `pandas`, and `pyarrow` to a known-good set. The
  numpy/pandas/pyarrow pins exist specifically to avoid the native segfault
  above — do not bump them casually. If you must upgrade, run the full test
  suite *and* launch the app and open every tab (the crash only shows up when a
  dataframe actually renders).
- **Prefer the standard library.** The data layer uses only `csv`, `io`, `re`,
  and `collections` — no extra dependencies for parsing, clustering, or export.
  Reach for a new dependency only when the stdlib genuinely can't do the job.
- **Dev-only tools go in [`requirements-dev.txt`](../requirements-dev.txt),**
  which includes `requirements.txt` via `-r` and adds `pytest`.

## Where code goes

Keep the two-layer split intact (see
[`TESTING_ARCHITECTURE.md`](TESTING_ARCHITECTURE.md)):

- **`report.py` — pure logic.** No Streamlit import, no file I/O, no global
  state. Every function takes the parsed dict (or a slice of it) and returns
  plain Python values. This is what keeps the logic unit-testable.
- **`app.py` — rendering only.** It calls `report.py` and lays out widgets. If
  you find yourself computing counts, grouping, or formatting export text
  inside `app.py`, move that into `report.py` and call it.

### Adding a feature (the usual flow)

1. Write the computation as a pure function in `report.py`.
2. Add unit tests for it in `tests/test_report.py` (happy path + one edge case).
3. Render it in `app.py`, ideally in its own `render_*` helper.
4. If it adds/reorders top-level metrics or tabs, update the affected
   `AppTest` assertions in `tests/test_app.py` (they index some widgets by
   position).
5. Update the **Features** section of the [README](../README.md).

## Coding style

- **Be defensive about report shape.** Reports vary: some sessions carry
  per-scenario `failure` objects, others only a `failedScenarios` list; fields
  go missing. Use `data.get(...) or default` and helpers like
  `normalize_status()` and `_scenario_failure()` rather than assuming keys
  exist. `collect_failures()` is the reference example — it reads scenarios
  first and falls back to `failedScenarios`.
- **Normalize before you compare or group.** Statuses run through
  `normalize_status()`; error strings run through `error_signature()` so that
  messages differing only by ids/numbers/timeouts/quoted selectors cluster
  together. Add new normalization there, not ad hoc at call sites.
- **Don't over-engineer.** No speculative config, abstractions, or "just in
  case" flexibility. Add the smallest thing that solves the task; a plain dict
  or list is usually enough — there's no ORM, no dataclass ceremony, no plugin
  system here on purpose.
- **Name render helpers `render_*`** and keep each focused on one tab or
  section, matching the existing `render_overview` / `render_failures` /
  `render_flaky` / `render_sessions` layout.

## Data handling & privacy

- The app is **in-memory only** — it reads the uploaded file for the session
  and never writes reports to disk or a database. Keep it that way; don't add
  server-side persistence without a deliberate reason.
- **Don't commit real reports.** [`.gitignore`](../.gitignore) ignores `*.json`
  except the test fixture (`!tests/fixtures/*.json`). The committed fixture
  should stay anonymized. When updating it, keep its internal counts consistent
  so `test_fixture_is_valid_and_self_consistent` passes.

## Before you commit

```sh
pytest
```

All tests must pass. If you touched rendering, also run the app once and click
through the four tabs — `AppTest` catches exceptions but not every visual
regression:

```sh
streamlit run app.py
```
