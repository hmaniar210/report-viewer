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

