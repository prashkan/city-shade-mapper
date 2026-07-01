"""Frame orchestration: build (or load) the ground-shade layer for a timestamp.

The mapper renders one shade *frame* per time step. Each frame is the dissolved
building + tree-canopy shade for that moment, scoped to a chosen neighbourhood
and cached on disk per (neighbourhood, solar bucket) so scrubbing the time slider
(or replaying a day) is instant after the first build.
"""
from __future__ import annotations

from datetime import datetime

import geopandas as gpd

import config
from src import data, shadows
from src.neighbourhoods import Neighbourhood

# Neighbourhood polygon (EPSG:32617) cache, keyed by slug — avoids reprojecting
# the boundary on every frame.
_AOI_METRIC: dict[str, object] = {}


def aoi_polygon_metric(nb: Neighbourhood):
    """The neighbourhood boundary as a shapely polygon in EPSG:32617 (cached)."""
    if nb.slug not in _AOI_METRIC:
        _AOI_METRIC[nb.slug] = (
            gpd.GeoSeries([nb.geometry], crs=config.CRS_WGS84)
            .to_crs(config.CRS_METRIC)
            .iloc[0]
        )
    return _AOI_METRIC[nb.slug]


def aoi_clip(shade_metric: gpd.GeoDataFrame, nb: Neighbourhood) -> gpd.GeoDataFrame:
    """Clip a shade layer (EPSG:32617) to the neighbourhood boundary.

    Buildings just outside the neighbourhood still cast shadows into it (we
    download a margin), but the shade is rendered only within the neighbourhood,
    so the visualisation stays scoped and shaded-area metrics are meaningful.
    """
    if not len(shade_metric) or shade_metric.geometry.iloc[0] is None:
        return shade_metric
    clipped = shade_metric.geometry.iloc[0].intersection(aoi_polygon_metric(nb))
    return gpd.GeoDataFrame({"kind": ["shade"]}, geometry=[clipped], crs=config.CRS_METRIC)


def build_frame(
    nb: Neighbourhood,
    when: datetime | None = None,
    include_trees: bool = True,
    use_cache: bool = True,
    buildings: gpd.GeoDataFrame | None = None,
) -> gpd.GeoDataFrame:
    """Return the combined shade layer (EPSG:32617) for ``nb`` at ``when``.

    Pass a preloaded ``buildings`` GeoDataFrame to avoid re-reading the cache on
    every frame when warming a whole day.
    """
    when = when or config.default_date()
    blds = buildings if buildings is not None else data.load_buildings(nb)
    return shadows.load_or_build_shade(
        blds, when, include_trees=include_trees, use_cache=use_cache, aoi_key=nb.slug
    )


def shaded_area_km2(shade_metric: gpd.GeoDataFrame) -> float:
    """Total ground area (km^2) covered by the shade layer (input EPSG:32617)."""
    if not len(shade_metric):
        return 0.0
    geom = shade_metric.geometry.iloc[0]
    if geom is None or geom.is_empty:
        return 0.0
    return round(geom.area / 1e6, 4)


def aoi_area_km2(nb: Neighbourhood) -> float:
    """Area (km^2) of the neighbourhood, for the shaded-fraction metric."""
    return round(aoi_polygon_metric(nb).area / 1e6, 4)


def shaded_fraction(shade_metric: gpd.GeoDataFrame, nb: Neighbourhood) -> float:
    """Fraction (0..1) of the neighbourhood in shade. Diagnostic / UI metric."""
    aoi = aoi_area_km2(nb)
    return round(shaded_area_km2(shade_metric) / aoi, 4) if aoi else 0.0


if __name__ == "__main__":
    import warnings

    warnings.filterwarnings("ignore")
    from src import neighbourhoods

    nb = neighbourhoods.default()
    when = config.default_date()
    frame = aoi_clip(build_frame(nb, when=when), nb)
    print(
        f"{nb.name} @ {when.isoformat()}: shaded area {shaded_area_km2(frame):.3f} km^2 "
        f"({shaded_fraction(frame, nb):.1%} of neighbourhood)"
    )
