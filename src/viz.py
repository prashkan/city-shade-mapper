"""Rendering - lightweight pydeck layers on a tokenless Carto basemap.

The map shows the neighbourhood's buildings (optionally extruded for a 3D feel)
and the ground-shade footprint for the selected moment. As the time slider moves
the shade layer is swapped, so shadows visibly sweep across the neighbourhood.
"""
from __future__ import annotations

import math

import geopandas as gpd
import pydeck as pdk

import config


def _polygon_rings(geom):
    if geom is None or geom.is_empty:
        return
    if geom.geom_type == "Polygon":
        yield [[float(x), float(y)] for x, y in geom.exterior.coords]
    elif geom.geom_type == "MultiPolygon":
        for part in geom.geoms:
            yield [[float(x), float(y)] for x, y in part.exterior.coords]


def buildings_layer(buildings_wgs84: gpd.GeoDataFrame, extruded: bool = True) -> pdk.Layer | None:
    """Neighbourhood buildings as a (optionally extruded) PolygonLayer.

    ``buildings_wgs84`` must carry a ``height`` column (metres) and WGS84 geom.
    """
    if not len(buildings_wgs84):
        return None
    rows = []
    for _, r in buildings_wgs84.iterrows():
        h = float(r.get("height", config.DEFAULT_BUILDING_HEIGHT_M) or 0.0)
        for ring in _polygon_rings(r.geometry):
            rows.append({"polygon": ring, "height": h})
    if not rows:
        return None
    return pdk.Layer(
        "PolygonLayer",
        data=rows,
        get_polygon="polygon",
        extruded=extruded,
        get_elevation="height",
        elevation_scale=1,
        get_fill_color=[200, 200, 205, 230],
        get_line_color=[140, 140, 150, 120],
        line_width_min_pixels=1,
        pickable=False,
    )


def shade_layer(shade_metric: gpd.GeoDataFrame) -> pdk.Layer | None:
    """Flat, translucent ground-shade polygons (input in EPSG:32617)."""
    if not len(shade_metric) or shade_metric.geometry.iloc[0] is None:
        return None
    simplified = shade_metric.copy()
    simplified["geometry"] = simplified.geometry.simplify(1.0)  # 1 m, keep it light
    shade_wgs84 = simplified.to_crs(config.CRS_WGS84)
    rows = [{"polygon": ring} for _, r in shade_wgs84.iterrows()
            for ring in _polygon_rings(r.geometry)]
    if not rows:
        return None
    return pdk.Layer(
        "PolygonLayer",
        data=rows,
        get_polygon="polygon",
        extruded=False,
        get_fill_color=[30, 60, 110, 120],
        get_line_color=[30, 60, 110, 0],
        pickable=False,
    )


def fit_view(points, pitch: float = 45.0) -> pdk.ViewState:
    """A view centred/zoomed to contain ``points`` = [(lat, lon), ...].

    Defaults to the AOI centre with a slight pitch so extruded buildings read as
    3D and their shadows are legible.
    """
    if not points:
        lat, lon = config.CENTER_LATLON
        return pdk.ViewState(latitude=lat, longitude=lon, zoom=14.5, pitch=pitch, bearing=0)
    lats = [p[0] for p in points]
    lons = [p[1] for p in points]
    clat, clon = (min(lats) + max(lats)) / 2, (min(lons) + max(lons)) / 2
    span = max(max(lats) - min(lats), (max(lons) - min(lons)) * math.cos(math.radians(clat)))
    span = max(span, 1e-4)
    zoom = min(16, max(12, math.log2(360 / span) - 0.5))
    return pdk.ViewState(latitude=clat, longitude=clon, zoom=zoom, pitch=pitch, bearing=0)


def build_deck(layers, view_state: pdk.ViewState | None = None) -> pdk.Deck:
    return pdk.Deck(
        layers=[ly for ly in layers if ly is not None],
        initial_view_state=view_state or fit_view([]),
        map_provider="carto",
        map_style="light",
    )
