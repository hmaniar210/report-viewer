"""Pure data-processing helpers for WDIO execution reports.

No Streamlit imports live here on purpose: everything in this module is a pure
function of the parsed report dict. That keeps the analysis logic fast to
unit-test without spinning up a Streamlit script runtime, and keeps app.py
focused on rendering.
"""
from __future__ import annotations

import csv
import io
import re
from collections import defaultdict
from typing import Any, Iterable, Iterator

VALID_STATUSES = ("PASSED", "FAILED", "SKIPPED")


def normalize_status(status: Any) -> str:
    """Uppercase, whitespace-trimmed status string (``""`` when missing)."""
    return (status or "").strip().upper()


def iter_scenarios(data: dict) -> Iterator[tuple[dict, dict]]:
    """Yield ``(session, scenario)`` for every scenario in the report."""
    for session in data.get("sessions", []) or []:
        for scenario in session.get("scenarios", []) or []:
            if scenario:
                yield session, scenario


def _scenario_failure(scenario: dict) -> dict:
    failure = scenario.get("failure")
    return failure if isinstance(failure, dict) else {}


def scenario_example(scenario: dict) -> str:
    """Readable label for a scenario-outline example (``""`` when not an outline).

    A scenario outline runs the same steps once per Examples row; those runs
    share a name and differ only by ``exampleParams``. Formatting the params
    (e.g. ``separator=comma, food=2,5``) makes each example identifiable so a
    passing row and a failing row of the same outline aren't confused.
    """
    params = scenario.get("exampleParams")
    if not isinstance(params, dict) or not params:
        return ""
    return ", ".join(f"{key}={value}" for key, value in params.items())


def flatten_scenarios(data: dict) -> list[dict]:
    """Flatten all scenarios into flat rows carrying their session context."""
    rows: list[dict] = []
    for session, scn in iter_scenarios(data):
        failure = _scenario_failure(scn)
        rows.append(
            {
                "session": session.get("sessionNumber"),
                "status": normalize_status(scn.get("status")),
                "name": scn.get("name") or "(unnamed)",
                "example": scenario_example(scn),
                "feature": scn.get("featureFile") or "",
                "durationMs": scn.get("durationMs") or 0,
                "durationReadable": scn.get("durationReadable") or "—",
                "start": scn.get("startTime") or "—",
                "failedStep": failure.get("step"),
                "error": failure.get("error"),
            }
        )
    return rows


def summarize(data: dict) -> dict:
    """Overall totals and pass rate across every session."""
    sessions = data.get("sessions", []) or []
    passed = sum(s.get("passed", 0) for s in sessions)
    failed = sum(s.get("failed", 0) for s in sessions)
    skipped = sum(s.get("skipped", 0) for s in sessions)
    total = passed + failed + skipped
    return {
        "sessions": len(sessions),
        "passed": passed,
        "failed": failed,
        "skipped": skipped,
        "total": total,
        "passRate": (passed / total * 100) if total else 0.0,
    }


def per_session_counts(data: dict) -> list[dict]:
    """Per-session passed/failed/skipped counts (for charting)."""
    return [
        {
            "session": s.get("sessionNumber"),
            "passed": s.get("passed", 0),
            "failed": s.get("failed", 0),
            "skipped": s.get("skipped", 0),
        }
        for s in data.get("sessions", []) or []
    ]


_HEX = re.compile(r"\b[0-9a-fA-F]{8}-[0-9a-fA-F-]{20,}\b|0x[0-9a-fA-F]+")
_QUOTED = re.compile(r"\"[^\"]*\"|'[^']*'|`[^`]*`")
_NUM = re.compile(r"\d+")


def error_signature(error: str | None) -> str:
    """Reduce a raw error string to a stable signature.

    Errors that differ only by ids, numbers, timeouts or quoted selectors are
    collapsed to the same signature so they cluster together during triage.
    """
    if not error:
        return "(no error message)"
    first = error.strip().splitlines()[0]
    first = _HEX.sub("<id>", first)
    first = _QUOTED.sub("<arg>", first)
    first = _NUM.sub("<n>", first)
    return re.sub(r"\s+", " ", first).strip()


def _scenario_key(scenario: dict) -> tuple[Any, tuple | None]:
    """Identity for matching a ``scenarios`` entry to its ``failedScenarios`` twin.

    Different report producers have shipped different amounts of detail per
    scenario over time, so a FAILED scenario doesn't always carry its own
    ``failure`` object — sometimes the detail only exists in the session's
    ``failedScenarios`` list. Matching by name alone would misattribute a
    Scenario Outline row, so ``exampleParams`` (when present) is part of the key.
    """
    params = scenario.get("exampleParams")
    key_params = tuple(sorted(params.items())) if isinstance(params, dict) else None
    return (scenario.get("name"), key_params)


def _failed_scenarios_lookup(session: dict) -> dict[tuple, dict]:
    return {_scenario_key(fs): fs for fs in session.get("failedScenarios") or [] if fs}


def _failure_detail(scenario: dict, fallback_lookup: dict[tuple, dict]) -> tuple[Any, Any]:
    """``(step, error)`` for a failed scenario, falling back to its matching
    ``failedScenarios`` entry when the scenario has no ``failure`` object."""
    failure = _scenario_failure(scenario)
    step, error = failure.get("step"), failure.get("error")
    if not step and not error:
        fallback = fallback_lookup.get(_scenario_key(scenario))
        if fallback:
            step, error = fallback.get("failedStep"), fallback.get("error")
    return step, error


def collect_failures(data: dict) -> list[dict]:
    """Every failed scenario across the report as flat, exportable rows.

    Failures are read from each session's ``scenarios`` (status ``FAILED``),
    falling back to the matching ``failedScenarios`` entry (by name + example
    params) when a scenario carries no ``failure`` object of its own. A session
    that has no ``scenarios`` at all falls back to ``failedScenarios`` wholesale.
    """
    failures: list[dict] = []
    for session in data.get("sessions", []) or []:
        num = session.get("sessionNumber")
        failed_scn = [
            s
            for s in session.get("scenarios") or []
            if s and normalize_status(s.get("status")) == "FAILED"
        ]
        if failed_scn:
            fallback_lookup = _failed_scenarios_lookup(session)
            for scn in failed_scn:
                step, error = _failure_detail(scn, fallback_lookup)
                failures.append(_failure_row(num, scn.get("name"), scn.get("featureFile"), scenario_example(scn), step, error))
        else:
            for fs in session.get("failedScenarios") or []:
                if fs:
                    failures.append(_failure_row(num, fs.get("name"), fs.get("featureFile"), scenario_example(fs), fs.get("failedStep"), fs.get("error")))
    return failures


def _failure_row(session: Any, name: Any, feature: Any, example: Any, step: Any, error: Any) -> dict:
    return {
        "session": session,
        "name": name or "(unnamed)",
        "feature": feature or "",
        "example": example or "",
        "failedStep": step,
        "error": error,
        "signature": error_signature(error),
    }


def cluster_failures(failures: Iterable[dict], by: str = "signature") -> list[dict]:
    """Group failures by ``signature`` or ``step``, largest cluster first."""
    key = "signature" if by == "signature" else "failedStep"
    fallback = "(no error message)" if by == "signature" else "(unknown step)"
    groups: dict[str, list[dict]] = defaultdict(list)
    for failure in failures:
        groups[failure.get(key) or fallback].append(failure)
    clusters = [{"key": k, "count": len(v), "failures": v} for k, v in groups.items()]
    clusters.sort(key=lambda c: c["count"], reverse=True)
    return clusters


def _session_failed_steps(session: dict) -> list[tuple[str | None, dict]]:
    """``(failed step, {name, example})`` for each failed scenario in a session.

    Steps come from the scenarios' ``failure.step``, falling back per-scenario to
    the matching ``failedScenarios`` entry when a scenario has no ``failure`` of
    its own. A session with no ``scenarios`` at all falls back to ``failedScenarios``
    wholesale.
    """
    failed = [
        s
        for s in session.get("scenarios") or []
        if s and normalize_status(s.get("status")) == "FAILED"
    ]
    if failed:
        fallback_lookup = _failed_scenarios_lookup(session)
        return [
            (
                _failure_detail(scn, fallback_lookup)[0],
                {"name": scn.get("name") or "(unnamed)", "example": scenario_example(scn)},
            )
            for scn in failed
        ]
    return [
        (
            fs.get("failedStep"),
            {"name": fs.get("name") or "(unnamed)", "example": scenario_example(fs)},
        )
        for fs in session.get("failedScenarios") or []
        if fs
    ]


def find_flaky(data: dict) -> list[dict]:
    """Per-session flaky steps — a step that failed while the session still passed.

    A step is treated as flaky within a *single* session when it makes some
    scenarios fail while that same session has passing scenarios: evidence the
    step can work, so the failure is likely transient (a bad network, a slow
    device) rather than a real defect. The report never records the steps of a
    passed scenario, so "the step passed elsewhere" is approximated by the
    session having any passing scenario. Flakiness is deliberately never inferred
    across sessions — a fix landed between two runs would look identical to flake.

    Each row is ``{session, step, failedCount, passedInSession, scenarios}`` where
    ``scenarios`` lists the failing scenarios (name + example params), sorted
    with the most-failed step first.
    """
    results: list[dict] = []
    for session in data.get("sessions", []) or []:
        scenarios = [s for s in session.get("scenarios") or [] if s]
        passed = sum(1 for s in scenarios if normalize_status(s.get("status")) == "PASSED")
        if passed == 0:
            continue  # nothing passed → no evidence the step ever worked here
        by_step: dict[str, list[dict]] = defaultdict(list)
        for step, scn in _session_failed_steps(session):
            if step:
                by_step[step].append(scn)
        for step, failed_scenarios in by_step.items():
            results.append(
                {
                    "session": session.get("sessionNumber"),
                    "step": step,
                    "failedCount": len(failed_scenarios),
                    "passedInSession": passed,
                    "scenarios": failed_scenarios,
                }
            )
    results.sort(key=lambda r: (r["failedCount"], r["passedInSession"]), reverse=True)
    return results


def feature_breakdown(data: dict) -> list[dict]:
    """Passed/failed/skipped counts and pass rate per feature file."""
    stats: dict[str, dict] = defaultdict(lambda: {"passed": 0, "failed": 0, "skipped": 0})
    for _, scn in iter_scenarios(data):
        status = normalize_status(scn.get("status"))
        if status in VALID_STATUSES:
            stats[scn.get("featureFile") or "(unknown)"][status.lower()] += 1
    rows = []
    for feature, rec in sorted(stats.items()):
        total = rec["passed"] + rec["failed"] + rec["skipped"]
        rows.append(
            {
                "feature": feature,
                "total": total,
                **rec,
                "passRate": (rec["passed"] / total * 100) if total else 0.0,
            }
        )
    return rows


def slowest_scenarios(data: dict, limit: int = 10) -> list[dict]:
    """The ``limit`` scenarios with the longest ``durationMs``."""
    rows = [r for r in flatten_scenarios(data) if r["durationMs"]]
    rows.sort(key=lambda r: r["durationMs"], reverse=True)
    return rows[:limit]


def _matches_query(scenario: dict, query: str) -> bool:
    haystack = " ".join(
        [
            scenario.get("name") or "",
            scenario.get("featureFile") or "",
            scenario_example(scenario),
            _scenario_failure(scenario).get("error") or "",
        ]
    ).lower()
    return query in haystack


def filter_report(
    data: dict,
    *,
    statuses: Iterable[str] | None = None,
    features: Iterable[str] | None = None,
    sessions: Iterable[Any] | None = None,
    query: str | None = None,
) -> dict:
    """Return a copy of ``data`` keeping only scenarios matching the filters.

    Per-session counts (``passed``/``failed``/``skipped``/``scenarioCount``) are
    recomputed so downstream rendering stays consistent.
    """
    status_set = {normalize_status(s) for s in statuses} if statuses else None
    feature_set = set(features) if features else None
    session_set = set(sessions) if sessions else None
    q = (query or "").strip().lower()

    out_sessions = []
    for session in data.get("sessions", []) or []:
        if session_set is not None and session.get("sessionNumber") not in session_set:
            continue
        kept = []
        for scn in session.get("scenarios") or []:
            if not scn:
                continue
            if status_set is not None and normalize_status(scn.get("status")) not in status_set:
                continue
            if feature_set is not None and (scn.get("featureFile") or "") not in feature_set:
                continue
            if q and not _matches_query(scn, q):
                continue
            kept.append(scn)

        new_session = dict(session)
        new_session["scenarios"] = kept
        new_session["scenarioCount"] = len(kept)
        new_session["passed"] = sum(1 for s in kept if normalize_status(s.get("status")) == "PASSED")
        new_session["failed"] = sum(1 for s in kept if normalize_status(s.get("status")) == "FAILED")
        new_session["skipped"] = sum(1 for s in kept if normalize_status(s.get("status")) == "SKIPPED")
        out_sessions.append(new_session)

    out = dict(data)
    out["sessions"] = out_sessions
    out["totalSessions"] = len(out_sessions)
    return out


FAILURE_COLUMNS = ("session", "feature", "name", "example", "failedStep", "signature", "error")


def failures_to_csv(failures: Iterable[dict]) -> str:
    """Failures as CSV text, ready for ``st.download_button``."""
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=FAILURE_COLUMNS, extrasaction="ignore")
    writer.writeheader()
    for failure in failures:
        writer.writerow({column: (failure.get(column) or "") for column in FAILURE_COLUMNS})
    return buffer.getvalue()


def _md_cell(value: Any) -> str:
    text = "" if value is None else str(value)
    return text.splitlines()[0].replace("|", "\\|") if text else ""


def failures_to_markdown(failures: Iterable[dict]) -> str:
    """Failures as a Markdown table, ready to paste into a bug report."""
    failures = list(failures)
    if not failures:
        return "_No failures._"
    lines = [
        "| Session | Feature | Scenario | Example | Failed step | Error |",
        "|---|---|---|---|---|---|",
    ]
    for f in failures:
        lines.append(
            f"| {_md_cell(f.get('session'))} | {_md_cell(f.get('feature'))} | "
            f"{_md_cell(f.get('name'))} | {_md_cell(f.get('example'))} | "
            f"{_md_cell(f.get('failedStep'))} | {_md_cell(f.get('error'))} |"
        )
    return "\n".join(lines)


def build_markdown_summary(data: dict) -> str:
    """A full copy-paste-ready Markdown summary of the whole report."""
    s = summarize(data)
    parts = ["# WDIO report summary", ""]
    if data.get("developer"):
        parts.append(f"- Developer: {data['developer']}")
    if data.get("lastUpdated"):
        parts.append(f"- Last updated: {data['lastUpdated']}")
    parts += [
        f"- Sessions: {s['sessions']}",
        f"- Total scenarios: {s['total']}",
        f"- Passed: {s['passed']} · Failed: {s['failed']} · Skipped: {s['skipped']}",
        f"- Pass rate: {s['passRate']:.1f}%",
    ]

    flaky = find_flaky(data)
    if flaky:
        parts += ["", f"## Flaky steps ({len(flaky)})"]
        parts += [
            f"- Session {r['session']}: `{r['step']}` — failed {r['failedCount']}× "
            f"({r['passedInSession']} scenario(s) passed in the session)"
            for r in flaky
        ]

    failures = collect_failures(data)
    if failures:
        parts += ["", f"## Failures ({len(failures)})", failures_to_markdown(failures)]

    return "\n".join(parts)
