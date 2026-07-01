"""Unit tests for the day-frame timeline (no network access required).

Frames are clamped to that day's actual sunrise..sunset, so counts vary by
season; these tests assert the invariants that must always hold.
"""
from datetime import datetime

import config


def _summer() -> datetime:
    return datetime(2024, 6, 21, tzinfo=config.TORONTO_TZ)


def _winter() -> datetime:
    return datetime(2024, 12, 21, tzinfo=config.TORONTO_TZ)


def test_frames_are_evenly_stepped():
    frames = config.day_frames(_summer())
    deltas = {
        int((b - a).total_seconds() // 60) for a, b in zip(frames, frames[1:])
    }
    assert deltas == {config.FRAME_STEP_MINUTES}


def test_frames_carry_toronto_tz():
    frames = config.day_frames(_summer())
    assert all(f.tzinfo is not None for f in frames)


def test_frames_are_within_daylight():
    day = _summer()
    window = config.sun_times(day)
    assert window is not None
    sunrise, sunset = window
    frames = config.day_frames(day)
    assert frames, "expected at least one daylight frame"
    assert frames[0] >= sunrise
    assert frames[-1] <= sunset


def test_summer_day_is_longer_than_winter():
    # Toronto: the summer solstice has far more daylight than the winter one,
    # so it must yield strictly more frames.
    assert len(config.day_frames(_summer())) > len(config.day_frames(_winter()))
