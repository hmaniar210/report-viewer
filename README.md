# WDIO Report Viewer


A [Streamlit](https://streamlit.io/) app for triaging WDIO/Cucumber execution
report JSON files (like `wdio-tests/reports/developer-executions/*.json`) in a
clean, tabbed UI instead of scrolling through raw JSON. It's built to save time
when you're working through passing and failing scenarios: it clusters similar
errors, flags flaky steps, surfaces the slowest scenarios, and exports
ready-to-paste failure tables for bug reports.

You upload the JSON from the UI — the app never fetches or reads files from
disk on its own; everything happens in-memory for the duration of the browser
session.

## Features

- **Overview dashboard** — total/passed/failed/skipped metrics, a pass-rate
  bar, a per-session pass/fail bar chart, a per-feature breakdown table, and
  the 10 slowest scenarios. One click downloads the whole report as a Markdown
  summary.
- **Failure triage** — every failure across all sessions, grouped into
  clusters by **error signature** (ids, numbers, timeouts and quoted selectors
  are normalized away so near-identical errors collapse together) or by
  **failed step**. Largest cluster first, with an example error per cluster.
  Export all failures as **CSV** or **Markdown**.
- **Flaky tests** — flaky **steps**, detected **per session**: a step that
  makes some scenarios fail while other scenarios in the *same* session pass —
  evidence the step can work, so the failure is likely transient (network, a
  slow device) rather than a real defect. Flakiness is never inferred across
  sessions, since a fix landed between two runs would look identical to flake.
- **Scenario-outline aware** — outline examples share a name and differ only by
  their `exampleParams`, so the viewer shows those params everywhere scenarios
  are listed. That lets you tell which example failed and which passed even when
  the scenario names are identical.
- **Sessions** — every session as a collapsible panel with per-session metrics,
  environment, failed steps, and a full scenario table. A **global filter bar**
  (status multiselect + feature-file multiselect + free-text search over
  name/feature/example/error) narrows everything at once.

## Architecture at a glance

The app is split into two modules so the analysis logic can be tested without a
browser:

| Module | Responsibility | Streamlit? |
|---|---|---|
| [report.py](report.py) | Pure data processing — summaries, clustering, flaky detection, filters, exports. Every function is a pure function of the parsed dict. | No |
| [app.py](app.py) | Rendering only — turns the results of `report.py` into Streamlit widgets. | Yes |

This separation is deliberate; see
[docs/TESTING_ARCHITECTURE.md](docs/TESTING_ARCHITECTURE.md) for why and
[docs/BEST_PRACTICES.md](docs/BEST_PRACTICES.md) for the conventions to follow
when extending it.

## How it works

1. **Upload** — `main()` renders a sidebar file uploader. Nothing is shown
   until a JSON file is uploaded; invalid JSON is caught and shown as an error
   instead of crashing the app. Once parsed, the report is kept in a small
   server-side cache keyed by an id stored in the page's URL query string
   (`?rid=...`), so **reloading the browser tab keeps the report showing**
   instead of prompting for another upload. A **"Clear report"** button in the
   sidebar drops it and returns to the upload prompt.
2. **Parse** — the uploaded file is `json.load()`-ed into a plain dict. The
   expected shape is:
   ```jsonc
   {
     "developer": "...",
     "totalSessions": 6,
     "lastUpdated": "...",
     "sessions": [
       {
         "sessionNumber": 1,
         "status": "completed",
         "startTime": "...", "endTime": "...", "durationReadable": "...",
         "environment": { "...": "..." },
         "scenarioCount": 2, "passed": 0, "failed": 2, "skipped": 0,
         "scenarios": [
           {
             "name": "...", "featureFile": "...", "status": "FAILED",
             "exampleParams": { "...": "..." },
             "startTime": "...", "endTime": "...",
             "durationReadable": "...", "durationMs": 1234,
             "failure": { "step": "...", "error": "...", "location": "..." }
           }
         ],
         "failedScenarios": [
           { "name": "...", "featureFile": "...", "failedStep": "...", "error": "...", "location": "..." }
         ]
       }
     ]
   }
   ```
   The viewer is defensive: sessions that only carry `failedScenarios` (no
   per-scenario `failure` objects) still work, missing fields fall back to
   sensible defaults, `exampleParams` is only present for scenario outlines, and
   `durationMs` is optional (the slowest-scenarios view simply skips scenarios
   without it).
3. **Render** — `render_report()` renders the top-level metrics and then four
   tabs (Overview / Failure triage / Flaky tests / Sessions), each backed by a
   pure function from `report.py`.

Rendering is a pure function of the parsed dict — there's no database. The
only server-side state is the small `{id: report}` cache used purely to
survive a browser reload; it's cleared when the process restarts or via the
sidebar's "Clear report" button.

## Run with Docker

```sh
cd report-viewer
docker compose up --build
```

Then open `http://<your-machine-ip>:8501` — anyone on the same network can reach it.

To find your machine IP on macOS: `ipconfig getifaddr en0`.

### Without compose

```sh
cd report-viewer
docker build -t report-viewer .
docker run --rm -p 8501:8501 report-viewer
```

## Run locally (no Docker)

> **Use Python 3.12.** The pinned `numpy`/`pandas`/`pyarrow` versions in
> [requirements.txt](requirements.txt) are what Streamlit uses to serialize
> dataframes to Arrow; newer Python builds (3.14) can segfault during that
> native step. See [docs/BEST_PRACTICES.md](docs/BEST_PRACTICES.md) for details.

```sh
cd report-viewer
python3.12 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
streamlit run app.py
```

## Usage

1. Open the app in a browser.
2. In the sidebar, upload a report JSON (one file at a time).
3. Start on **Overview** for the health of the run, jump to **Failure triage**
   to see clustered failures (group by error signature or failed step, then
   export CSV/Markdown), check **Flaky tests** for steps that failed in a
   session that also had passing scenarios, and use **Sessions** with the filter
   bar to drill into specific statuses, features, or a text search across
   name/feature/example/error.

## Testing

Tests are split to match the architecture:

- [tests/test_report.py](tests/test_report.py) — fast, pure unit tests for
  every function in `report.py` (summaries, error-signature clustering, flaky
  detection, feature breakdown, filters, CSV/Markdown exports). No Streamlit
  runtime needed.
- [tests/test_app.py](tests/test_app.py) — `streamlit.testing.v1.AppTest`
  smoke tests that run the real app against a committed sample report
  ([tests/fixtures/sample_report.json](tests/fixtures/sample_report.json)) and
  assert it renders all four tabs without raising.

```sh
pip install -r requirements-dev.txt
pytest            # or: pytest -v
```

[pytest.ini](pytest.ini) sets `pythonpath = .` so bare `pytest` can import
`app` and `report`. The full rationale and patterns (why data is passed to
`AppTest` via kwargs rather than file reads, how fixtures are committed) are in
[docs/TESTING_ARCHITECTURE.md](docs/TESTING_ARCHITECTURE.md).

## Project structure

```
app.py                          # Streamlit rendering layer (imports report.py)
report.py                       # pure data-processing layer (no Streamlit)
requirements.txt                # runtime dependencies (pinned)
requirements-dev.txt            # + test dependencies (pytest)
pytest.ini                      # pythonpath so `pytest` finds app/report
Dockerfile / docker-compose.yml # containerized run
docs/
  TESTING_ARCHITECTURE.md       # how the tests are structured and why
  BEST_PRACTICES.md             # conventions for extending the project
tests/
  test_report.py                # unit tests for report.py
  test_app.py                   # AppTest smoke tests for app.py
  fixtures/sample_report.json   # sample WDIO report used by the tests
```
