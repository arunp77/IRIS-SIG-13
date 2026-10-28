"""Dashboard for the staging table: where the plans are, how far they have come, and what is missing.

    python tasks.py ui          (or: streamlit run ui/app.py)

It only reads iris_staging.signal_ingest (and the run report); it never writes. Load data first with
`python tasks.py reproduce` (755 plans) or `python tasks.py demo` (12 plans).
"""

import json

import altair as alt
import psycopg
import pydeck
import streamlit as st
from plans import (
    ROOT,
    STAGE_HEX,
    STAGES,
    database_url,
    filter_plans,
    load_plans,
    load_report,
    missing_fields,
    per_year,
    signal_counts,
    stage_counts,
    unmatched_by_word,
)

st.set_page_config(page_title="B-Plan portal MV", page_icon=":material/map:", layout="wide")


@st.cache_data(ttl="5m", max_entries=2)
def plans_from_database(url):
    return load_plans(url)


@st.cache_data
def state_outline():
    return json.loads((ROOT / "data" / "reference" / "mv_outline.geojson").read_text(encoding="utf-8"))


def bars(frame, label, description):
    """Horizontal bars, largest first, with room for long labels (st.bar_chart cuts them off)."""
    chart = (
        alt.Chart(frame)
        .mark_bar()
        .encode(
            x=alt.X("plans:Q", title=None),
            y=alt.Y(f"{label}:N", sort="-x", title=None, axis=alt.Axis(labelLimit=320, labelOverlap=False)),
            tooltip=[label, "plans"],
        )
        .properties(height=max(60, 26 * len(frame)))
    )
    st.altair_chart(chart, alt=description)


STAGE_SCALE = alt.Scale(domain=list(STAGE_HEX), range=list(STAGE_HEX.values()))
STAGE_COLOUR = alt.Color("stage:N", scale=STAGE_SCALE, legend=None)
STAGE_COLOUR_WITH_LEGEND = alt.Color("stage:N", scale=STAGE_SCALE, legend=alt.Legend(orient="top", title=None))


def stage_bars(counts, description, height=None):
    """Horizontal bars per label, split by stage in the colours of the map, with the total at the end of each bar."""
    labels = list(counts["label"].drop_duplicates())
    room = alt.Scale(domain=[0, counts["total"].max() * 1.12], nice=False)  # space for the number after the longest bar
    axis = alt.Y("label:N", sort=labels, title=None, axis=alt.Axis(labelLimit=320, labelOverlap=False, ticks=False))
    bars_layer = (
        alt.Chart(counts)
        .mark_bar()
        .encode(
            x=alt.X("plans:Q", title=None, axis=alt.Axis(grid=True, tickMinStep=1), scale=room),
            y=axis,
            color=STAGE_COLOUR,
            tooltip=[alt.Tooltip("label:N", title="Label"), "stage", "plans"],
        )
    )
    totals = (
        alt.Chart(counts.drop_duplicates("label"))
        .mark_text(align="left", dx=4, fontSize=11)
        .encode(x=alt.X("total:Q", scale=room), y=axis, text="total:Q")
    )
    st.altair_chart((bars_layer + totals).properties(height=height or max(80, 28 * len(labels))), alt=description)


def plain_bars(frame, label, description, height):
    """Horizontal bars in one colour with the number at the end of each bar."""
    labels = list(frame[label])
    room = alt.Scale(domain=[0, frame["plans"].max() * 1.12], nice=False)
    axis = alt.Y(f"{label}:N", sort=labels, title=None, axis=alt.Axis(labelLimit=320, labelOverlap=False, ticks=False))
    base = alt.Chart(frame)
    bars_layer = base.mark_bar(color="#5b7c99").encode(
        x=alt.X("plans:Q", title=None, axis=alt.Axis(tickMinStep=1), scale=room), y=axis, tooltip=[label, "plans"]
    )
    numbers = base.mark_text(align="left", dx=4, fontSize=11).encode(
        x=alt.X("plans:Q", scale=room), y=axis, text="plans:Q"
    )
    st.altair_chart((bars_layer + numbers).properties(height=height), alt=description)


st.title("Planning portal Mecklenburg-Vorpommern")
st.caption(
    "Plans about solar acreage, grid landing points and peatland restoration, as loaded into "
    "`iris_staging.signal_ingest`. Points are stored in EPSG:25832 (metres) and converted to latitude/longitude "
    "only for the map."
)

url = database_url()
if not url:
    st.error("Set DATABASE_URL in .env (see .env.example), then reload.", title="No database configured")
    st.stop()
try:
    plans = plans_from_database(url)
except psycopg.Error as exc:
    st.error(
        f"{type(exc).__name__}. Start the database with `python tasks.py setup` "
        "and load data with `python tasks.py reproduce`.",
        title="Database not reachable",
    )
    st.stop()
if plans.empty:
    st.warning("The table is empty. Run `python tasks.py reproduce` (or `demo`) first.", title="No plans loaded yet")
    st.stop()

with st.sidebar:
    st.header("Filters")
    text = st.text_input("Title contains", placeholder="for example Agri-PV")
    districts = st.multiselect("District", sorted(plans["district"].dropna().unique()), placeholder="All districts")
    stages = st.pills("Stage", STAGES, selection_mode="multi", default=STAGES)
    st.caption(
        ":green[●] in force (incl. effective) · :orange[●] in procedure · :gray[●] ended. "
        "The stage is the portal's `Rechtsstand`, grouped into three."
    )

shown = filter_plans(plans, text, districts, stages or [])
gaps = missing_fields(shown).set_index("field")["plans"]

with st.container(horizontal=True):
    st.metric(f"Plans shown (of {len(plans)})", f"{len(shown)}", border=True)
    st.metric("Municipalities", f"{shown['municipality'].nunique()}", border=True)
    st.metric(
        "Without publication date",
        f"{gaps['publication date']}",
        border=True,
        help="The portal has no such date; see the date basis chart",
    )
    st.metric("Without documents", f"{gaps['documents']}", border=True)
    st.metric("Location unresolved", f"{gaps['location']}", border=True)

if shown.empty:
    st.info("No plan matches the filters.")
    st.stop()

map_tab, charts_tab, table, report_tab = st.tabs(
    [
        ":material/map: Map",
        ":material/bar_chart: Charts",
        ":material/table: Plans",
        ":material/fact_check: Run report",
    ]
)

with map_tab:
    located = shown.dropna(subset=["lon", "lat"])
    with st.container(border=True):
        st.subheader("Where the plans are")
        deck = pydeck.Deck(
            layers=[
                pydeck.Layer(
                    "GeoJsonLayer",
                    state_outline(),
                    stroked=True,
                    filled=False,
                    get_line_color=[90, 90, 100],
                    line_width_min_pixels=2,
                ),
                pydeck.Layer(
                    "ScatterplotLayer",
                    located[["lon", "lat", "colour", "title", "municipality", "legal_status"]],
                    get_position=["lon", "lat"],
                    get_fill_color="colour",
                    get_radius=500,
                    radius_min_pixels=3,
                    radius_max_pixels=9,
                    opacity=0.8,
                    pickable=True,
                ),
            ],
            initial_view_state=pydeck.ViewState(latitude=53.85, longitude=12.45, zoom=6.8),
            map_style="light",
            tooltip={"text": "{title}\n{municipality} · {legal_status}"},
        )
        st.pydeck_chart(
            deck, height=600, alt=f"Map of {len(located)} planning records inside the outline of Mecklenburg-Vorpommern"
        )
        st.caption(
            "Hover a point for the plan. Colours are the stage; the dark line is the state outline (Natural Earth)."
        )

with charts_tab:
    with st.container(border=True):
        st.subheader("Plans per year")
        yearly = per_year(shown)
        if yearly.empty:
            st.info("None of the shown plans has a date.")
        else:
            timeline = (
                alt.Chart(yearly)
                .mark_bar()
                .encode(
                    x=alt.X("year:O", title=None, axis=alt.Axis(labelAngle=0, labelOverlap="greedy")),
                    y=alt.Y("plans:Q", title=None, axis=alt.Axis(tickMinStep=1)),
                    color=STAGE_COLOUR_WITH_LEGEND,
                    order=alt.Order("stage:N", sort="descending"),
                    tooltip=["year", "stage", "plans"],
                )
                .properties(height=260)
            )
            st.altair_chart(timeline, alt="Number of plans per year of the stand-in date, split by stage")
        st.caption(
            "The portal has no publication date: the year is that of the first date on the plan's detail page "
            "(see the date chart below). Years without plans are shown as empty. The current year is not complete."
        )

    left, right = st.columns(2)
    with left, st.container(border=True):
        st.subheader("Plans per district")
        stage_bars(stage_counts(shown, "district"), "Number of plans per district, split by stage")
    with right, st.container(border=True):
        st.subheader("Largest municipalities")
        stage_bars(stage_counts(shown, "municipality", top=10), "Municipalities with the most plans, split by stage")

    left, right = st.columns(2)
    with left, st.container(border=True):
        st.subheader("Legal status")
        stage_bars(stage_counts(shown, "legal_status"), "Number of plans per legal status, coloured by stage")
    with right:
        with st.container(border=True):
            st.subheader("Kind of plan")
            stage_bars(stage_counts(shown, "plan_type"), "Number of plans per kind of plan, split by stage")
        with st.container(border=True):
            st.subheader("Date used as publication date")
            by_basis = shown["date_basis"].value_counts().rename_axis("basis").reset_index(name="plans")
            plain_bars(by_basis, "basis", "Number of plans per date basis", height=max(80, 28 * len(by_basis)))

with table:
    st.dataframe(
        shown[
            [
                "title",
                "municipality",
                "district",
                "legal_status",
                "plan_type",
                "year",
                "documents",
                "easting",
                "northing",
                "source_url",
            ]
        ],
        hide_index=True,
        height=560,
        column_config={
            "title": st.column_config.TextColumn("Title", width="large"),
            "year": st.column_config.NumberColumn("Year", format="%d"),
            "easting": st.column_config.NumberColumn("Easting (25832)", format="%.0f"),
            "northing": st.column_config.NumberColumn("Northing (25832)", format="%.0f"),
            "source_url": st.column_config.LinkColumn("Portal", display_text="open"),
        },
        alt="Planning records matching the filters, with a link to the portal page of each",
    )
    st.download_button(
        "Download shown plans as CSV",
        shown.drop(columns=["colour", "document_urls"]).to_csv(index=False).encode("utf-8"),
        file_name="plans.csv",
        mime="text/csv",
        icon=":material/download:",
    )

with report_tab:
    report, report_file = load_report()
    st.subheader("Signals")
    signals = signal_counts(plans)
    left, right = st.columns([2, 3])
    with left:
        bars(signals, "signal", "Plans loaded per signal type")
    with right:
        empty = [row.signal.replace("_", " ") for row in signals.itertuples() if row.plans == 0]
        if empty:
            st.info(
                "No plans were loaded for this signal type, so there is nothing to show. "
                "Plans that matched only a weak word are listed below.",
                title="Empty: " + ", ".join(empty),
            )
        else:
            st.success("Every configured signal type has plans.")
    if report is None:
        st.warning("Run `python tasks.py reproduce` to create out/full_run.json.", title="No run report found")
    else:
        st.caption(f"Report: `{report_file}`")
        with st.container(horizontal=True):
            st.metric("Records seen", report["records_seen"], border=True)
            st.metric("Distinct candidates", report["candidates"], border=True)
            st.metric(
                "Loaded",
                report["load"]["inserted"] + report["load"]["updated"] + report["load"]["unchanged"],
                border=True,
            )
            st.metric("Not matched", len(report["unmatched"]), border=True)
            st.metric("Rejected (bad location)", len(report["rejected"]), border=True)
            st.metric("Failed detail pages", len(report["detail_errors"]), border=True)
        unmatched = unmatched_by_word(report)
        st.subheader("Found by the search but not loaded")
        if unmatched.empty:
            st.success("Nothing was left out.")
        else:
            st.dataframe(
                unmatched.groupby("word").size().rename("plans").reset_index(),
                hide_index=True,
                alt="Unmatched plans per weak word",
            )
            st.dataframe(
                unmatched[["word", "title"]],
                hide_index=True,
                height=300,
                alt="Titles of the plans that were not loaded",
            )
        st.subheader("Missing fields in the shown plans")
        bars(
            missing_fields(shown).rename(columns={"field": "missing"}),
            "missing",
            "Number of shown plans lacking each optional field",
        )
