"""Streamlit dashboard - Toronto shadow mapper.

Watch how building + tree shadows sweep across **Liberty Village / King West** as
the day goes by. Scrub the time slider, or press ▶ Play to animate the day.
"""
from __future__ import annotations

import math
import time
import warnings
from datetime import datetime

warnings.filterwarnings("ignore")

import streamlit as st

import config
from src import data, frames, shadows, viz

st.set_page_config(page_title="Toronto Shadow Mapper", page_icon="☀", layout="wide")

PLAY_DELAY_S = 0.6  # pause between frames while animating


@st.cache_resource(show_spinner=False)
def _buildings():
    """Building footprints for the AOI (cached in memory + on disk)."""
    return data.load_buildings()


@st.cache_resource(show_spinner=False)
def _shade_frame(when_iso: str, include_trees: bool):
    """Combined shade layer for one timestamp (cached in memory + on disk)."""
    return frames.build_frame(
        when=datetime.fromisoformat(when_iso), include_trees=include_trees
    )


def _fmt_time(dt: datetime) -> str:
    return dt.strftime("%-I:%M %p")


# --------------------------------------------------------------------------
st.title("☀ Toronto Shadow Mapper")
st.caption(
    "See how building and tree shadows move across **Liberty Village / King West** "
    "over the course of a day. Pick a date, then scrub the time slider or press "
    "**▶ Play**."
)

with st.sidebar:
    st.subheader("Day")
    the_date = st.date_input("Date", value=config.default_date().date(), key="when_date")

    st.subheader("Shade sources")
    include_trees = st.checkbox("Include tree canopy", value=True, key="trees")
    extruded = st.checkbox("3D buildings", value=True, key="extruded")

    st.subheader("Playback")
    playing = st.toggle("▶ Play the day", value=False, key="playing")

# Timestamps to animate for the chosen day (local Toronto time).
day = datetime(the_date.year, the_date.month, the_date.day, tzinfo=config.TORONTO_TZ)
frame_times = config.day_frames(day)
labels = [_fmt_time(t) for t in frame_times]

# Advance the frame index automatically while playing; otherwise honour the slider.
if "frame_idx" not in st.session_state:
    st.session_state.frame_idx = labels.index(_fmt_time(config.default_date())) if _fmt_time(config.default_date()) in labels else len(labels) // 2

if playing:
    st.session_state.frame_idx = (st.session_state.frame_idx + 1) % len(labels)

chosen_label = st.select_slider(
    "Time of day",
    options=labels,
    value=labels[min(st.session_state.frame_idx, len(labels) - 1)],
    key="time_slider",
)
# Keep the index in sync when the user scrubs manually.
st.session_state.frame_idx = labels.index(chosen_label)
when = frame_times[st.session_state.frame_idx]

# --- Compute + render ------------------------------------------------------
clat, clon = config.CENTER_LATLON
sp = shadows.sun_position(when, clon, clat)
alt_deg = math.degrees(sp["altitude"])
daytime = alt_deg > 0

blds = _buildings()
layers = [viz.buildings_layer(blds, extruded=extruded)]

shade_km2 = 0.0
shade_frac = 0.0
if daytime:
    with st.spinner("Casting shadows…"):
        frame = _shade_frame(when.isoformat(), include_trees)
    layers.insert(1, viz.shade_layer(frame))  # under buildings, over basemap
    shade_km2 = frames.shaded_area_km2(frame)
    shade_frac = frames.shaded_fraction(frame)

c1, c2, c3, c4 = st.columns(4)
c1.metric("Time", _fmt_time(when))
c2.metric("Sun altitude", f"{alt_deg:.0f}°", "night" if not daytime else None)
c3.metric("Shaded area", f"{shade_km2:.2f} km²")
c4.metric("AOI in shade", f"{shade_frac*100:.0f}%")

if not daytime:
    st.info("The sun is below the horizon at this time — the whole neighbourhood is in shade.")

st.pydeck_chart(viz.build_deck(layers, viz.fit_view([])), use_container_width=True)

# Drive the animation loop: re-run after a short pause while playing.
if playing:
    time.sleep(PLAY_DELAY_S)
    st.rerun()
