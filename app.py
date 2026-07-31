import json
import uuid

import pandas as pd
import streamlit as st

import report as rp

st.set_page_config(page_title="WDIO Report Viewer", layout="wide")

STATUS_ICON = {"PASSED": "🟢", "FAILED": "🔴", "SKIPPED": "🟡"}

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
    st.dataframe(
        [
            {
                "Status": status_badge(scn.get("status")),
                "Duration": scn.get("durationReadable", "—"),
                "Start": scn.get("startTime", "—"),
                "Scenario": scn.get("name", "(unnamed)"),
                "Example": rp.scenario_example(scn) or "—",
                "Feature": scn.get("featureFile", ""),
            }
            for scn in scenarios
        ],
        use_container_width=True,
        hide_index=True,
    )

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
    title = (
        f"Session {num} · {status_badge(session.get('status'))} · "
        f"🟢 {passed}  🔴 {failed}  🟡 {skipped} · {session.get('durationReadable', '—')}"
    )
    with st.expander(title, expanded=False):
        cols = st.columns(4)
        cols[0].metric("Scenarios", session.get("scenarioCount", 0))
        cols[1].metric("Passed", passed)
        cols[2].metric("Failed", failed)
        cols[3].metric("Skipped", skipped)

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

    filters = st.columns([2, 2, 3])
    statuses = filters[0].multiselect("Status", list(rp.VALID_STATUSES), key="flt_status")
    features = filters[1].multiselect("Feature file", all_features, key="flt_feature")
    query = filters[2].text_input("Search (name / feature / error)", key="flt_query")

    filters_active = bool(statuses or features or query.strip())
    view = (
        rp.filter_report(data, statuses=statuses, features=features, query=query)
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


def render_report(data: dict) -> None:
    st.title("WDIO Test Report")

    summary = rp.summarize(data)
    top = st.columns(3)
    top[0].metric("Developer", data.get("developer", "—"))
    top[1].metric("Sessions", summary["sessions"])
    top[2].metric("Last updated", data.get("lastUpdated", "—"))

    overview, failures, flaky, sessions = st.tabs(
        ["Overview", "Failure triage", "Flaky tests", "Sessions"]
    )
    with overview:
        render_overview(data, summary)
    with failures:
        render_failures(data)
    with flaky:
        render_flaky(data)
    with sessions:
        render_sessions(data)


def main() -> None:
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


if __name__ == "__main__":
    main()
