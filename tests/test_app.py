"""Smoke tests that run the real Streamlit app against a committed sample
report (tests/fixtures/sample_report.json) and assert it renders without
raising. Pure data logic is covered separately in test_report.py.
"""
import json
from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

import app
import report as rp

FIXTURE_PATH = Path(__file__).parent / "fixtures" / "sample_report.json"


@pytest.fixture(scope="module")
def report_data() -> dict:
    with open(FIXTURE_PATH, encoding="utf-8") as f:
        return json.load(f)


@pytest.mark.parametrize("status", ["PASSED", "FAILED", "SKIPPED", None, "", "weird"])
def test_status_badge_never_raises(status):
    badge = app.status_badge(status)
    assert isinstance(badge, str) and badge


def _render_script(data):
    import app as _app

    _app.render_report(data)


def test_render_report_runs_without_exception(report_data):
    """Runs the real app.render_report() inside a simulated Streamlit script
    and asserts no uncaught exception was raised while rendering the fixture."""
    at = AppTest.from_function(_render_script, kwargs={"data": report_data})
    at.run(timeout=30)

    assert not at.exception, f"Rendering raised: {[str(e) for e in at.exception]}"


def test_overview_tab_shows_overall_totals(report_data):
    at = AppTest.from_function(_render_script, kwargs={"data": report_data})
    at.run(timeout=30)

    assert not at.exception

    summary = rp.summarize(report_data)
    # Top metrics (Developer, Sessions, Last updated) occupy indices 0-2;
    # the Overview KPIs follow at 3-6. Per-session metrics share the same
    # labels, so index by position instead of a label lookup.
    overview = {m.label: m.value for m in at.metric[3:7]}
    assert overview.get("Total scenarios") == str(summary["total"])
    assert overview.get("Passed") == str(summary["passed"])
    assert overview.get("Failed") == str(summary["failed"])
    assert overview.get("Skipped") == str(summary["skipped"])


def test_report_renders_four_tabs(report_data):
    at = AppTest.from_function(_render_script, kwargs={"data": report_data})
    at.run(timeout=30)

    assert not at.exception
    assert len(at.tabs) == 4


def test_main_shows_upload_prompt_when_no_file():
    at = AppTest.from_file(str(Path(__file__).parent.parent / "app.py"))
    at.run(timeout=30)

    assert not at.exception
    assert any("Upload a WDIO execution report" in i.value for i in at.info)


def test_main_handles_invalid_json(monkeypatch):
    """Ensures main() reports a clean error for invalid JSON instead of crashing."""
    import streamlit as st

    class FakeUploaded:
        def read(self):
            return b"{not valid json"

    def fake_load(_fp):
        raise json.JSONDecodeError("bad", "{", 0)

    monkeypatch.setattr(st.sidebar, "file_uploader", lambda *a, **k: FakeUploaded())
    monkeypatch.setattr(json, "load", fake_load)

    errors = []
    monkeypatch.setattr(st, "error", lambda msg: errors.append(msg))

    app.main()

    assert errors, "main() should call st.error() on invalid JSON"


# --- report persistence across browser reload ---------------------------------


def test_main_restores_report_from_query_param_after_reload(monkeypatch):
    """A browser reload gives a fresh session (uploaded=None), but the report id
    kept in the URL query params should still resolve to the stored report."""
    import streamlit as st

    report_id = "test-persist-id"
    fake_data = {"developer": "d", "sessions": []}
    app._REPORT_STORE[report_id] = fake_data
    try:
        monkeypatch.setattr(st.sidebar, "file_uploader", lambda *a, **k: None)
        monkeypatch.setattr(st.sidebar, "button", lambda *a, **k: False)
        monkeypatch.setattr(st, "query_params", {"rid": report_id})

        rendered = {}
        monkeypatch.setattr(app, "render_report", lambda data: rendered.setdefault("data", data))

        app.main()

        assert rendered.get("data") == fake_data
    finally:
        app._REPORT_STORE.pop(report_id, None)


def test_main_clear_report_button_removes_stored_report(monkeypatch):
    import streamlit as st

    report_id = "test-clear-id"
    app._REPORT_STORE[report_id] = {"developer": "d", "sessions": []}

    class FakeQueryParams(dict):
        def pop(self, key, default=None):
            return dict.pop(self, key, default)

    query_params = FakeQueryParams(rid=report_id)
    monkeypatch.setattr(st.sidebar, "file_uploader", lambda *a, **k: None)
    monkeypatch.setattr(st.sidebar, "button", lambda *a, **k: True)
    monkeypatch.setattr(st, "query_params", query_params)
    monkeypatch.setattr(st, "rerun", lambda: (_ for _ in ()).throw(SystemExit))

    with pytest.raises(SystemExit):
        app.main()

    assert report_id not in app._REPORT_STORE
    assert "rid" not in query_params


# --- scenario-outline exampleParams + info buttons ---------------------------


@pytest.fixture
def outline_data() -> dict:
    """A small report with a scenario outline (mixed pass/fail examples in one
    session), a SKIPPED scenario, and a failedScenarios-only fallback session —
    covers everything the Example column / info buttons need to exercise."""
    return {
        "developer": "test-dev",
        "totalSessions": 2,
        "sessions": [
            {
                "sessionNumber": 1,
                "status": "completed",
                "scenarioCount": 3,
                "passed": 1,
                "failed": 1,
                "skipped": 1,
                "scenarios": [
                    {
                        "name": "Enter bolus amount",
                        "featureFile": "f.feature",
                        "exampleParams": {"separator": "period", "food": "2.5"},
                        "status": "PASSED",
                    },
                    {
                        "name": "Enter bolus amount",
                        "featureFile": "f.feature",
                        "exampleParams": {"separator": "comma", "food": "2,5"},
                        "status": "FAILED",
                        "failure": {"step": "enter value", "error": "bad separator"},
                    },
                    {
                        "name": "Skipped scenario",
                        "featureFile": "f.feature",
                        "status": "SKIPPED",
                    },
                ],
                "failedScenarios": [
                    {
                        "name": "Enter bolus amount",
                        "exampleParams": {"separator": "comma", "food": "2,5"},
                        "failedStep": "enter value",
                        "error": "bad separator",
                    }
                ],
            },
            {
                "sessionNumber": 2,
                "status": "completed",
                "scenarioCount": 1,
                "passed": 0,
                "failed": 1,
                "skipped": 0,
                "scenarios": [{"name": "Legacy failure", "featureFile": "f.feature", "status": "FAILED"}],
                "failedScenarios": [
                    {"name": "Legacy failure", "failedStep": "legacy step", "error": "legacy error"}
                ],
            },
        ],
    }


def test_render_report_runs_without_exception_for_outline_data(outline_data):
    at = AppTest.from_function(_render_script, kwargs={"data": outline_data})
    at.run(timeout=30)
    assert not at.exception


def test_info_buttons_explain_every_new_feature(outline_data):
    """Each of the four ℹ️ info popovers should be present with feature-specific
    guidance; popover body markdown renders into the tree even when collapsed."""
    at = AppTest.from_function(_render_script, kwargs={"data": outline_data})
    at.run(timeout=30)
    assert not at.exception

    all_markdown = " ".join(m.value for m in at.markdown)
    assert "Example column" in all_markdown  # Overview: slowest scenarios
    assert "Correct row attribution" in all_markdown  # Failure triage
    assert "Flaky step detection" in all_markdown  # Flaky tests
    assert "Scenario Outline rows are uniquely identifiable" in all_markdown  # Sessions


def test_sessions_scenario_table_shows_example_column(outline_data):
    at = AppTest.from_function(_render_script, kwargs={"data": outline_data})
    at.run(timeout=30)
    assert not at.exception

    frames = [df.value for df in at.dataframe if "Example" in df.value.columns]
    assert frames, "expected at least one dataframe with an Example column"
    examples = set()
    for frame in frames:
        examples |= set(frame["Example"])
    assert "separator=period, food=2.5" in examples
    assert "separator=comma, food=2,5" in examples


def test_failure_triage_distinguishes_outline_examples_by_name(outline_data):
    at = AppTest.from_function(_render_script, kwargs={"data": outline_data})
    at.run(timeout=30)
    assert not at.exception

    all_failures_frames = [
        df.value for df in at.dataframe if {"Scenario", "Example", "Failed step"} <= set(df.value.columns)
    ]
    assert all_failures_frames
    frame = all_failures_frames[0]
    rows = frame[frame["Scenario"] == "Enter bolus amount"]
    assert len(rows) == 1
    assert rows.iloc[0]["Example"] == "separator=comma, food=2,5"
    assert rows.iloc[0]["Failed step"] == "enter value"


def test_legacy_failure_scenarios_fallback_renders_step_and_error(outline_data):
    """Session 2 has no per-scenario ``failure`` object at all — the failure
    detail must still surface via the failedScenarios fallback."""
    at = AppTest.from_function(_render_script, kwargs={"data": outline_data})
    at.run(timeout=30)
    assert not at.exception

    failures = rp.collect_failures(outline_data)
    legacy = next(f for f in failures if f["name"] == "Legacy failure")
    assert legacy["failedStep"] == "legacy step"
    assert legacy["error"] == "legacy error"


@pytest.mark.parametrize("status,icon", [("PASSED", "🟢"), ("FAILED", "🔴"), ("SKIPPED", "🟡")])
def test_status_badge_uses_expected_icon(status, icon):
    assert app.status_badge(status).startswith(icon)


def test_render_report_runs_without_exception_for_test_json():
    """Smoke-tests the maintainer's larger ad-hoc test.json (10+ real/dummy
    sessions covering skipped scenarios, outlines, and the fallback path)."""
    path = Path(__file__).parent.parent / "test.json"
    with open(path, encoding="utf-8") as f:
        data = json.load(f)

    at = AppTest.from_function(_render_script, kwargs={"data": data})
    at.run(timeout=30)
    assert not at.exception

