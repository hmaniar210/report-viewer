# WDIO Report Viewer

A [Streamlit](https://streamlit.io/) app for triaging WDIO/Cucumber execution
report JSON files (like `wdio-tests/reports/developer-executions/*.json`) in a
clean, tabbed UI instead of scrolling through raw JSON. It's built to save time
when you're working through passing and failing scenarios: it clusters similar
errors, flags flaky steps, surfaces the slowest scenarios, and exports
ready-to-paste failure tables for bug reports.

By default you upload the JSON from the UI and everything happens in-memory for
the duration of the browser session. There's also an opt-in **watch mode** where
the app reads a report file or folder the operator mounts and auto-refreshes as
it changes — handy while a test run keeps rewriting the report (see
[Live updates](#live-updates)).

## Features

- **Overview dashboard** — total/passed/failed/skipped metrics, a pass-rate
  bar, a per-session pass/fail bar chart, a per-feature breakdown table, and
  the 10 slowest scenarios. One click downloads the whole report as a Markdown
  summary.
- **By day** — a per-day breakdown table and bar chart (passed/failed/skipped
  and pass rate for each calendar day the run touched), a day filter, and
  one-click CSV downloads of both the day breakdown and the scenarios for the
  selected day(s). The day comes from each scenario's `startTime` (falling back
  to its session's), so runs that span midnight or several days group correctly.
- **Live updates (watch mode)** — give the app the **path to a report file** (the
  one your test run keeps rewriting, e.g. inside another repo) or a folder, and
  it re-reads on a timer so the UI tracks it. Each file is read **live, in place
  — nothing is uploaded or copied**. A sidebar **Auto-refresh** toggle (with a
  chosen interval) and a **Refresh now** button control it, and a file caught
  mid-write is tolerated — the last good render stays until the next good read.
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
  (status + feature-file + **day** multiselects + free-text search over
  name/feature/tags/error) narrows everything at once. The session panel also
  shows a **Failing tags** summary (unique tags gathered from FAILED scenarios),
  and each scenario row includes a **Tags** column so you can quickly identify
  failures by tag. For in-progress sessions, if scenario rows include
  `scenarioIndex` + `scenariosLeft`, the panel also shows current running
  scenario progress (for example, `running #40` and `26 left`) and marks the
  active scenario row.

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
   sidebar drops it and returns to the upload prompt. Alternatively, run in
   **watch mode** (see [Live updates](#live-updates)) to read a mounted report
   file instead of uploading.
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
             "tags": ["release:3.7", "AMOBI-3030", "locale:en_US"],
             "scenarioIndex": 40,
             "scenariosLeft": 26,
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
  sensible defaults, `exampleParams` is only present for scenario outlines,
  `scenarioIndex`/`scenariosLeft` are optional progress hints for in-progress
  runs, and `durationMs` is optional (the slowest-scenarios view simply skips
  scenarios without it).
3. **Render** — `render_report()` renders the top-level metrics and then five
   tabs (Overview / By day / Failure triage / Flaky tests / Sessions), each
   backed by a pure function from `report.py`.

Rendering is a pure function of the parsed dict — there's no database. The
only server-side state is the small `{id: report}` cache used purely to
survive a browser reload; it's cleared when the process restarts or via the
sidebar's "Clear report" button.

## Quick start (one command)

```sh
cd report-viewer
./quickstart.sh /path/to/other-repo/reports          # a FOLDER → multi-report + switching
./quickstart.sh /path/to/other-repo/reports/exec.json # a single FILE → track just that one
# or just: ./quickstart.sh                             # defaults to ./reports
```

`quickstart.sh` makes sure Docker is running — **starting Docker Desktop for you
on macOS and waiting until it's ready** — then builds and starts the app, waits
until it's actually serving, and opens `http://localhost:8501` in your browser.

Give it a **folder** and the sidebar lists every report in it so you can switch
between them and live-track whichever you pick. Give it a **single file** (the
one your test run keeps rewriting, wherever it lives) and it tracks just that one
live. Either way the path is mounted read-only and read in place — nothing is
copied. Pass nothing to use `./reports`.

- Stop + clean (also quits Docker Desktop): `./quickstart.sh down`
- Follow logs: `./quickstart.sh logs`

## Run with Docker

```sh
cd report-viewer
docker compose up --build
```

Then open `http://<your-machine-ip>:8501` — anyone on the same network can reach it.

To find your machine IP on macOS: `ipconfig getifaddr en0`.

The committed [docker-compose.yml](docker-compose.yml) mounts `./reports` into
the container and sets `REPORT_DIR=/data`, so the app starts in **watch mode**
and live-updates from that folder. Remove those two keys for upload-only mode.

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

## Live updates

By default the app is upload-only and in-memory. Set one of these env vars to
switch it into **watch mode**, where it reads a report from disk and
auto-refreshes on a timer. Only these operator-set env vars enable disk reads —
the web UI never accepts a filesystem path, so a visitor on your network can't
use the app to read arbitrary files off the host.

- `REPORT_DIR=/path/to/folder` — watch a **folder of reports**: the sidebar lists
  every `*.json` in it so you can **switch between reports** and live-track
  whichever you pick (or choose **Newest** to always follow the most recently
  updated one).
- `REPORT_FILE=/path/to/report.json` — watch a single fixed file.

Run locally against a folder:

```sh
REPORT_DIR=./reports streamlit run app.py
```

In watch mode the sidebar gains a **File to track** picker (with an *All
reports* list), an **Auto-refresh** toggle, a **Refresh every** interval, and a
**🔄 Refresh now** button. The cadence is minute-scale by default (**1 min**,
adjustable from 30s up to 30 min) — a live report doesn't need second-by-second
polling, and **Refresh now** is always there when you want an instant update. A
file caught mid-write (a test run rewriting it) is tolerated: the last good
report stays on screen with a notice until the next successful read.

**Live means in place.** The app reads the report straight from its file on
disk — it never uploads or copies it. There's no browser upload in watch mode,
because a browser upload only sends the file's *bytes* — not its path — so
there'd be no original file to re-read. To follow a report at its real location
(e.g. inside another repo your tests rewrite), give the app that path:

- With Docker: `./quickstart.sh /path/to/other-repo/reports/exec.json` mounts
  the file's folder read-only and tracks that exact file live.
- Locally: `REPORT_FILE=/path/to/other-repo/reports/exec.json streamlit run app.py`.

## Usage

1. Open the app in a browser.
2. In the sidebar, upload a report JSON (one file at a time).
3. Start on **Overview** for the health of the run, use **By day** to see the
   per-day breakdown and download a day's scenarios as CSV, jump to **Failure
   triage** to see clustered failures (group by error signature or failed step,
   then export CSV/Markdown), check **Flaky tests** for steps that failed in a
   session that also had passing scenarios, and use **Sessions** with the filter
   bar to drill into specific statuses, features, days, or a text search across
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
  assert it renders all five tabs without raising, plus watch-mode tests that
  read a report from a temp folder and tolerate a mid-write file.

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
quickstart.sh                   # one-command Docker start that opens the UI
reports/                        # watch-mode folder (mounted into the container)
Dockerfile / docker-compose.yml # containerized run
docs/
  TESTING_ARCHITECTURE.md       # how the tests are structured and why
  BEST_PRACTICES.md             # conventions for extending the project
tests/
  test_report.py                # unit tests for report.py
  test_app.py                   # AppTest smoke tests for app.py
  fixtures/sample_report.json   # sample WDIO report used by the tests
```
