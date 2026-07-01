"""Streamlit dashboard - Toronto shadow mapper.

Pick a Toronto neighbourhood and a date, then watch building + tree shadows sweep
across it over the day. The whole day's shade is rendered into a single deck.gl
component that animates client-side, so playback glides without reloading the map.
"""
from __future__ import annotations

import math
import warnings
from datetime import datetime

warnings.filterwarnings("ignore")

import streamlit as st

import config
from src import data, frames, neighbourhoods, shadows, viz

st.set_page_config(page_title="Toronto Shadow Mapper", page_icon="☀", layout="wide")

MAP_HEIGHT = 640

# Smoothness -> minutes between shade frames (finer = smoother shadow motion).
SMOOTHNESS = {"Standard (30 min)": 30, "Smooth (15 min)": 15, "Fine (10 min)": 10}
# Speed -> seconds to play the whole day, independent of how many frames it has.
SPEED_SECONDS = {"Relaxed": 26, "Brisk": 14, "Fast": 7}


@st.cache_data(show_spinner=False)
def _neighbourhood_names():
    return neighbourhoods.list_names()


@st.cache_data(show_spinner="Fetching buildings…")
def _buildings_fc(nb_name: str):
    """Building footprints for a neighbourhood as a GeoJSON FeatureCollection."""
    return viz.buildings_fc(data.load_buildings(neighbourhoods.get(nb_name)))


@st.cache_data(show_spinner="Casting the day's shadows…")
def _day_payload(nb_name: str, date_iso: str, include_trees: bool, step_min: int) -> list[dict]:
    """All daylight shade frames for a neighbourhood-day: label, sun alt, %, geojson."""
    nb = neighbourhoods.get(nb_name)
    day = datetime.fromisoformat(date_iso)
    clat, clon = nb.centroid_latlon()
    out: list[dict] = []
    for when in config.day_frames(day, step_min):
        frame = frames.aoi_clip(frames.build_frame(nb, when=when, include_trees=include_trees), nb)
        alt = math.degrees(shadows.sun_position(when, clon, clat)["altitude"])
        out.append({
            "label": when.strftime("%-I:%M %p"),
            "sun_alt": round(alt),
            "shade_pct": round(min(frames.shaded_fraction(frame, nb), 1.0) * 100),
            "shade": viz.shade_fc(frame),
        })
    return out


# --------------------------------------------------------------------------
st.title("☀ Toronto Shadow Mapper")
st.caption(
    "See how building and tree shadows move across a **Toronto neighbourhood** over "
    "the course of a day. Pick a neighbourhood and date, then use the **slider** or "
    "**▶ Play** in the map — playback is smooth (no reloads), and pan/zoom/tilt stay put."
)

names = _neighbourhood_names()
default_idx = names.index(neighbourhoods.DEFAULT_NEIGHBOURHOOD) if neighbourhoods.DEFAULT_NEIGHBOURHOOD in names else 0

with st.sidebar:
    st.subheader("Where & when")
    nb_name = st.selectbox("Neighbourhood", names, index=default_idx, key="nb")
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

    st.subheader("Animation")
    smoothness = st.select_slider(
        "Smoothness", options=list(SMOOTHNESS), value="Standard (30 min)", key="smooth",
        help="Finer steps move the shadows in smaller time increments (smoother "
             "motion, a bit more to compute the first time).",
    )
    speed = st.select_slider(
        "Speed", options=list(SPEED_SECONDS), value="Brisk", key="speed",
        help="How fast ▶ Play sweeps the whole day.",
    )
    step_min = SMOOTHNESS[smoothness]
    play_seconds = SPEED_SECONDS[speed]

    st.caption(
        "First load of a new neighbourhood/date/smoothness computes its frames "
        "(fast if precomputed; see `scripts/precompute.py`). Playback is then instant."
    )

nb = neighbourhoods.get(nb_name)
day = datetime(the_date.year, the_date.month, the_date.day, tzinfo=config.TORONTO_TZ)
frames_payload = _day_payload(nb_name, day.isoformat(), include_trees, step_min)

if not frames_payload:
    st.info("No daylight at this location on the selected date — nothing to animate.")
else:
    minx, miny, maxx, maxy = nb.bbox()
    view = viz.fit_view([(miny, minx), (maxy, maxx)])
    interval_ms = max(90, round(play_seconds * 1000 / len(frames_payload)))
    html = viz.animated_html(
        buildings=_buildings_fc(nb_name),
        frames=frames_payload,
        view=view,
        extruded=extruded,
        height=MAP_HEIGHT,
        interval_ms=interval_ms,
    )
    st.iframe(html, height=MAP_HEIGHT + 20)
