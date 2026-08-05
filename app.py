import json
import os
import uuid
from datetime import datetime
from pathlib import Path

import pandas as pd
import streamlit as st

import report as rp

st.set_page_config(page_title="WDIO Report Viewer", layout="wide")

STATUS_ICON = {"PASSED": "🟢", "FAILED": "🔴", "SKIPPED": "🟡", "IN-PROGRESS": "🟠", "RUNNING": "🟠"}

# Server-side store for uploaded reports, keyed by an id kept in the URL query
# params. st.session_state doesn't survive a browser reload (it's a fresh
# session), but the URL does, so the query param is what makes the report
# "stick" across a refresh.
_REPORT_STORE = st.cache_resource(lambda: {})()


def status_badge(status: str) -> str:
    status = rp.normalize_status(status)
    return f"{STATUS_ICON.get(status, '⚪️')} {status or 'UNKNOWN'}"


def _info_button(body: str, key: str) -> None:
    """Small ℹ️ popover next to a section header explaining a feature."""
    with st.popover("ℹ️", use_container_width=False):
        st.markdown(body)


def _header_with_info(title: str, body: str, key: str) -> None:
    left, right = st.columns([8, 1])
    left.markdown(title)
    with right:
        _info_button(body, key)


def render_overview(data: dict, summary: dict) -> None:
    cols = st.columns(4)
    cols[0].metric("Total scenarios", summary["total"])
    cols[1].metric("Passed", summary["passed"])
    cols[2].metric("Failed", summary["failed"])
    cols[3].metric("Skipped", summary["skipped"])
    st.progress(min(summary["passRate"] / 100, 1.0), text=f"Pass rate: {summary['passRate']:.1f}%")

    st.markdown("#### Pass / fail per session")
    counts = rp.per_session_counts(data)
    if counts:
        frame = pd.DataFrame(counts).set_index("session")[["passed", "failed", "skipped"]]
        # st.bar_chart maps colors to columns by name sorted alphabetically: failed, passed, skipped.
        st.bar_chart(frame, color=["#e74c3c", "#2ecc71", "#95a5a6"])

    st.markdown("#### By feature file")
    breakdown = rp.feature_breakdown(data)
    if breakdown:
        st.dataframe(
            [
                {
                    "Feature": r["feature"],
                    "Total": r["total"],
                    "🟢": r["passed"],
                    "🔴": r["failed"],
                    "🟡": r["skipped"],
                    "Pass rate": f"{r['passRate']:.0f}%",
                }
                for r in breakdown
            ],
            use_container_width=True,
            hide_index=True,
        )
    else:
        st.caption("No scenarios recorded.")

    _header_with_info(
        "#### Slowest scenarios",
        "**Example column** — Scenario Outline rows share the same name and only "
        "differ by their `exampleParams` (the values from the Examples table), so "
        "this column shows those params to tell otherwise-identical rows apart.\n\n"
        "Example: two rows named *\"Enter bolus amount\"* show "
        "`separator=period, food=2.5` and `separator=comma, food=2,5` here.",
        key="info_slowest",
    )
    slowest = rp.slowest_scenarios(data, 10)
    if slowest:
        st.dataframe(
            [
                {
                    "Status": status_badge(r["status"]),
                    "Duration": r["durationReadable"],
                    "Scenario": r["name"],
                    "Example": r.get("example") or "—",
                    "Feature": r["feature"],
                    "Session": r["session"],
                }
                for r in slowest
            ],
            use_container_width=True,
            hide_index=True,
        )
    else:
        st.caption("No timing data available.")

    st.download_button(
        "⬇︎ Download summary (Markdown)",
        rp.build_markdown_summary(data),
        file_name="wdio-summary.md",
        mime="text/markdown",
    )


def render_failures(data: dict) -> None:
    failures = rp.collect_failures(data)
    if not failures:
        st.success("No failed scenarios. 🎉")
        return

    st.markdown(f"**{len(failures)} failed scenario(s)** across all sessions.")
    _info_button(
        "**Correct row attribution** — each failure is read straight off the exact "
        "scenario object that failed, not matched back by name. So when a Scenario "
        "Outline runs the same name multiple times (once per Examples row), a "
        "failure always lands on the right row instead of a name collision "
        "attaching it to the wrong one. The **Example** column shows the "
        "`exampleParams` of that exact row.\n\nExample: *\"Enter bolus amount\"* fails "
        "only for `separator=comma, food=2,5` while `separator=period, food=2.5` passes "
        "— both show up as the same name but different Example values.",
        key="info_failures",
    )
    group_label = st.radio("Group by", ["Error signature", "Failed step"], horizontal=True)
    by = "signature" if group_label == "Error signature" else "step"

    for cluster in rp.cluster_failures(failures, by=by):
        with st.expander(f"×{cluster['count']} — {cluster['key']}", expanded=False):
            st.dataframe(
                [
                    {
                        "Session": f["session"],
                        "Scenario": f["name"],
                        "Example": f.get("example") or "—",
                        "Feature": f["feature"],
                        "Failed step": f.get("failedStep") or "—",
                    }
                    for f in cluster["failures"]
                ],
                use_container_width=True,
                hide_index=True,
            )
            example = next((f["error"] for f in cluster["failures"] if f.get("error")), None)
            if example:
                st.caption("Example error")
                st.code(example, language="text")

    st.markdown("#### All failures")
    st.dataframe(
        [
            {
                "Session": f["session"],
                "Feature": f["feature"],
                "Scenario": f["name"],
                "Example": f.get("example") or "—",
                "Failed step": f.get("failedStep") or "—",
                "Error": (f["error"] or "").splitlines()[0] if f.get("error") else "",
            }
            for f in failures
        ],
        use_container_width=True,
        hide_index=True,
    )

    downloads = st.columns(2)
    downloads[0].download_button(
        "⬇︎ Failures (CSV)", rp.failures_to_csv(failures), file_name="wdio-failures.csv", mime="text/csv"
    )
    downloads[1].download_button(
        "⬇︎ Failures (Markdown)",
        rp.failures_to_markdown(failures),
        file_name="wdio-failures.md",
        mime="text/markdown",
    )


def render_flaky(data: dict) -> None:
    _info_button(
        "**Flaky step detection (per session)** — a step is flagged when it makes "
        "some scenarios fail while other scenarios in the *same* session pass — "
        "evidence the step can work, so the failure is likely transient (network, "
        "a slow device) rather than a real defect. Flakiness is never inferred "
        "across sessions, since a fix landed between two runs would look identical "
        "to flake.\n\nExample: in session 4, the step `the user is logged into a "
        "Connected Health Account` fails on 8 scenarios while 25 other scenarios "
        "in that same session pass — flagged as flaky rather than a real defect.",
        key="info_flaky",
    )
    st.caption(
        "Flaky steps are detected **per session**: a step that makes some "
        "scenarios fail while other scenarios in the same session pass — evidence "
        "the step can work, so the failure is likely transient (network, a slow "
        "device) rather than a real defect. The report doesn't store steps for "
        "passed scenarios, so “passed elsewhere” is approximated by the session "
        "having passing scenarios. Flakiness is never inferred across sessions — a "
        "fix landed between runs would look the same."
    )
    flaky = rp.find_flaky(data)
    if not flaky:
        st.success("No flaky steps detected. 🎉")
        return

    st.dataframe(
        [
            {
                "Session": r["session"],
                "Flaky step": r["step"],
                "🔴 Scenarios failed": r["failedCount"],
                "🟢 Passed in session": r["passedInSession"],
            }
            for r in flaky
        ],
        use_container_width=True,
        hide_index=True,
    )

    for r in flaky:
        with st.expander(
            f"Session {r['session']} · {r['step']} · 🔴 {r['failedCount']} failed", expanded=False
        ):
            st.dataframe(
                [
                    {"Scenario": s["name"], "Example": s.get("example") or "—"}
                    for s in r["scenarios"]
                ],
                use_container_width=True,
                hide_index=True,
            )


def render_failed_steps(session: dict) -> None:
    steps = [
        (rp._scenario_failure(scn).get("step"), rp._scenario_failure(scn).get("error"))
        for scn in session.get("scenarios") or []
        if scn
        and rp.normalize_status(scn.get("status")) == "FAILED"
        and rp._scenario_failure(scn).get("step")
    ]
    if not steps:
        steps = [
            (fs.get("failedStep"), fs.get("error"))
            for fs in session.get("failedScenarios") or []
            if fs and fs.get("failedStep")
        ]
    if not steps:
        return
    st.markdown("**Failed steps**")
    for step, error in steps:
        st.markdown(f"- 🔴 `{step}`")
        if error:
            st.code(error, language="text")


def render_scenarios(session: dict) -> None:
    scenarios = session.get("scenarios") or []
    if not scenarios:
        st.caption("No scenarios recorded.")
        return

    progress = rp.session_scenario_progress(session)
    current_index = progress.get("scenarioIndex") if progress else None
    session_status = rp.normalize_status(session.get("status"))
    is_running_session = session_status in {"IN-PROGRESS", "RUNNING"}
    has_progress_fields = any(
        isinstance(scn, dict) and ("scenarioIndex" in scn or "scenariosLeft" in scn)
        for scn in scenarios
    )

    st.markdown(f"**Scenarios ({len(scenarios)})**")
    _info_button(
        "**Scenario Outline rows are uniquely identifiable** — each row carries its "
        "own `exampleParams` (the Examples-table values for that run), shown here "
        "as the **Example** column. The scenario `name` also embeds these params "
        "(e.g. `... [region=US, bg_entered=600]`), so otherwise-identical rows are "
        "distinguishable both in the raw JSON and in this table.\n\nExample: three "
        "rows all named *\"Enter bolus amount\"* show `region=US`, `region=EU`, "
        "`region=UK` here — one per Examples row.",
        key=f"info_scenarios_{session.get('sessionNumber', '?')}",
    )
    rows = []
    for scn in scenarios:
        row = {
            "Status": status_badge(scn.get("status")),
            "Duration": scn.get("durationReadable", "—"),
            "Start": scn.get("startTime", "—"),
            "Scenario": scn.get("name", "(unnamed)"),
            "Example": rp.scenario_example(scn) or "—",
            "Tags": ", ".join(rp.scenario_tags(scn)) or "—",
            "Feature": scn.get("featureFile", ""),
        }
        if has_progress_fields:
            scn_index = rp._non_negative_int(scn.get("scenarioIndex"))
            row["Scenario #"] = scn_index if scn_index is not None else "—"
            scn_left = rp._non_negative_int(scn.get("scenariosLeft"))
            row["Scenarios left"] = scn_left if scn_left is not None else "—"
            row["State"] = "Running" if is_running_session and scn_index == current_index else "—"
        rows.append(row)

    st.dataframe(rows, use_container_width=True, hide_index=True)

    failures = [scn for scn in scenarios if isinstance(scn.get("failure"), dict)]
    if failures:
        st.markdown("**Scenario failures**")
        for scn in failures:
            st.markdown(f"🔴 {scn.get('name', '(unnamed)')}")
            st.json(scn.get("failure"), expanded=False)


def render_session(session: dict) -> None:
    num = session.get("sessionNumber", "?")
    passed = session.get("passed", 0)
    failed = session.get("failed", 0)
    skipped = session.get("skipped", 0)
    progress = rp.session_scenario_progress(session)
    progress_bits = []
    if progress:
        if progress.get("scenarioIndex") is not None:
            progress_bits.append(f"running #{progress['scenarioIndex']}")
        if progress.get("scenariosLeft") is not None:
            progress_bits.append(f"{progress['scenariosLeft']} left")

    title = (
        f"Session {num} · {status_badge(session.get('status'))} · "
        f"🟢 {passed}  🔴 {failed}  🟡 {skipped} · {session.get('durationReadable', '—')}"
    )
    if progress_bits:
        title += " · " + " · ".join(progress_bits)

    with st.expander(title, expanded=False):
        metrics = [
            ("Scenarios", session.get("scenarioCount", 0)),
            ("Passed", passed),
            ("Failed", failed),
            ("Skipped", skipped),
        ]
        if progress and progress.get("scenariosLeft") is not None:
            metrics.append(("Left", progress["scenariosLeft"]))

        cols = st.columns(len(metrics))
        for col, (label, value) in zip(cols, metrics):
            col.metric(label, value)

        status = rp.normalize_status(session.get("status"))
        is_running_session = status in {"IN-PROGRESS", "RUNNING"}
        if is_running_session and progress:
            index = progress.get("scenarioIndex")
            left = progress.get("scenariosLeft")
            total = progress.get("totalScenarios")
            if index is not None and left is not None and total:
                done = max(total - left, 0)
                st.progress(min(done / total, 1.0), text=f"Running scenario {index} of {total} ({left} left)")
            elif index is not None and left is not None:
                st.info(f"Running scenario {index} ({left} left)")
            elif index is not None:
                st.info(f"Running scenario {index}")
            elif left is not None:
                st.info(f"{left} scenario(s) left")

        failed_tags = rp.session_failed_tags(session)
        if failed_tags:
            st.markdown("**Failing tags**")
            st.caption(
                ", ".join(failed_tags)
            )

        times = st.columns(2)
        times[0].markdown(f"**Start**  \n{session.get('startTime', '—')}")
        times[1].markdown(f"**End**  \n{session.get('endTime', '—')}")

        env = session.get("environment")
        if env and st.toggle("Show environment", key=f"env_{num}"):
            st.json(env, expanded=False)

        render_failed_steps(session)
        render_scenarios(session)


def render_sessions(data: dict) -> None:
    all_features = sorted({scn.get("featureFile") or "" for _, scn in rp.iter_scenarios(data)} - {""})
    all_days = rp.available_days(data)

    filters = st.columns([2, 2, 2, 3])
    statuses = filters[0].multiselect("Status", list(rp.VALID_STATUSES), key="flt_status")
    features = filters[1].multiselect("Feature file", all_features, key="flt_feature")
    days = filters[2].multiselect("Day", all_days, key="flt_day")
    query = filters[3].text_input("Search (name / feature / tags / error)", key="flt_query")

    filters_active = bool(statuses or features or days or query.strip())
    view = (
        rp.filter_report(data, statuses=statuses, features=features, days=days, query=query)
        if filters_active
        else data
    )

    shown = 0
    for session in view.get("sessions", []):
        if filters_active and session.get("scenarioCount", 0) == 0:
            continue
        render_session(session)
        shown += 1

    if shown == 0:
        st.caption("No sessions match the current filters.")


def render_by_day(data: dict) -> None:
    days = rp.available_days(data)
    if not days:
        st.caption("No dated scenarios to break down by day.")
        return

    _header_with_info(
        "#### Per-day breakdown",
        "**Day comes from each scenario's `startTime`** (falling back to its "
        "session's start time), normalized to a single `DD Mon YYYY` spelling so "
        "runs that cross midnight or span several days still group correctly. A "
        "single report can cover more than one day when sessions are re-run over "
        "time.",
        key="info_by_day",
    )

    breakdown = rp.day_breakdown(data)
    st.dataframe(
        [
            {
                "Day": r["day"],
                "Total": r["total"],
                "🟢": r["passed"],
                "🔴": r["failed"],
                "🟡": r["skipped"],
                "Pass rate": f"{r['passRate']:.0f}%",
            }
            for r in breakdown
        ],
        use_container_width=True,
        hide_index=True,
    )

    frame = pd.DataFrame(breakdown).set_index("day")[["passed", "failed", "skipped"]]
    # st.bar_chart maps colors to columns sorted alphabetically: failed, passed, skipped.
    st.bar_chart(frame, color=["#e74c3c", "#2ecc71", "#95a5a6"])

    st.download_button(
        "⬇︎ Day breakdown (CSV)",
        rp.day_breakdown_to_csv(breakdown),
        file_name="wdio-day-breakdown.csv",
        mime="text/csv",
    )

    st.markdown("#### Scenarios by day")
    selected = st.multiselect("Filter days", days, default=days, key="byday_days")
    if not selected:
        st.caption("Select at least one day to see scenarios.")
        return

    rows = rp.flatten_scenarios(rp.filter_report(data, days=selected))
    if not rows:
        st.caption("No scenarios on the selected day(s).")
        return

    st.dataframe(
        [
            {
                "Status": status_badge(r["status"]),
                "Day": r["day"],
                "Start": r["start"],
                "Duration": r["durationReadable"],
                "Scenario": r["name"],
                "Example": r["example"] or "—",
                "Feature": r["feature"],
                "Session": r["session"],
            }
            for r in rows
        ],
        use_container_width=True,
        hide_index=True,
    )
    st.download_button(
        "⬇︎ Scenarios for selected day(s) (CSV)",
        rp.scenarios_to_csv(rows),
        file_name="wdio-scenarios-by-day.csv",
        mime="text/csv",
    )


def render_report(data: dict) -> None:
    st.title("WDIO Test Report")

    summary = rp.summarize(data)
    top = st.columns(3)
    top[0].metric("Developer", data.get("developer", "—"))
    top[1].metric("Sessions", summary["sessions"])
    top[2].metric("Last updated", data.get("lastUpdated", "—"))

    overview, byday, failures, flaky, sessions = st.tabs(
        ["Overview", "By day", "Failure triage", "Flaky tests", "Sessions"]
    )
    with overview:
        render_overview(data, summary)
    with byday:
        render_by_day(data)
    with failures:
        render_failures(data)
    with flaky:
        render_flaky(data)
    with sessions:
        render_sessions(data)


# --- Live / watch mode -------------------------------------------------------
#
# By default the app is upload-only and in-memory. When the operator sets
# REPORT_FILE or REPORT_DIR (e.g. in docker-compose), the app instead *watches*
# that path and re-reads it on a timer so the UI tracks a report a test run
# keeps rewriting. Only these operator-set env vars can turn on disk reads — the
# browser UI never accepts a raw filesystem path, so a visitor on the network
# can't use it to read arbitrary files off the host.

WATCH_FILE_ENV = "REPORT_FILE"
WATCH_DIR_ENV = "REPORT_DIR"
# Refresh cadence options. Kept deliberately on the calm side (minutes, with one
# 30s option for the impatient) — a live report doesn't need second-by-second
# polling, and slower refreshes are gentler on large reports.
REFRESH_INTERVALS = {
    "30s": 30,
    "1 min": 60,
    "2 min": 120,
    "5 min": 300,
    "10 min": 600,
    "15 min": 900,
    "30 min": 1800,
}
_DEFAULT_INTERVAL = "1 min"
_LAST_GOOD_KEY = "_last_good_report"


def _watch_file() -> Path | None:
    """Operator-configured single report file to watch (``REPORT_FILE``), if any."""
    value = os.environ.get(WATCH_FILE_ENV, "").strip()
    return Path(value).expanduser() if value else None


def _watch_dir() -> Path | None:
    """Operator-configured folder of reports to watch (``REPORT_DIR``), if a dir."""
    value = os.environ.get(WATCH_DIR_ENV, "").strip()
    if not value:
        return None
    path = Path(value).expanduser()
    return path if path.is_dir() else None


def _list_reports(directory: Path) -> list[Path]:
    """JSON files directly inside ``directory``, newest (by mtime) first."""
    try:
        files = [p for p in directory.glob("*.json") if p.is_file()]
    except OSError:
        return []
    files.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    return files


def _load_report_file(path: Path) -> dict:
    """Parse a report JSON from disk. Raises on missing/invalid; callers handle."""
    with open(path, encoding="utf-8") as handle:
        return json.load(handle)


def _auto_refresh_controls(default_on: bool) -> tuple[bool, int]:
    """Sidebar auto-refresh toggle + interval + a manual 'Refresh now' button."""
    auto = st.sidebar.toggle("Auto-refresh", value=default_on, key="auto_refresh")
    label = st.sidebar.select_slider(
        "Refresh every",
        options=list(REFRESH_INTERVALS),
        value=_DEFAULT_INTERVAL,
        key="refresh_interval",
        disabled=not auto,
    )
    if st.sidebar.button("🔄 Refresh now", use_container_width=True):
        st.rerun()
    return auto, REFRESH_INTERVALS[label]


def _render_live(source: Path | None) -> None:
    """Read ``source`` and render the report, tolerating a mid-write file.

    A report being rewritten by a running test suite can be caught half-written,
    so a JSON decode error falls back to the last good render instead of
    crashing — the next refresh picks up the finished file.
    """
    if source is None:
        st.info("Waiting for a report JSON to appear… drop one into the watched folder.")
        return

    checked = datetime.now().strftime("%H:%M:%S")
    stale = False
    try:
        data = _load_report_file(source)
        st.session_state[_LAST_GOOD_KEY] = data
    except FileNotFoundError:
        st.info(f"Waiting for `{source.name}` to appear…")
        return
    except (json.JSONDecodeError, OSError):
        data = st.session_state.get(_LAST_GOOD_KEY)
        if data is None:
            st.warning("The report file isn't valid JSON yet (it may be mid-write). Retrying…")
            return
        stale = True

    try:
        updated = datetime.fromtimestamp(source.stat().st_mtime).strftime("%d %b %Y, %H:%M:%S")
    except OSError:
        updated = "—"

    note = f"🟢 Live · `{source.name}` · file updated {updated} · checked {checked}"
    if stale:
        note += " · ⚠️ last read hit a mid-write file, showing last good data"
    st.caption(note)

    render_report(data)


def _run_watch_mode(watch_file: Path | None, watch_dir: Path | None) -> None:
    st.sidebar.header("Live report")
    st.sidebar.caption(
        "The report is read **live from its file on disk** — nothing is uploaded "
        "or copied. It re-reads in place as your test run rewrites the file."
    )

    pinned: Path | None = None
    if watch_file is not None:
        st.sidebar.caption(
            f"Tracking `{watch_file.name}` live from its location on disk — "
            "it updates as your test run rewrites the file."
        )
    elif watch_dir is not None:
        reports = _list_reports(watch_dir)
        names = [p.name for p in reports]
        if names:
            options = ["Newest"] + names
            if st.session_state.get("watch_choice") not in options:
                st.session_state["watch_choice"] = "Newest"
            choice = st.sidebar.selectbox("File to track", options, key="watch_choice")
            st.sidebar.caption(
                f"{len(names)} report(s) in the folder — switch anytime; the one you "
                "pick is live-tracked. “Newest” always follows the most recent."
            )
            if choice != "Newest":
                pinned = watch_dir / choice
            # A quick at-a-glance list of every report available to switch to.
            picker = st.sidebar.expander(f"All reports ({len(names)})", expanded=False)
            active = pinned if pinned is not None else (reports[0] if reports else None)
            for p in reports:
                try:
                    mtime = datetime.fromtimestamp(p.stat().st_mtime).strftime("%d %b %H:%M")
                except OSError:
                    mtime = "—"
                marker = "▶ " if (active is not None and p == active) else ""
                picker.markdown(f"{marker}`{p.name}` · updated {mtime}")
        else:
            st.sidebar.info("No report JSONs in the folder yet.")

    auto, interval = _auto_refresh_controls(default_on=True)

    def resolve() -> Path | None:
        if watch_file is not None:
            return watch_file
        if pinned is not None:
            return pinned
        newest = _list_reports(watch_dir) if watch_dir is not None else []
        return newest[0] if newest else None

    if auto:

        @st.fragment(run_every=interval)
        def _live() -> None:
            _render_live(resolve())

        _live()
    else:
        _render_live(resolve())


def _run_upload_mode() -> None:
    st.sidebar.header("Report input")
    uploaded = st.sidebar.file_uploader("Upload a report JSON", type=["json"])
    report_id = st.query_params.get("rid")

    if uploaded is not None:
        try:
            data = json.load(uploaded)
        except json.JSONDecodeError as exc:
            st.error(f"Invalid JSON: {exc}")
            return
        report_id = uuid.uuid4().hex
        _REPORT_STORE[report_id] = data
        st.query_params["rid"] = report_id
    elif report_id and report_id in _REPORT_STORE:
        data = _REPORT_STORE[report_id]
    else:
        st.info("Upload a WDIO execution report JSON from the sidebar to begin.")
        return

    if st.sidebar.button("Clear report"):
        _REPORT_STORE.pop(report_id, None)
        st.query_params.pop("rid", None)
        st.rerun()

    render_report(data)


def main() -> None:
    watch_file = _watch_file()
    watch_dir = _watch_dir()
    if watch_file is not None or watch_dir is not None:
        _run_watch_mode(watch_file, watch_dir)
    else:
        _run_upload_mode()


if __name__ == "__main__":
    main()
