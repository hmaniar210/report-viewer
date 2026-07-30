import json

import pandas as pd
import streamlit as st

import report as rp

st.set_page_config(page_title="WDIO Report Viewer", layout="wide")

STATUS_ICON = {"PASSED": "🟢", "FAILED": "🔴", "SKIPPED": "🟡"}


def status_badge(status: str) -> str:
    status = rp.normalize_status(status)
    return f"{STATUS_ICON.get(status, '⚪️')} {status or 'UNKNOWN'}"


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
        st.bar_chart(frame, color=["#2ecc71", "#e74c3c", "#f1c40f"])

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

    st.markdown("#### Slowest scenarios")
    slowest = rp.slowest_scenarios(data, 10)
    if slowest:
        st.dataframe(
            [
                {
                    "Status": status_badge(r["status"]),
                    "Duration": r["durationReadable"],
                    "Scenario": r["name"],
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
    group_label = st.radio("Group by", ["Error signature", "Failed step"], horizontal=True)
    by = "signature" if group_label == "Error signature" else "step"

    for cluster in rp.cluster_failures(failures, by=by):
        with st.expander(f"×{cluster['count']} — {cluster['key']}", expanded=False):
            st.dataframe(
                [
                    {
                        "Session": f["session"],
                        "Scenario": f["name"],
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
    st.caption(
        "Scenarios that both passed and failed across the report — likely flaky "
        "or environment-dependent, and worth re-running before filing a bug."
    )
    flaky = rp.find_flaky(data)
    if not flaky:
        st.success("No flaky scenarios detected. 🎉")
        return
    st.dataframe(
        [
            {
                "Scenario": r["name"],
                "🟢 Passed": r["passed"],
                "🔴 Failed": r["failed"],
                "Feature": r["feature"],
            }
            for r in flaky
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
    st.dataframe(
        [
            {
                "Status": status_badge(scn.get("status")),
                "Duration": scn.get("durationReadable", "—"),
                "Start": scn.get("startTime", "—"),
                "Scenario": scn.get("name", "(unnamed)"),
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

    if uploaded is None:
        st.info("Upload a WDIO execution report JSON from the sidebar to begin.")
        return

    try:
        data = json.load(uploaded)
    except json.JSONDecodeError as exc:
        st.error(f"Invalid JSON: {exc}")
        return

    render_report(data)


if __name__ == "__main__":
    main()
