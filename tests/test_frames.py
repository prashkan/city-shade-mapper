"""Unit tests for the day-frame timeline (no network access required)."""
from datetime import datetime

import config


def _day() -> datetime:
    return datetime(2024, 6, 21, tzinfo=config.TORONTO_TZ)


def test_frames_span_configured_hours():
    frames = config.day_frames(_day())
    assert frames[0].hour == config.FRAME_START_HOUR
    assert frames[0].minute == 0
    assert frames[-1].hour == config.FRAME_END_HOUR


def test_frames_are_evenly_stepped():
    frames = config.day_frames(_day())
    deltas = {
        int((b - a).total_seconds() // 60) for a, b in zip(frames, frames[1:])
    }
    assert deltas == {config.FRAME_STEP_MINUTES}


def test_frames_carry_toronto_tz():
    frames = config.day_frames(_day())
    assert all(f.tzinfo is not None for f in frames)


def test_frame_count_matches_range():
    frames = config.day_frames(_day())
    span_min = (config.FRAME_END_HOUR - config.FRAME_START_HOUR) * 60
    assert len(frames) == span_min // config.FRAME_STEP_MINUTES + 1
