"""Frame orchestration: build (or load) the ground-shade layer for a timestamp.

The mapper renders one shade *frame* per time step. Each frame is the dissolved
building + tree-canopy shade for that moment, cached on disk per solar bucket so
scrubbing the time slider (or replaying a day) is instant after the first build.
"""
from __future__ import annotations

from datetime import datetime

import geopandas as gpd

import config
from src import data, shadows


def build_frame(
    when: datetime | None = None,
    include_trees: bool = True,
    use_cache: bool = True,
    buildings: gpd.GeoDataFrame | None = None,
) -> gpd.GeoDataFrame:
    """Return the combined shade layer (EPSG:32617) for ``when``.

    Pass a preloaded ``buildings`` GeoDataFrame to avoid re-reading the cache on
    every frame when warming a whole day.
    """
    when = when or config.default_date()
    blds = buildings if buildings is not None else data.load_buildings()
    return shadows.load_or_build_shade(
        blds, when, include_trees=include_trees, use_cache=use_cache
    )


def shaded_area_km2(shade_metric: gpd.GeoDataFrame) -> float:
    """Total ground area (km^2) covered by the shade layer (input EPSG:32617)."""
    if not len(shade_metric):
        return 0.0
    geom = shade_metric.geometry.iloc[0]
    if geom is None or geom.is_empty:
        return 0.0
    return round(geom.area / 1e6, 4)


def aoi_area_km2() -> float:
    """Area (km^2) of the square AOI, for computing a shaded-fraction metric."""
    side_m = 2 * config.RADIUS_M
    return round((side_m * side_m) / 1e6, 4)


def shaded_fraction(shade_metric: gpd.GeoDataFrame) -> float:
    """Fraction (0..1) of the AOI in shade. Diagnostic / UI metric."""
    aoi = aoi_area_km2()
    return round(shaded_area_km2(shade_metric) / aoi, 4) if aoi else 0.0


if __name__ == "__main__":
    import warnings

    warnings.filterwarnings("ignore")
    when = config.default_date()
    frame = build_frame(when=when)
    print(
        f"Frame {when.isoformat()}: shaded area {shaded_area_km2(frame):.3f} km^2 "
        f"({shaded_fraction(frame):.1%} of AOI)"
    )
