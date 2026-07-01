"""Unit tests for solar-bucket cache keying (no network required)."""
from datetime import datetime

import config


def _w(s: str) -> datetime:
    return datetime.fromisoformat(s).replace(tzinfo=config.TORONTO_TZ)


def test_same_week_dates_share_one_bucket():
    rep_a = config.solar_bucket(_w("2026-06-22T13:00"))
    rep_b = config.solar_bucket(_w("2026-06-24T13:00"))
    assert rep_a == rep_b


def test_bucket_keeps_hour_and_tzinfo():
    rep = config.solar_bucket(_w("2026-06-22T09:00"))
    assert rep.hour == 9
    assert rep.tzinfo is not None


def test_bucket_is_idempotent():
    w = _w("2026-03-23T16:00")
    once = config.solar_bucket(w)
    assert config.solar_bucket(once) == once


def test_distant_dates_differ():
    rep_summer = config.solar_bucket(_w("2026-06-22T13:00"))
    rep_winter = config.solar_bucket(_w("2026-12-22T13:00"))
    assert rep_summer != rep_winter
