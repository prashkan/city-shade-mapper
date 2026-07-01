"""Toronto neighbourhood boundaries - the AOI picker's data source.

Downloads the City of Toronto's official 158 neighbourhood polygons (Open Data,
WGS84) via the CKAN datastore API and caches them locally. Each neighbourhood
scopes the shadow map: buildings are downloaded for it and the shade is clipped
to its polygon.
"""
from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass

import geopandas as gpd
import requests
from shapely.geometry import shape
from shapely.geometry.base import BaseGeometry

import config

# City of Toronto Open Data - "Neighbourhoods" (current 158), WGS84.
CKAN_SEARCH_URL = "https://ckan0.cf.opendata.inter.prod-toronto.ca/api/3/action/datastore_search"
NEIGHBOURHOODS_RESOURCE_ID = "5e6095fc-1bef-4776-887c-28d37f722c51"
NEIGHBOURHOODS_CACHE = os.path.join(config.DATA_DIR, "neighbourhoods.gpkg")
NAME_FIELD = "AREA_NAME"

# The pilot neighbourhood (contains Liberty Village) — the app's default.
DEFAULT_NEIGHBOURHOOD = "Fort York-Liberty Village"

_HEADERS = {"User-Agent": "city-shade-mapper/1.0 (github.com/prashkan/city-shade-mapper)"}


def slugify(name: str) -> str:
    """Filesystem-safe slug for cache filenames, e.g. 'Fort York-Liberty
    Village' -> 'fort-york-liberty-village'."""
    s = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    return s or "aoi"


@dataclass(frozen=True)
class Neighbourhood:
    """A single Toronto neighbourhood: its name and WGS84 boundary polygon."""

    name: str
    geometry: BaseGeometry

    @property
    def slug(self) -> str:
        return slugify(self.name)

    def bbox(self) -> tuple[float, float, float, float]:
        """(minx, miny, maxx, maxy) in WGS84."""
        return tuple(self.geometry.bounds)

    def centroid_latlon(self) -> tuple[float, float]:
        c = self.geometry.centroid
        return (c.y, c.x)


def load_all(use_cache: bool = True) -> gpd.GeoDataFrame:
    """All neighbourhoods as a GeoDataFrame(name, geometry) in WGS84.

    Cached to ``data/neighbourhoods.gpkg`` after the first download.
    """
    if use_cache and os.path.exists(NEIGHBOURHOODS_CACHE):
        return gpd.read_file(NEIGHBOURHOODS_CACHE)

    resp = requests.get(
        CKAN_SEARCH_URL,
        params={"resource_id": NEIGHBOURHOODS_RESOURCE_ID, "limit": 500},
        headers=_HEADERS,
        timeout=60,
    )
    resp.raise_for_status()
    records = resp.json()["result"]["records"]

    names, geoms = [], []
    for r in records:
        raw = r.get("geometry")
        if not raw:
            continue
        geom = shape(json.loads(raw) if isinstance(raw, str) else raw)
        names.append(str(r[NAME_FIELD]))
        geoms.append(geom)

    gdf = gpd.GeoDataFrame({"name": names}, geometry=geoms, crs=config.CRS_WGS84)
    gdf = gdf.sort_values("name").reset_index(drop=True)

    os.makedirs(config.DATA_DIR, exist_ok=True)
    gdf.to_file(NEIGHBOURHOODS_CACHE, driver="GPKG")
    return gdf


def list_names() -> list[str]:
    """Sorted neighbourhood names for the UI picker."""
    return load_all()["name"].tolist()


def get(name: str) -> Neighbourhood:
    """Look up a neighbourhood by exact name."""
    gdf = load_all()
    row = gdf[gdf["name"] == name]
    if row.empty:
        raise KeyError(f"Unknown neighbourhood: {name!r}")
    return Neighbourhood(name=name, geometry=row.geometry.iloc[0])


def default() -> Neighbourhood:
    """The pilot neighbourhood (Fort York-Liberty Village), or the first by name."""
    try:
        return get(DEFAULT_NEIGHBOURHOOD)
    except KeyError:  # pragma: no cover - dataset always contains it
        return get(list_names()[0])


if __name__ == "__main__":
    import warnings

    warnings.filterwarnings("ignore")
    names = list_names()
    print(f"{len(names)} neighbourhoods; default = {DEFAULT_NEIGHBOURHOOD!r}")
    nb = default()
    print(f"{nb.name}: slug={nb.slug} centroid={nb.centroid_latlon()} bbox={[round(x,4) for x in nb.bbox()]}")
