"""Unit tests for report.py — the pure data-processing layer.

These run against both the committed sample_report.json fixture and small
hand-built dicts, and never touch Streamlit, so they are fast and deterministic.
"""
import json
from pathlib import Path

import pytest

import report as rp

FIXTURE_PATH = Path(__file__).parent / "fixtures" / "sample_report.json"


@pytest.fixture(scope="module")
def data() -> dict:
    with open(FIXTURE_PATH, encoding="utf-8") as f:
        return json.load(f)


# --- fixture integrity -------------------------------------------------------


def test_fixture_is_valid_and_self_consistent(data):
    assert data["totalSessions"] == len(data["sessions"])
    for session in data["sessions"]:
        scenarios = session.get("scenarios", [])
        assert session.get("scenarioCount") == len(scenarios)
        assert (
            session.get("passed", 0) + session.get("failed", 0) + session.get("skipped", 0)
            == len(scenarios)
        )
        assert session.get("failed", 0) == len(session.get("failedScenarios", []))


# --- normalize_status --------------------------------------------------------


@pytest.mark.parametrize(
    "value,expected",
    [("passed", "PASSED"), (" Failed ", "FAILED"), (None, ""), ("", "")],
)
def test_normalize_status(value, expected):
    assert rp.normalize_status(value) == expected


# --- summarize ---------------------------------------------------------------


def test_summarize_matches_manual_totals(data):
    summary = rp.summarize(data)
    assert summary["passed"] == sum(s.get("passed", 0) for s in data["sessions"])
    assert summary["failed"] == sum(s.get("failed", 0) for s in data["sessions"])
    assert summary["total"] == summary["passed"] + summary["failed"] + summary["skipped"]
    assert 0.0 <= summary["passRate"] <= 100.0


def test_summarize_empty_report():
    summary = rp.summarize({"sessions": []})
    assert summary == {
        "sessions": 0,
        "passed": 0,
        "failed": 0,
        "skipped": 0,
        "total": 0,
        "passRate": 0.0,
    }


# --- error_signature ---------------------------------------------------------


def test_error_signature_normalizes_ids_numbers_and_quotes():
    a = 'Error: element ("~foo_button") still not displayed after 5000ms'
    b = 'Error: element ("~bar_widget") still not displayed after 60000ms'
    assert rp.error_signature(a) == rp.error_signature(b)


def test_error_signature_handles_missing():
    assert rp.error_signature(None) == "(no error message)"
    assert rp.error_signature("") == "(no error message)"


def test_error_signature_uses_only_first_line():
    err = "Error: boom\n    at somewhere.js:1:2"
    assert "at somewhere" not in rp.error_signature(err)


# --- collect_failures / cluster_failures -------------------------------------


def test_collect_failures_count_matches_failed_totals(data):
    failures = rp.collect_failures(data)
    expected = sum(s.get("failed", 0) for s in data["sessions"])
    assert len(failures) == expected
    for f in failures:
        assert set(f) >= {"session", "name", "feature", "example", "failedStep", "error", "signature"}


def test_cluster_failures_groups_and_sorts():
    failures = [
        {"signature": "A", "failedStep": "x"},
        {"signature": "A", "failedStep": "y"},
        {"signature": "B", "failedStep": "x"},
    ]
    clusters = rp.cluster_failures(failures, by="signature")
    assert [c["count"] for c in clusters] == [2, 1]
    assert clusters[0]["key"] == "A"

    by_step = rp.cluster_failures(failures, by="step")
    counts = {c["key"]: c["count"] for c in by_step}
    assert counts == {"x": 2, "y": 1}


def test_cluster_failures_total_is_preserved(data):
    failures = rp.collect_failures(data)
    clusters = rp.cluster_failures(failures)
    assert sum(c["count"] for c in clusters) == len(failures)


# --- collect_failures: mixed per-scenario detail / failedScenarios fallback --


def test_collect_failures_falls_back_per_scenario_when_failure_object_missing():
    """A FAILED scenario with no ``failure`` object should pull its step/error
    from the matching ``failedScenarios`` entry (matched by name + example),
    even when other scenarios in the same session DO carry their own ``failure``."""
    data = {
        "sessions": [
            {
                "sessionNumber": 1,
                "scenarios": [
                    {
                        "name": "A",
                        "status": "FAILED",
                        "failure": {"step": "step-a", "error": "error-a"},
                    },
                    {"name": "B", "status": "FAILED"},  # no failure object at all
                ],
                "failedScenarios": [
                    {"name": "B", "failedStep": "step-b", "error": "error-b"},
                ],
            }
        ]
    }
    failures = {f["name"]: f for f in rp.collect_failures(data)}
    assert failures["A"]["failedStep"] == "step-a" and failures["A"]["error"] == "error-a"
    assert failures["B"]["failedStep"] == "step-b" and failures["B"]["error"] == "error-b"


def test_collect_failures_fallback_matches_by_example_params_not_name_alone():
    data = {
        "sessions": [
            {
                "sessionNumber": 1,
                "scenarios": [
                    {"name": "Outline", "status": "FAILED", "exampleParams": {"region": "US"}},
                    {"name": "Outline", "status": "FAILED", "exampleParams": {"region": "EU"}},
                ],
                "failedScenarios": [
                    {"name": "Outline", "exampleParams": {"region": "US"}, "failedStep": "us-step", "error": "us-error"},
                    {"name": "Outline", "exampleParams": {"region": "EU"}, "failedStep": "eu-step", "error": "eu-error"},
                ],
            }
        ]
    }
    failures = rp.collect_failures(data)
    by_example = {f["example"]: f for f in failures}
    assert by_example["region=US"]["failedStep"] == "us-step"
    assert by_example["region=EU"]["failedStep"] == "eu-step"


def test_find_flaky_uses_per_scenario_fallback_too():
    data = {
        "sessions": [
            {
                "sessionNumber": 1,
                "scenarios": [
                    {"name": "A", "status": "PASSED"},
                    {"name": "B", "status": "FAILED"},  # no failure object
                ],
                "failedScenarios": [{"name": "B", "failedStep": "shared-step", "error": "e"}],
            }
        ]
    }
    flaky = rp.find_flaky(data)
    assert len(flaky) == 1
    assert flaky[0]["step"] == "shared-step"


# --- scenario_example --------------------------------------------------------


def test_scenario_example_formats_params():
    scn = {"exampleParams": {"separator": "comma", "food": "2,5"}}
    assert rp.scenario_example(scn) == "separator=comma, food=2,5"


@pytest.mark.parametrize("scn", [{}, {"exampleParams": None}, {"exampleParams": {}}])
def test_scenario_example_empty_when_not_outline(scn):
    assert rp.scenario_example(scn) == ""


# --- find_flaky (per-session, step-level) ------------------------------------


def test_find_flaky_flags_step_when_session_has_passes():
    data = {
        "sessions": [
            {
                "sessionNumber": 1,
                "scenarios": [
                    {"name": "A", "status": "PASSED"},
                    {"name": "B", "status": "FAILED", "failure": {"step": "user logged in"}},
                    {"name": "C", "status": "FAILED", "failure": {"step": "user logged in"}},
                ],
            }
        ]
    }
    flaky = rp.find_flaky(data)
    assert len(flaky) == 1
    row = flaky[0]
    assert row["session"] == 1
    assert row["step"] == "user logged in"
    assert row["failedCount"] == 2
    assert row["passedInSession"] == 1
    assert {s["name"] for s in row["scenarios"]} == {"B", "C"}


def test_find_flaky_ignores_session_without_passes():
    data = {
        "sessions": [
            {
                "sessionNumber": 1,
                "scenarios": [
                    {"name": "B", "status": "FAILED", "failure": {"step": "user logged in"}},
                    {"name": "C", "status": "FAILED", "failure": {"step": "user logged in"}},
                ],
            }
        ]
    }
    assert rp.find_flaky(data) == []


def test_find_flaky_is_per_session_not_cross_session():
    # A step failing in one session but only passing in another is NOT flaky:
    # a fix landed between the two runs must not look like flake.
    data = {
        "sessions": [
            {
                "sessionNumber": 1,
                "scenarios": [{"name": "B", "status": "FAILED", "failure": {"step": "connect"}}],
            },
            {"sessionNumber": 2, "scenarios": [{"name": "B", "status": "PASSED"}]},
        ]
    }
    assert rp.find_flaky(data) == []


def test_find_flaky_separates_outline_examples():
    data = {
        "sessions": [
            {
                "sessionNumber": 1,
                "scenarios": [
                    {"name": "Bolus", "status": "PASSED", "exampleParams": {"sep": "period"}},
                    {
                        "name": "Bolus",
                        "status": "FAILED",
                        "exampleParams": {"sep": "comma"},
                        "failure": {"step": "enter value"},
                    },
                ],
            }
        ]
    }
    flaky = rp.find_flaky(data)
    assert len(flaky) == 1
    assert flaky[0]["scenarios"] == [{"name": "Bolus", "example": "sep=comma"}]


def test_find_flaky_on_fixture_is_per_session_and_shaped(data):
    rows = rp.find_flaky(data)
    assert rows  # fixture sessions 4/5/6 have passes alongside failed steps
    for row in rows:
        assert row["step"]
        assert row["failedCount"] >= 1
        assert row["passedInSession"] >= 1


# --- feature_breakdown / slowest --------------------------------------------


def test_feature_breakdown_totals_match_scenarios(data):
    breakdown = rp.feature_breakdown(data)
    total = sum(r["total"] for r in breakdown)
    assert total == len(rp.flatten_scenarios(data))


def test_slowest_scenarios_is_sorted_desc_and_limited(data):
    slow = rp.slowest_scenarios(data, limit=5)
    assert len(slow) <= 5
    durations = [r["durationMs"] for r in slow]
    assert durations == sorted(durations, reverse=True)


# --- filter_report -----------------------------------------------------------


def test_filter_report_by_status_keeps_only_failed(data):
    view = rp.filter_report(data, statuses=["FAILED"])
    for session in view["sessions"]:
        for scn in session["scenarios"]:
            assert rp.normalize_status(scn.get("status")) == "FAILED"
        assert session["passed"] == 0
        assert session["failed"] == session["scenarioCount"]


def test_filter_report_query_matches_error_text(data):
    view = rp.filter_report(data, query="could not connect to pump")
    matched = rp.flatten_scenarios(view)
    assert matched  # fixture contains a pump connection failure
    assert all("pump" in (r["error"] or "").lower() or "pump" in r["name"].lower() for r in matched)


def test_filter_report_no_filters_is_noop_shaped(data):
    view = rp.filter_report(data)
    assert len(view["sessions"]) == len(data["sessions"])


def test_filter_report_query_matches_example_params():
    data = {
        "sessions": [
            {
                "sessionNumber": 1,
                "scenarios": [
                    {"name": "Outline", "status": "PASSED", "exampleParams": {"region": "US"}},
                    {"name": "Outline", "status": "PASSED", "exampleParams": {"region": "EU"}},
                ],
            }
        ]
    }
    view = rp.filter_report(data, query="region=eu")
    matched = rp.flatten_scenarios(view)
    assert len(matched) == 1
    assert matched[0]["example"] == "region=EU"


# --- exports -----------------------------------------------------------------


def test_failures_to_csv_has_header_and_rows(data):
    import csv
    import io

    failures = rp.collect_failures(data)
    csv_text = rp.failures_to_csv(failures)
    assert csv_text.splitlines()[0] == "session,feature,name,example,failedStep,signature,error"
    # Errors embed newlines, so parse properly rather than counting text lines.
    rows = list(csv.DictReader(io.StringIO(csv_text)))
    assert len(rows) == len(failures)


def test_failures_to_markdown_escapes_pipes():
    md = rp.failures_to_markdown([{"session": 1, "name": "a|b", "error": "x|y"}])
    body = md.splitlines()[-1]
    assert "a\\|b" in body and "x\\|y" in body


def test_failures_to_markdown_empty():
    assert rp.failures_to_markdown([]) == "_No failures._"


def test_build_markdown_summary_contains_key_sections(data):
    md = rp.build_markdown_summary(data)
    assert "# WDIO report summary" in md
    assert "Pass rate:" in md
    assert "## Failures" in md
