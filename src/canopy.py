"""Tree-canopy shade from the Meta/WRI 1 m global canopy-height raster.

Tree shade is derived from the Meta x WRI "Very High Resolution" canopy-height
tiles (AWS open data, EPSG:3857, uint8 height in metres, ~1.2 m resolution),
which give consistent, city-wide canopy coverage independent of OSM tree tagging.

Approach (a routing-grade approximation, not a ray-traced shadow):
  1. Locate the canopy tile(s) covering the AOI and window-read just the AOI
     (decimated for speed) straight from S3 via GDAL ``/vsicurl``.
  2. Threshold canopy height >= CANOPY_MIN_HEIGHT_M to get tree-covered pixels.
  3. Vectorise the tree mask to polygons.
  4. Sweep each polygon along the anti-solar azimuth by L = H / tan(altitude)
     (Minkowski-style union of a few translated copies) to approximate the
     ground shade the canopy casts.

All offset maths happen in the metric CRS (EPSG:32617); the result is returned
in WGS84 to match :func:`src.shadows.canopy_shade`'s contract.
"""
from __future__ import annotations

import math
import os
from datetime import datetime
from typing import Optional

import geopandas as gpd
import numpy as np
import pandas as pd
import rasterio
from rasterio.features import shapes as rio_shapes
from rasterio.warp import transform_bounds
from rasterio.windows import from_bounds as window_from_bounds
from shapely.affinity import translate
from shapely.geometry import box, shape
from shapely.ops import unary_union

import config

CANOPY_S3_BASE = (
    "https://dataforgood-fb-data.s3.amazonaws.com/forests/v1/alsgedi_global_v6_float"
)
CANOPY_TILES_INDEX = f"{CANOPY_S3_BASE}/tiles.geojson"
CANOPY_TILES_CACHE = os.path.join(config.DATA_DIR, "canopy_tiles.geojson")
# Dissolved tree-canopy footprint (EPSG:32617) + representative height. This is
# time-independent, so we read the raster once and reuse it for every hour.
CANOPY_TREES_CACHE = os.path.join(config.DATA_DIR, "canopy_trees.gpkg")

# Read the AOI at ~this many metres/pixel; 1.2 m native is overkill for routing.
CANOPY_READ_RES_M: float = 3.0

# GDAL tuning for remote COG reads.
_GDAL_ENV = dict(
    GDAL_DISABLE_READDIR_ON_OPEN="EMPTY_DIR",
    CPL_VSIL_CURL_ALLOWED_EXTENSIONS=".tif",
    GDAL_HTTP_MULTIRANGE="YES",
    VSI_CACHE="TRUE",
)


def _load_tile_index() -> gpd.GeoDataFrame:
    if os.path.exists(CANOPY_TILES_CACHE):
        return gpd.read_file(CANOPY_TILES_CACHE)
    idx = gpd.read_file(CANOPY_TILES_INDEX)
    os.makedirs(config.DATA_DIR, exist_ok=True)
    idx.to_file(CANOPY_TILES_CACHE, driver="GeoJSON")
    return idx


def tiles_for_bbox(bbox_wgs84: tuple[float, float, float, float]) -> list[str]:
    """Canopy tile ids whose footprint intersects the AOI bbox (WGS84)."""
    idx = _load_tile_index()
    aoi = gpd.GeoDataFrame(geometry=[box(*bbox_wgs84)], crs=config.CRS_WGS84)
    hit = gpd.sjoin(idx, aoi, how="inner", predicate="intersects")
    return sorted(hit["tile"].astype(str).unique().tolist())


def _shadow_offset_xy(sun_altitude_rad: float, sun_azimuth_rad: float, height_m: float):
    """Ground shade displacement (dx, dy) in metres for an object of ``height_m``.

    Metric axes: x = east, y = north. suncalc azimuth is measured from south,
    positive toward west, so the sun's horizontal unit vector is
    (-sin A, -cos A) and the shade points the opposite way.
    """
    if sun_altitude_rad <= 0:
        return 0.0, 0.0
    length = height_m / math.tan(sun_altitude_rad)
    dx = length * math.sin(sun_azimuth_rad)
    dy = length * math.cos(sun_azimuth_rad)
    return dx, dy


def _read_tree_mask(tile: str, bbox_3857) -> tuple[np.ndarray, object]:
    """Decimated window read of one tile over ``bbox_3857``; returns (height, transform)."""
    url = f"/vsicurl/{CANOPY_S3_BASE}/chm/{tile}.tif"
    with rasterio.Env(**_GDAL_ENV), rasterio.open(url) as ds:
        win = window_from_bounds(*bbox_3857, transform=ds.transform)
        win = win.round_offsets().round_lengths()
        if win.width <= 0 or win.height <= 0:
            return np.zeros((0, 0), dtype="uint8"), ds.window_transform(win)
        decim = max(1, int(round(CANOPY_READ_RES_M / ds.res[0])))
        out_h = max(1, int(win.height // decim))
        out_w = max(1, int(win.width // decim))
        arr = ds.read(1, window=win, out_shape=(out_h, out_w))
        transform = ds.window_transform(win) * rasterio.Affine.scale(
            win.width / out_w, win.height / out_h
        )
    return arr, transform


def _load_tree_canopy(bbox_wgs84, use_cache: bool = True):
    """Time-independent dissolved tree-canopy footprint (EPSG:32617) and a
    representative canopy height (m). Reads the raster once, then caches to disk.
    Returns (geometry_or_None, rep_height)."""
    if use_cache and os.path.exists(CANOPY_TREES_CACHE):
        g = gpd.read_file(CANOPY_TREES_CACHE)
        if len(g) and g.geometry.iloc[0] is not None and not g.geometry.iloc[0].is_empty:
            return g.geometry.iloc[0], float(g["rep_height"].iloc[0])
        return None, 0.0

    bbox_3857 = transform_bounds(config.CRS_WGS84, "EPSG:3857", *bbox_wgs84)
    tree_polys_3857 = []
    heights = []
    for tile in tiles_for_bbox(bbox_wgs84):
        arr, transform = _read_tree_mask(tile, bbox_3857)
        if arr.size == 0:
            continue
        mask = arr >= config.CANOPY_MIN_HEIGHT_M
        if not mask.any():
            continue
        heights.append(float(np.median(arr[mask])))
        for geom, val in rio_shapes(mask.astype("uint8"), mask=mask, transform=transform):
            if val == 1:
                tree_polys_3857.append(shape(geom))

    if not tree_polys_3857:
        return None, 0.0

    trees_geom = unary_union(
        gpd.GeoDataFrame(geometry=tree_polys_3857, crs="EPSG:3857")
        .to_crs(config.CRS_METRIC)
        .geometry.values
    )
    rep_height = float(np.median(heights)) if heights else config.CANOPY_MIN_HEIGHT_M

    os.makedirs(config.DATA_DIR, exist_ok=True)
    gpd.GeoDataFrame(
        {"rep_height": [rep_height]}, geometry=[trees_geom], crs=config.CRS_METRIC
    ).to_file(CANOPY_TREES_CACHE, driver="GPKG")
    return trees_geom, rep_height


def canopy_shade(
    bbox_wgs84: tuple[float, float, float, float],
    when: datetime,
    sun_altitude_deg: Optional[float] = None,
    sun_azimuth_deg: Optional[float] = None,
) -> gpd.GeoDataFrame:
    """Tree-canopy ground-shade polygons for ``bbox`` at ``when`` (WGS84 out).

    The expensive raster read/vectorise is cached (time-independent); only the
    sun-direction sweep is recomputed per timestamp, so repeat hours are fast.
    """
    if sun_altitude_deg is None or sun_azimuth_deg is None:
        from suncalc import get_position

        cx = 0.5 * (bbox_wgs84[0] + bbox_wgs84[2])
        cy = 0.5 * (bbox_wgs84[1] + bbox_wgs84[3])
        ts = pd.Timestamp(when)
        ts = ts.tz_convert("UTC") if ts.tzinfo else ts.tz_localize("UTC")
        sp = get_position(ts.to_pydatetime(), cx, cy)
        alt, az = sp["altitude"], sp["azimuth"]
    else:
        alt = math.radians(sun_altitude_deg)
        az = math.radians(sun_azimuth_deg)

    empty = gpd.GeoDataFrame({"kind": []}, geometry=[], crs=config.CRS_WGS84)
    if alt <= 0:
        return empty

    trees_geom, rep_height = _load_tree_canopy(bbox_wgs84)
    if trees_geom is None or trees_geom.is_empty:
        return empty

    dx, dy = _shadow_offset_xy(alt, az, rep_height)

    # Sweep the canopy footprint along the shade vector (Minkowski approx).
    fractions = (0.0, 0.34, 0.67, 1.0)
    swept = unary_union(
        [translate(trees_geom, xoff=f * dx, yoff=f * dy) for f in fractions]
    )

    out = gpd.GeoDataFrame({"kind": ["tree_shade"]}, geometry=[swept], crs=config.CRS_METRIC)
    return out.to_crs(config.CRS_WGS84)


if __name__ == "__main__":
    import config as _c

    bbox_wgs84 = _c.aoi_bbox()  # (minx, miny, maxx, maxy)
    sh = canopy_shade(bbox_wgs84, _c.default_date())
    if len(sh):
        area = sh.to_crs(_c.CRS_METRIC).geometry.iloc[0].area / 1e6
        print(f"Tree-canopy shade: {area:.3f} km^2")
    else:
        print("No tree-canopy shade found.")
