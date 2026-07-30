# WDIO Report Viewer

A [Streamlit](https://streamlit.io/) app for triaging WDIO/Cucumber execution
report JSON files (like `wdio-tests/reports/developer-executions/*.json`) in a
clean, tabbed UI instead of scrolling through raw JSON. It's built to save time
when you're working through passing and failing scenarios: it clusters similar
errors, flags flaky tests, surfaces the slowest scenarios, and exports
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
- **Flaky tests** — scenarios that both passed *and* failed somewhere in the
  report, so you can re-run them before filing a bug.
- **Sessions** — every session as a collapsible panel with per-session metrics,
  environment, failed steps, and a full scenario table. A **global filter bar**
  (status multiselect + feature-file multiselect + free-text search over
  name/feature/error) narrows everything at once.

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
   instead of crashing the app.
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
   sensible defaults, and `durationMs` is optional (the slowest-scenarios view
   simply skips scenarios without it).
3. **Render** — `render_report()` renders the top-level metrics and then four
   tabs (Overview / Failure triage / Flaky tests / Sessions), each backed by a
   pure function from `report.py`.

Everything is a pure function of the uploaded dict — there's no server-side
state, database, or file persistence involved.

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
   export CSV/Markdown), check **Flaky tests** for scenarios that both passed
   and failed, and use **Sessions** with the filter bar to drill into specific
   statuses, features, or a text search across name/feature/error.

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
