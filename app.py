"""Streamlit dashboard - Toronto shadow mapper.

Watch how building + tree shadows sweep across **Liberty Village / King West** as
the day goes by. The whole day's shade is rendered into a single deck.gl
component that animates **client-side**, so pressing Play glides the shadows
without reloading the map.
"""
from __future__ import annotations

import math
import warnings
from datetime import datetime

warnings.filterwarnings("ignore")

import streamlit as st

import config
from src import data, frames, shadows, viz

st.set_page_config(page_title="Toronto Shadow Mapper", page_icon="☀", layout="wide")

MAP_HEIGHT = 640


@st.cache_data(show_spinner=False)
def _buildings_fc():
    """Building footprints as a GeoJSON FeatureCollection (built once)."""
    return viz.buildings_fc(data.load_buildings())


@st.cache_data(show_spinner="Casting the day's shadows…")
def _day_payload(date_iso: str, include_trees: bool) -> list[dict]:
    """All daylight shade frames for a day: label, sun altitude, shade %, geojson."""
    day = datetime.fromisoformat(date_iso)
    clat, clon = config.CENTER_LATLON
    out: list[dict] = []
    for when in config.day_frames(day):
        frame = frames.aoi_clip(frames.build_frame(when=when, include_trees=include_trees))
        alt = math.degrees(shadows.sun_position(when, clon, clat)["altitude"])
        out.append({
            "label": when.strftime("%-I:%M %p"),
            "sun_alt": round(alt),
            "shade_pct": round(min(frames.shaded_fraction(frame), 1.0) * 100),
            "shade": viz.shade_fc(frame),
        })
    return out


# --------------------------------------------------------------------------
st.title("☀ Toronto Shadow Mapper")
st.caption(
    "See how building and tree shadows move across **Liberty Village / King West** "
    "over the course of a day. Pick a date, then use the **slider** or **▶ Play** "
    "in the map — playback is smooth (no reloads), and pan/zoom/tilt stay put."
)

with st.sidebar:
    st.subheader("Day")
    the_date = st.date_input("Date", value=config.default_date().date(), key="when_date")

    st.subheader("Shade sources")
    mode = st.radio(
        "Cast shadows from",
        ["Buildings + trees", "Buildings only"],
        index=0,
        key="mode",
        help="Buildings only is lighter and faster; add trees for canopy shade.",
    )
    include_trees = mode == "Buildings + trees"
    extruded = st.checkbox("3D buildings", value=True, key="extruded")

    st.caption(
        "First load of a new date computes that day's frames (fast if precomputed; "
        "see `scripts/precompute.py`). Playback afterwards is instant."
    )

day = datetime(the_date.year, the_date.month, the_date.day, tzinfo=config.TORONTO_TZ)
frames_payload = _day_payload(day.isoformat(), include_trees)

if not frames_payload:
    st.info("No daylight at this location on the selected date — nothing to animate.")
else:
    html = viz.animated_html(
        buildings=_buildings_fc(),
        frames=frames_payload,
        view=viz.fit_view([]),
        extruded=extruded,
        height=MAP_HEIGHT,
    )
    st.iframe(html, height=MAP_HEIGHT + 20)
