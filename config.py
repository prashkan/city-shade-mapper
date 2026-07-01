"""Central configuration for the Toronto Shade Mapper.

A single source of truth for the study area, coordinate systems, building-height
imputation, and solar-bucket caching, shared by the shade engine and the
Streamlit UI.

Pilot area: **Liberty Village / King West, Toronto, Canada**.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

# --- Study area ------------------------------------------------------------
# Pilot location. We define the area of interest by centre + radius and download
# building footprints with osmnx `features_from_point`.
PLACE_NAME: str = "Liberty Village / King West, Toronto, Ontario, Canada"

# Centre between Liberty Village and King West Village, WGS84 lat/lon. Chosen so
# the square AOI covers both neighbourhoods (Liberty Village to the SW, King West
# to the NE).
CENTER_LATLON: tuple[float, float] = (43.6415, -79.4110)
# Half-extent of the square AOI, in metres.
RADIUS_M: float = 1300.0


def aoi_bbox() -> tuple[float, float, float, float]:
    """AOI bounding box (minx, miny, maxx, maxy) in WGS84, derived from
    CENTER_LATLON +/- RADIUS_M (small-angle approximation)."""
    import math

    lat, lon = CENTER_LATLON
    dlat = RADIUS_M / 111_320.0
    dlon = RADIUS_M / (111_320.0 * math.cos(math.radians(lat)))
    return (lon - dlon, lat - dlat, lon + dlon, lat + dlat)


# --- Coordinate reference systems -----------------------------------------
# Geographic CRS used by OSM / deck.gl rendering.
CRS_WGS84: str = "EPSG:4326"
# Projected metric CRS for Toronto (UTM Zone 17N). ALL length / area / overlay
# math MUST happen in this CRS. Never compute .length or .area in degrees.
CRS_METRIC: str = "EPSG:32617"

# --- Building height imputation -------------------------------------------
# Used when OSM lacks explicit `height` / `building:levels` tags.
METERS_PER_LEVEL: float = 3.5
DEFAULT_BUILDING_HEIGHT_M: float = 4.0

# --- Tree canopy (Meta/WRI 1m global canopy height) -----------------------
# Heights at or above this threshold (metres) are treated as shade-casting
# tree canopy.
CANOPY_MIN_HEIGHT_M: float = 3.0

# --- Solar / time ----------------------------------------------------------
TORONTO_TZ = ZoneInfo("America/Toronto")

# The sun's position at a given clock time changes only ~0.3 deg/day, so we cache
# shade per ~weekly solar bucket + timestamp rather than per exact date. One
# precompute then serves the whole bucket. Each bucket is represented by its
# midpoint day-of-year, keeping the worst-case sun error small.
SOLAR_BUCKET_DAYS: int = 7

# Default animation range (local clock hours) used as a fallback when sunrise/
# sunset can't be computed. Normally the timeline is clamped to actual daylight.
FRAME_START_HOUR: int = 6
FRAME_END_HOUR: int = 21
FRAME_STEP_MINUTES: int = 30


def solar_bucket(when: datetime) -> datetime:
    """Snap ``when`` to the representative timestamp of its solar bucket.

    Keeps the hour/minute and timezone; replaces the date with the midpoint day
    of its ``SOLAR_BUCKET_DAYS``-day bucket within the same year. Idempotent.
    """
    doy = when.timetuple().tm_yday  # 1-based day of year
    bucket = (doy - 1) // SOLAR_BUCKET_DAYS
    rep_doy = bucket * SOLAR_BUCKET_DAYS + 1 + SOLAR_BUCKET_DAYS // 2
    year_start = when.replace(
        month=1, day=1, hour=when.hour, minute=when.minute, second=0, microsecond=0
    )
    return year_start + timedelta(days=rep_doy - 1)


def default_date() -> datetime:
    """Reference day for the visualisation: summer-solstice week, local noon.

    A long, high-sun day makes the shadow sweep most legible.
    """
    return datetime(2024, 6, 21, 12, 0, 0, tzinfo=TORONTO_TZ)


def sun_times(day: datetime) -> tuple[datetime, datetime] | None:
    """Local (sunrise, sunset) for ``day`` at the AOI centre, or None.

    Returns ``None`` if suncalc is unavailable or the sun never rises/sets that
    day (polar edge cases), so callers can fall back to a fixed hour window.
    """
    try:
        import pandas as pd
        from suncalc import get_times
    except Exception:  # pragma: no cover
        return None

    lat, lon = CENTER_LATLON
    noon = day.replace(hour=12, minute=0, second=0, microsecond=0)
    ts = pd.Timestamp(noon)
    ts = ts.tz_convert("UTC") if ts.tzinfo else ts.tz_localize("UTC")
    t = get_times(ts.to_pydatetime(), lon, lat)
    sr, ss = t.get("sunrise"), t.get("sunset")
    if sr is None or ss is None or pd.isna(sr) or pd.isna(ss):
        return None
    sunrise = pd.Timestamp(sr).tz_localize("UTC").tz_convert(TORONTO_TZ).floor("s")
    sunset = pd.Timestamp(ss).tz_localize("UTC").tz_convert(TORONTO_TZ).floor("s")
    return sunrise.to_pydatetime(), sunset.to_pydatetime()


def day_frames(day: datetime) -> list[datetime]:
    """Daylight timestamps to render for ``day`` (local), stepped by
    FRAME_STEP_MINUTES.

    Night is skipped — there is no shade when the sun is down — so the timeline
    is clamped to that day's actual sunrise..sunset (which shortens automatically
    in winter and lengthens in summer). Falls back to FRAME_START_HOUR..
    FRAME_END_HOUR if sunrise/sunset can't be computed.
    """
    window = sun_times(day)
    if window is not None:
        sunrise, sunset = window
        lo, hi = sunrise, sunset
    else:
        lo = day.replace(hour=FRAME_START_HOUR, minute=0, second=0, microsecond=0)
        hi = day.replace(hour=FRAME_END_HOUR, minute=0, second=0, microsecond=0)

    step = timedelta(minutes=FRAME_STEP_MINUTES)
    frames: list[datetime] = []
    cursor = day.replace(hour=0, minute=0, second=0, microsecond=0)
    end = cursor + timedelta(days=1)
    while cursor < end:
        if lo <= cursor <= hi:
            frames.append(cursor)
        cursor += step
    return frames


# --- Caching ---------------------------------------------------------------
DATA_DIR: str = "data"
BUILDINGS_CACHE: str = "data/buildings.gpkg"


@dataclass
class MapParams:
    """User-tunable parameters passed from the UI to the shade engine."""

    when: datetime = field(default_factory=default_date)
    include_trees: bool = True
