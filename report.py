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


def collect_failures(data: dict) -> list[dict]:
    """Every failed scenario across the report as flat, exportable rows.

    Failures are read from each session's ``scenarios`` (status ``FAILED``);
    a session that only carries ``failedScenarios`` falls back to those.
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
            for scn in failed_scn:
                failure = _scenario_failure(scn)
                failures.append(_failure_row(num, scn.get("name"), scn.get("featureFile"), failure.get("step"), failure.get("error")))
        else:
            for fs in session.get("failedScenarios") or []:
                if fs:
                    failures.append(_failure_row(num, fs.get("name"), fs.get("featureFile"), fs.get("failedStep"), fs.get("error")))
    return failures


def _failure_row(session: Any, name: Any, feature: Any, step: Any, error: Any) -> dict:
    return {
        "session": session,
        "name": name or "(unnamed)",
        "feature": feature or "",
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


def find_flaky(data: dict) -> list[dict]:
    """Scenarios (by name) that both passed and failed — likely flaky."""
    stats: dict[str, dict] = defaultdict(lambda: {"passed": 0, "failed": 0, "feature": ""})
    for _, scn in iter_scenarios(data):
        rec = stats[scn.get("name") or "(unnamed)"]
        status = normalize_status(scn.get("status"))
        if status == "PASSED":
            rec["passed"] += 1
        elif status == "FAILED":
            rec["failed"] += 1
        if not rec["feature"]:
            rec["feature"] = scn.get("featureFile") or ""
    flaky = [
        {"name": name, "passed": rec["passed"], "failed": rec["failed"], "feature": rec["feature"]}
        for name, rec in stats.items()
        if rec["passed"] > 0 and rec["failed"] > 0
    ]
    flaky.sort(key=lambda r: r["failed"], reverse=True)
    return flaky


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


FAILURE_COLUMNS = ("session", "feature", "name", "failedStep", "signature", "error")


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
    lines = ["| Session | Feature | Scenario | Failed step | Error |", "|---|---|---|---|---|"]
    for f in failures:
        lines.append(
            f"| {_md_cell(f.get('session'))} | {_md_cell(f.get('feature'))} | "
            f"{_md_cell(f.get('name'))} | {_md_cell(f.get('failedStep'))} | {_md_cell(f.get('error'))} |"
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
        parts += ["", f"## Flaky scenarios ({len(flaky)})"]
        parts += [f"- {r['name']} — {r['passed']}✓ / {r['failed']}✗" for r in flaky]

    failures = collect_failures(data)
    if failures:
        parts += ["", f"## Failures ({len(failures)})", failures_to_markdown(failures)]

    return "\n".join(parts)
