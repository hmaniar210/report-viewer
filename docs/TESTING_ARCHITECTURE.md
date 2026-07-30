# Testing architecture

This project keeps its tests fast, deterministic, and easy to extend by
mirroring the structure of the code itself: a pure data layer and a thin
rendering layer, each tested the way that suits it.

## The two layers

| Layer | File | Imports Streamlit? | Tested by |
|---|---|---|---|
| Data processing | [`report.py`](../report.py) | No | [`tests/test_report.py`](../tests/test_report.py) — plain unit tests |
| Rendering | [`app.py`](../app.py) | Yes | [`tests/test_app.py`](../tests/test_app.py) — `AppTest` smoke tests |

Everything that *computes* something — summaries, error clustering, flaky
detection, feature breakdowns, filtering, CSV/Markdown export — lives in
`report.py` as a pure function of the parsed report dict. Nothing there touches
Streamlit, the filesystem, or global state.

`app.py` only *renders*: it calls a `report.py` function and turns the result
into widgets. This is what lets the bulk of the logic be tested in milliseconds
without spinning up a script runtime.

> **Rule of thumb:** if you can write a `assert f(input) == expected` test for
> it, it belongs in `report.py`. If it arranges widgets on screen, it belongs
> in `app.py`.

## Unit tests (`tests/test_report.py`)

These are ordinary `pytest` tests. They run against two kinds of input:

1. **The committed fixture** — [`tests/fixtures/sample_report.json`](../tests/fixtures/sample_report.json),
   a real anonymized WDIO report (6 sessions, 76 scenarios). A dedicated test
   (`test_fixture_is_valid_and_self_consistent`) asserts the fixture's own
   counts add up, so if the fixture is ever replaced the rest of the suite can
   trust it.
2. **Small hand-built dicts** — for behavior that the fixture can't guarantee
   (e.g. a scenario that both passes and fails, or pipe-escaping in Markdown),
   the test builds the minimal dict inline. This keeps each assertion obvious
   and independent of fixture contents.

What to cover when you add a `report.py` function:

- A happy-path result against the fixture.
- At least one hand-built edge case (empty input, missing fields, a value that
  triggers the branch you added).
- For anything that normalizes/clusters, a test proving two *different-looking*
  inputs collapse to the same group (see `test_error_signature_*` and
  `test_cluster_failures_groups_and_sorts`).
- For exports, parse the output back (`csv.DictReader`, line inspection) rather
  than asserting on a raw string — error text contains embedded newlines and
  pipes that will break naive string checks.

## Smoke tests (`tests/test_app.py`)

Rendering is verified with Streamlit's official
[`streamlit.testing.v1.AppTest`](https://docs.streamlit.io/develop/api-reference/app-testing),
which executes the app as a script in a simulated runtime and exposes the
resulting element tree (`at.metric`, `at.tabs`, `at.expander`, `at.info`, …).

Two patterns matter here:

### 1. Pass data via `kwargs`, not a file path

`AppTest.from_function(...)` runs the script in a **temporary working
directory**, so a relative `open("tests/fixtures/…")` inside the script fails.
Instead we load the fixture in the test process and hand the parsed dict to the
script through `kwargs`:

```python
def _render_script(data):
    import app
    app.render_report(data)

at = AppTest.from_function(_render_script, kwargs={"data": report_data})
at.run(timeout=30)
assert not at.exception
```

The script wrapper imports `app` *inside* the function so the import happens in
the runtime, not at collection time.

### 2. Index widgets by position when labels repeat

`AppTest` renders **all** tabs eagerly — there is no lazy tab loading — so
`at.metric` contains the Overview KPIs *and* every per-session metric, several
of which share labels like "Passed"/"Failed". A `{label: value}` map would be
overwritten by the last session. The overview KPIs are asserted by their known
position instead:

```python
# top metrics (Developer, Sessions, Last updated) = indices 0-2
# Overview KPIs (Total scenarios, Passed, Failed, Skipped) = indices 3-6
overview = {m.label: m.value for m in at.metric[3:7]}
assert overview["Passed"] == str(summary["passed"])
```

If you add or reorder top-level metrics, update that slice.

### `main()` without a browser

`test_main_*` covers the upload branch by monkeypatching
`st.sidebar.file_uploader`, `json.load`, and `st.error`, so the invalid-JSON
path is exercised without an actual file or UI. `AppTest.from_file(app.py)`
covers the no-upload prompt.

## `pytest.ini`

```ini
[pytest]
pythonpath = .
```

This puts the repo root on `sys.path` so a bare `pytest` from any directory can
`import app` and `import report`. Without it the smoke tests fail to import the
app under test.

## Dependency pinning is part of the test contract

The suite runs `st.dataframe`/`st.bar_chart`, which serialize through Arrow
(`pyarrow`). The pinned `numpy==1.26.4`, `pandas==2.1.4`, `pyarrow==14.0.2` in
[`requirements.txt`](../requirements.txt), on **Python 3.12**, are a known-good
combination. Unpinning them (or running on Python 3.14) can turn a passing test
into a native segfault (exit code 139) that no Python-level assertion can catch.
See [`BEST_PRACTICES.md`](BEST_PRACTICES.md).

## Running

```sh
pip install -r requirements-dev.txt
pytest            # quiet
pytest -v         # per-test names
pytest tests/test_report.py::test_error_signature_normalizes_ids_numbers_and_quotes
```
