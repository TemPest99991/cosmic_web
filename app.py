from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

from analysis.data_sources import DashboardData, load_dashboard_data


st.set_page_config(
    page_title="Cosmic Ray Research Dashboard",
    page_icon=".",
    layout="wide",
    initial_sidebar_state="collapsed",
)


def _format_time(value: datetime | None) -> str:
    if value is None:
        return "No updates yet"
    local_value = value.astimezone()
    return local_value.strftime("%b %d, %Y %I:%M:%S %p").replace(" 0", " ")


def _rate_status(data: DashboardData) -> tuple[str, str]:
    last_update = data.last_update
    if last_update is None:
        return "Offline", "No detector update has been recorded."

    age_seconds = (datetime.now(timezone.utc) - last_update).total_seconds()
    if age_seconds <= 120:
        return "Running", "Detector data is current."
    if age_seconds <= 600:
        return "Delayed", "Detector data is a few minutes behind."
    return "Offline", "No recent detector update."


def _metric_card(label: str, value: str, help_text: str | None = None) -> None:
    st.metric(label=label, value=value, help=help_text)


def render_header(data: DashboardData) -> None:
    status, status_help = _rate_status(data)
    duration = data.recording_duration
    duration_text = "No run"
    if duration is not None:
        hours = duration.total_seconds() / 3600
        duration_text = f"{hours:.2f} hr"

    st.title("Cosmic Ray Research Dashboard")

    status_col, update_col, total_col, duration_col, rate_col = st.columns(5)
    with status_col:
        _metric_card("Detector status", status, status_help)
    with update_col:
        _metric_card("Last update", _format_time(data.last_update))
    with total_col:
        _metric_card("Total triggers", f"{data.total_triggers:,}")
    with duration_col:
        _metric_card("Recording duration", duration_text)
    with rate_col:
        _metric_card("Overall rate", f"{data.overall_rate:.1f} / min")


def render_scintillators(data: DashboardData) -> None:
    st.header("Scintillator Activity")
    current_rates = data.current_rates

    if current_rates.empty:
        st.info("No scintillator-rate data available yet.")
        return

    cols = st.columns(4)
    for idx, row in current_rates.iterrows():
        with cols[idx % 4]:
            st.metric(
                label=row["scintillator"],
                value=f"{row['rate_per_min']:.1f} / min",
                delta=f"{row['total_hits']:,} hits",
            )

    history = data.rate_history.copy()
    history["timestamp"] = pd.to_datetime(history["timestamp"], utc=True)

    chart = px.line(
        history,
        x="timestamp",
        y="rate_per_min",
        color="scintillator",
        markers=True,
        labels={
            "timestamp": "Time",
            "rate_per_min": "Particles per minute",
            "scintillator": "Detector",
        },
    )
    chart.update_layout(legend_title_text="", height=420, margin=dict(l=8, r=8, t=24, b=8))
    st.plotly_chart(chart, width="stretch")


def render_muon_lifetime(data: DashboardData) -> None:
    st.header("Muon Lifetime")
    lifetime = data.muon_lifetime

    if lifetime.empty:
        st.info("No muon-lifetime data available yet.")
        return

    col_chart, col_stats = st.columns([3, 1])
    with col_chart:
        fig = go.Figure()
        fig.add_trace(
            go.Bar(
                x=lifetime["decay_time_us"],
                y=lifetime["counts"],
                name="Measured candidates",
                marker_color="#3b82f6",
            )
        )
        if "fit_counts" in lifetime.columns:
            fig.add_trace(
                go.Scatter(
                    x=lifetime["decay_time_us"],
                    y=lifetime["fit_counts"],
                    name="Exponential fit",
                    mode="lines",
                    line=dict(color="#111827", width=3),
                )
            )
        fig.update_layout(
            xaxis_title="Decay time (microseconds)",
            yaxis_title="Candidates",
            height=430,
            margin=dict(l=8, r=8, t=24, b=8),
        )
        st.plotly_chart(fig, width="stretch")

    with col_stats:
        accepted = int(lifetime["counts"].sum())
        weighted_mean = (lifetime["decay_time_us"] * lifetime["counts"]).sum() / max(
            lifetime["counts"].sum(), 1
        )
        st.metric("Accepted candidates", f"{accepted:,}")
        st.metric("Estimated lifetime", f"{weighted_mean:.2f} us")
        st.metric("Accepted value", "2.20 us")
        st.metric("Difference", f"{weighted_mean - 2.20:+.2f} us")


def render_absorption(data: DashboardData) -> None:
    st.header("Absorption")
    absorption = data.absorption

    if absorption.empty:
        st.info("No absorption data available yet.")
        return

    fig = px.scatter(
        absorption,
        x="thickness_cm",
        y="rate_per_min",
        color="material",
        error_y="uncertainty",
        labels={
            "thickness_cm": "Absorber thickness (cm)",
            "rate_per_min": "Count rate per minute",
            "material": "Material",
        },
    )
    fig.update_traces(marker=dict(size=10))
    fig.update_layout(height=420, margin=dict(l=8, r=8, t=24, b=8))
    st.plotly_chart(fig, width="stretch")


def render_system(data: DashboardData) -> None:
    st.header("System Information")
    cols = st.columns(4)
    with cols[0]:
        st.metric("Data source", data.source_name)
    with cols[1]:
        st.metric("Malformed records", f"{data.malformed_records:,}")
    with cols[2]:
        st.metric("Current log file", data.current_log_file or "Not connected")
    with cols[3]:
        st.metric("Last upload", _format_time(data.last_upload))


def main() -> None:
    refresh_seconds = st.sidebar.slider("Refresh interval", 5, 120, 30)
    st.sidebar.caption("The page refreshes while it is open.")
    st.sidebar.toggle("Use sample data fallback", value=True, key="sample_fallback")

    @st.fragment(run_every=timedelta(seconds=refresh_seconds))
    def dashboard_body() -> None:
        data = load_dashboard_data(use_sample_fallback=st.session_state.sample_fallback)

        render_header(data)
        st.divider()
        render_scintillators(data)
        st.divider()

        left, right = st.columns(2)
        with left:
            render_muon_lifetime(data)
        with right:
            render_absorption(data)

        st.divider()
        render_angular(data)
        st.divider()
        render_system(data)

        st.caption(f"Refreshing every {refresh_seconds} seconds.")

    dashboard_body()


if __name__ == "__main__":
    main()
