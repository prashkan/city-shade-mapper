"""Data scaffolding - building footprints for the study area.

Downloads building footprints for the AOI, imputes building heights where OSM
lacks them, and caches results so we do not re-hit Overpass on every run. Unlike
the routing project this app needs no street graph — the shade is cast purely by
building (and optional tree-canopy) geometry.
"""
from __future__ import annotations

import os
import re
from typing import Optional

import geopandas as gpd
import osmnx as ox

import config


# --------------------------------------------------------------------------
# Building footprints + height imputation
# --------------------------------------------------------------------------
def _parse_number(raw: object) -> Optional[float]:
    """Parse an OSM numeric tag (e.g. '12', '12 m', '12.5') to a float."""
    if raw is None:
        return None
    if isinstance(raw, (int, float)):
        return float(raw)
    m = re.search(r"[-+]?\d*\.?\d+", str(raw))
    return float(m.group()) if m else None


def impute_height(row) -> float:
    """Best available height: explicit -> levels*per_level -> default."""
    h = _parse_number(row.get("height"))
    if h and h > 0:
        return h
    levels = _parse_number(row.get("building:levels"))
    if levels and levels > 0:
        return levels * config.METERS_PER_LEVEL
    return config.DEFAULT_BUILDING_HEIGHT_M


def load_buildings(use_cache: bool = True) -> gpd.GeoDataFrame:
    """Return building footprints (polygons) with a populated `height` column.

    Output is in WGS84 (EPSG:4326); reproject downstream as needed.
    """
    if use_cache and os.path.exists(config.BUILDINGS_CACHE):
        return gpd.read_file(config.BUILDINGS_CACHE)

    tags = {"building": True}
    try:
        gdf = ox.features_from_point(
            config.CENTER_LATLON, tags=tags, dist=config.RADIUS_M
        )
    except Exception:
        minx, miny, maxx, maxy = config.aoi_bbox()
        gdf = ox.features_from_bbox(bbox=(minx, miny, maxx, maxy), tags=tags)

    # Keep only polygonal footprints.
    gdf = gdf[gdf.geometry.type.isin(["Polygon", "MultiPolygon"])].copy()

    # Ensure the columns we read exist even if absent in this extract.
    for col in ("height", "building:levels"):
        if col not in gdf.columns:
            gdf[col] = None

    gdf["height"] = gdf.apply(impute_height, axis=1)
    gdf = gdf.set_crs(config.CRS_WGS84, allow_override=True)

    # Trim to a tidy, serializable schema (GPKG dislikes mixed/list columns).
    keep = gpd.GeoDataFrame(
        {"height": gdf["height"].astype(float)},
        geometry=gdf.geometry,
        crs=config.CRS_WGS84,
    )

    os.makedirs(config.DATA_DIR, exist_ok=True)
    keep.to_file(config.BUILDINGS_CACHE, driver="GPKG")
    return keep


def height_coverage(buildings: gpd.GeoDataFrame) -> dict:
    """Diagnostic: how many buildings fell back to the default height."""
    total = len(buildings)
    defaulted = int((buildings["height"] == config.DEFAULT_BUILDING_HEIGHT_M).sum())
    return {
        "buildings": total,
        "defaulted": defaulted,
        "explicit_pct": round(100 * (total - defaulted) / total, 1) if total else 0.0,
    }


if __name__ == "__main__":
    b = load_buildings()
    print(f"Buildings: {len(b)}")
    print("Height coverage:", height_coverage(b))
