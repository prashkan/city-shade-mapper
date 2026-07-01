"""Dynamic shadow generation - the core of the shade mapper.

Two shade sources, unioned into a single ground-shade layer:

1. **Building shadows** via ``pybdshadow`` (sun position from the given
   timestamp; footprints + imputed heights).
2. **Tree-canopy shade** derived from the Meta/WRI 1 m global canopy-height
   raster.

pybdshadow expects buildings in **WGS84** and returns shadows in WGS84; we
reproject the unioned shade layer to the metric CRS (EPSG:32617, UTM 17N) for
all downstream length / area maths.
"""
from __future__ import annotations

import os
from datetime import datetime
from typing import Optional

import geopandas as gpd
import pandas as pd
from shapely.ops import unary_union

import config

try:  # pybdshadow pulls in matplotlib; keep import lazy-friendly
    import pybdshadow
except Exception:  # pragma: no cover
    pybdshadow = None


# --------------------------------------------------------------------------
# Building shadows
# --------------------------------------------------------------------------
def building_shadows(
    buildings_wgs84: gpd.GeoDataFrame, when: datetime
) -> gpd.GeoDataFrame:
    """Ground shadow polygons cast by buildings at ``when`` (WGS84 in/out).

    ``when`` should be timezone-aware (Toronto local); suncalc reads its UTC
    instant via ``.timestamp()``.
    """
    if pybdshadow is None:  # pragma: no cover
        raise RuntimeError("pybdshadow is not installed")

    bld = buildings_wgs84.copy()
    if bld.crs is None:
        bld = bld.set_crs(config.CRS_WGS84)
    else:
        bld = bld.to_crs(config.CRS_WGS84)

    # pybdshadow operates on single Polygons (no .exterior on MultiPolygon).
    bld = bld.explode(index_parts=False)
    bld = bld[bld.geometry.type == "Polygon"]

    # pybdshadow keys shadows by a 'building_id' column.
    bld = bld.reset_index(drop=True)
    bld["building_id"] = bld.index.astype(int)
    bld = bld[bld["height"] > 0]

    # Night-safe: if the sun is below the horizon there is no shade. Return an
    # empty layer instead of letting pybdshadow raise.
    minx, miny, maxx, maxy = bld.total_bounds
    if not sun_is_up(when, (minx + maxx) / 2, (miny + maxy) / 2):
        return gpd.GeoDataFrame({"geometry": []}, geometry="geometry", crs=config.CRS_WGS84)

    # Pass a UTC pandas Timestamp so sun position is unambiguous.
    ts = pd.Timestamp(when).tz_convert("UTC") if pd.Timestamp(when).tzinfo else pd.Timestamp(when, tz="UTC")

    try:
        shadows = pybdshadow.bdshadow_sunlight(bld, ts, height="height")
    except ValueError:
        # pybdshadow guards sunrise/sunset internally; treat as no shade.
        return gpd.GeoDataFrame({"geometry": []}, geometry="geometry", crs=config.CRS_WGS84)
    shadows = shadows.set_crs(config.CRS_WGS84, allow_override=True)
    return shadows


# --------------------------------------------------------------------------
# Tree-canopy shade (Meta/WRI 1 m canopy height)
# --------------------------------------------------------------------------
def canopy_shade(
    bbox_wgs84: tuple[float, float, float, float],
    when: datetime,
    sun_altitude_deg: Optional[float] = None,
    sun_azimuth_deg: Optional[float] = None,
    aoi_key: str = "default",
) -> gpd.GeoDataFrame:
    """Tree-canopy shade polygons for ``bbox`` at ``when`` (WGS84 out).

    Strategy: clip the canopy-height raster to the AOI, threshold to
    canopy >= CANOPY_MIN_HEIGHT_M to obtain tree-covered pixels, vectorise,
    then offset each canopy polygon along the anti-solar azimuth by a distance
    proportional to canopy height / tan(altitude) to approximate cast shade.

    Implemented in src/canopy.py to keep the raster machinery isolated.
    """
    from src import canopy  # local import: rasterio is heavy / optional

    return canopy.canopy_shade(
        bbox_wgs84,
        when,
        sun_altitude_deg=sun_altitude_deg,
        sun_azimuth_deg=sun_azimuth_deg,
        aoi_key=aoi_key,
    )


# --------------------------------------------------------------------------
# Sun position helpers
# --------------------------------------------------------------------------
def sun_position(when: datetime, lon: float, lat: float) -> dict:
    """Sun altitude/azimuth (radians) at ``when`` via suncalc."""
    from suncalc import get_position

    ts = pd.Timestamp(when)
    ts = ts.tz_convert("UTC") if ts.tzinfo else ts.tz_localize("UTC")
    return get_position(ts.to_pydatetime(), lon, lat)


def sun_is_up(when: datetime, lon: float, lat: float) -> bool:
    """True if the sun is above the horizon at ``when`` / location."""
    return sun_position(when, lon, lat)["altitude"] > 0


# --------------------------------------------------------------------------
# Combined shade layer
# --------------------------------------------------------------------------
def combined_shade(
    buildings_wgs84: gpd.GeoDataFrame,
    when: datetime,
    include_trees: bool = True,
    aoi_key: str = "default",
) -> gpd.GeoDataFrame:
    """Union of building + (optional) tree-canopy shade, projected to metric CRS.

    Returns a single-row GeoDataFrame in EPSG:32617 holding the dissolved shade
    geometry, ready for fast rendering / area maths.
    """
    parts = []

    bsh = building_shadows(buildings_wgs84, when)
    if len(bsh):
        parts.append(unary_union(bsh.geometry.values))

    if include_trees:
        minx, miny, maxx, maxy = buildings_wgs84.total_bounds
        try:
            tsh = canopy_shade((minx, miny, maxx, maxy), when, aoi_key=aoi_key)
            if len(tsh):
                parts.append(unary_union(tsh.geometry.values))
        except Exception as exc:  # canopy is best-effort; never fail the frame
            print(f"[shadows] tree canopy skipped: {exc}")

    geom = unary_union(parts) if parts else None
    out = gpd.GeoDataFrame({"kind": ["shade"]}, geometry=[geom], crs=config.CRS_WGS84)
    return out.to_crs(config.CRS_METRIC)


def shade_cache_path(when: datetime, include_trees: bool, aoi_key: str = "default") -> str:
    """Deterministic cache path keyed by AOI + timestamp + tree inclusion."""
    key = f"{pd.Timestamp(when).strftime('%Y%m%dT%H%M')}_{'t' if include_trees else 'b'}"
    return os.path.join(config.DATA_DIR, f"shade_{aoi_key}_{key}.gpkg")


def load_or_build_shade(
    buildings_wgs84: gpd.GeoDataFrame,
    when: datetime,
    include_trees: bool = True,
    use_cache: bool = True,
    aoi_key: str = "default",
) -> gpd.GeoDataFrame:
    """Combined shade layer (EPSG:32617), cached to disk per AOI + solar bucket."""
    when = config.solar_bucket(when)
    path = shade_cache_path(when, include_trees, aoi_key)
    if use_cache and os.path.exists(path):
        return gpd.read_file(path)
    shade = combined_shade(buildings_wgs84, when, include_trees=include_trees, aoi_key=aoi_key)
    os.makedirs(config.DATA_DIR, exist_ok=True)
    if shade.geometry.iloc[0] is not None:
        shade.to_file(path, driver="GPKG")
    return shade


if __name__ == "__main__":
    from src import data, neighbourhoods

    nb = neighbourhoods.default()
    blds = data.load_buildings(nb)
    when = config.default_date()
    sh = building_shadows(blds, when)
    print(f"{nb.name}: {len(sh)} building-shadow polygons at {when.isoformat()}")
    comb = combined_shade(blds, when, include_trees=False, aoi_key=nb.slug)
    area_km2 = comb.geometry.iloc[0].area / 1e6 if comb.geometry.iloc[0] else 0
    print(f"Combined (buildings-only) shade area: {area_km2:.3f} km^2 (EPSG:32617)")
