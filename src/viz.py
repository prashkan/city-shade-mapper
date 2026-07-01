"""Rendering - deck.gl layers on a tokenless Carto basemap.

Two rendering paths share the same look:

* :func:`build_deck` — a static pydeck scene (one moment), handy for exports and
  quick server-side snapshots.
* :func:`animated_html` — a self-contained deck.gl component that holds *all* of
  a day's shade frames and animates them **client-side**. A JS timer swaps only
  the shade layer each tick, so the basemap and camera never reload — the
  shadows glide seamlessly instead of the whole map flickering on every rerun.
"""
from __future__ import annotations

import json
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


# --------------------------------------------------------------------------
# Client-side animated component (seamless playback)
# --------------------------------------------------------------------------
def _round_coords(obj, nd: int):
    """Recursively round GeoJSON coordinate arrays to ``nd`` decimals."""
    if isinstance(obj, (list, tuple)):
        if obj and isinstance(obj[0], (int, float)):
            return [round(float(obj[0]), nd), round(float(obj[1]), nd)]
        return [_round_coords(x, nd) for x in obj]
    return obj


def buildings_fc(buildings_wgs84: gpd.GeoDataFrame, simplify_m: float = 2.0, nd: int = 5) -> dict:
    """Buildings as a GeoJSON FeatureCollection with a ``height`` property.

    Simplified (in metres) and coordinate-rounded to keep the embedded payload
    small. Serialized once per session — the geometry never changes with time.
    """
    from shapely.geometry import mapping

    if not len(buildings_wgs84):
        return {"type": "FeatureCollection", "features": []}
    metric = buildings_wgs84.to_crs(config.CRS_METRIC)
    metric["geometry"] = metric.geometry.simplify(simplify_m)
    wgs = metric.to_crs(config.CRS_WGS84)
    feats = []
    for h, geom in zip(buildings_wgs84["height"], wgs.geometry):
        if geom is None or geom.is_empty:
            continue
        gj = mapping(geom)
        gj["coordinates"] = _round_coords(gj["coordinates"], nd)
        feats.append({
            "type": "Feature",
            "properties": {"height": float(h or config.DEFAULT_BUILDING_HEIGHT_M)},
            "geometry": gj,
        })
    return {"type": "FeatureCollection", "features": feats}


def shade_fc(shade_metric: gpd.GeoDataFrame, simplify_m: float = 8.0, nd: int = 5) -> dict:
    """One shade frame as a GeoJSON FeatureCollection (input EPSG:32617)."""
    from shapely.geometry import mapping

    empty = {"type": "FeatureCollection", "features": []}
    if not len(shade_metric) or shade_metric.geometry.iloc[0] is None:
        return empty
    geom = shade_metric.geometry.iloc[0].simplify(simplify_m)
    if geom is None or geom.is_empty:
        return empty
    gj = mapping(gpd.GeoSeries([geom], crs=config.CRS_METRIC).to_crs(config.CRS_WGS84).iloc[0])
    gj["coordinates"] = _round_coords(gj["coordinates"], nd)
    return {"type": "FeatureCollection", "features": [{"type": "Feature", "properties": {}, "geometry": gj}]}


def animated_html(
    buildings: dict,
    frames: list[dict],
    view: pdk.ViewState | None = None,
    extruded: bool = True,
    height: int = 640,
    interval_ms: int = 700,
) -> str:
    """A self-contained deck.gl HTML component animating a day's shade frames.

    ``frames`` is a list of ``{"label", "sun_alt", "shade_pct", "shade"}`` dicts
    (``shade`` is a GeoJSON FeatureCollection). Playback (slider + Play/Pause)
    runs entirely in the browser, so only the shade layer updates each tick —
    the Carto basemap and the user's pan/zoom/pitch are preserved.
    """
    view = view or fit_view([])
    payload = {
        "buildings": buildings,
        "frames": frames,
        "extruded": bool(extruded),
        "intervalMs": int(interval_ms),
        "view": {
            "latitude": view.latitude,
            "longitude": view.longitude,
            "zoom": view.zoom,
            "pitch": getattr(view, "pitch", 45) or 0,
            "bearing": getattr(view, "bearing", 0) or 0,
        },
    }
    return _HTML_TEMPLATE.replace("__PAYLOAD__", json.dumps(payload)).replace(
        "__HEIGHT__", str(int(height))
    )


_HTML_TEMPLATE = """
<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8" />
<script src="https://unpkg.com/deck.gl@9/dist.min.js"></script>
<style>
  html, body { margin: 0; font-family: -apple-system, Segoe UI, Roboto, sans-serif; }
  #map { position: relative; width: 100%; height: __HEIGHT__px; background: #eef1f4; }
  #controls {
    position: absolute; left: 12px; right: 12px; bottom: 12px; z-index: 10;
    background: rgba(255,255,255,0.92); border-radius: 10px; padding: 10px 14px;
    box-shadow: 0 2px 10px rgba(0,0,0,0.15); display: flex; align-items: center; gap: 12px;
  }
  #play { border: none; background: #1f6feb; color: #fff; border-radius: 8px;
    width: 40px; height: 34px; font-size: 15px; cursor: pointer; }
  #slider { flex: 1; }
  .readout { font-variant-numeric: tabular-nums; white-space: nowrap; font-size: 13px; color: #24292f; }
  .readout b { font-size: 15px; }
  #legend { position: absolute; top: 10px; left: 12px; z-index: 10; font-size: 12px;
    background: rgba(255,255,255,0.9); padding: 6px 10px; border-radius: 8px; color: #24292f; }
  .sw { display: inline-block; width: 11px; height: 11px; border-radius: 2px; margin: 0 4px -1px 0; }
</style>
</head>
<body>
<div id="map">
  <div id="legend">
    <span class="sw" style="background:#c8c8cd"></span>Buildings
    <span class="sw" style="background:#1e3c6e;opacity:.55;margin-left:10px"></span>Shadow
  </div>
  <div id="controls">
    <button id="play">&#9654;</button>
    <input id="slider" type="range" min="0" value="0" step="1" />
    <div class="readout">
      <b id="t_time">--</b> &nbsp;·&nbsp; sun <b id="t_alt">--</b>&deg;
      &nbsp;·&nbsp; shade <b id="t_pct">--</b>%
    </div>
  </div>
</div>
<script>
const P = __PAYLOAD__;
const D = deck;
const N = P.frames.length;

const basemap = new D.TileLayer({
  id: 'basemap',
  data: 'https://basemaps.cartocdn.com/light_all/{z}/{x}/{y}.png',
  minZoom: 0, maxZoom: 19, tileSize: 256,
  renderSubLayers: props => {
    const {boundingBox} = props.tile;
    return new D.BitmapLayer(props, {
      data: null, image: props.data,
      bounds: [boundingBox[0][0], boundingBox[0][1], boundingBox[1][0], boundingBox[1][1]]
    });
  }
});

const buildings = new D.GeoJsonLayer({
  id: 'buildings',
  data: P.buildings,
  extruded: P.extruded,
  getElevation: f => f.properties.height || 4,
  getFillColor: [200, 200, 205, 230],
  getLineColor: [140, 140, 150, 120],
  lineWidthMinPixels: 1, stroked: true, filled: true, pickable: false
});

function shadeLayer(i) {
  return new D.GeoJsonLayer({
    id: 'shade',
    data: P.frames[i] ? P.frames[i].shade : {type: 'FeatureCollection', features: []},
    extruded: false,
    getFillColor: [30, 60, 110, 120],
    getLineColor: [30, 60, 110, 0],
    stroked: false, filled: true, pickable: false
  });
}

const deckgl = new D.DeckGL({
  container: 'map',
  initialViewState: P.view,
  controller: true,
  layers: [basemap, shadeLayer(0), buildings]
});

const slider = document.getElementById('slider');
const playBtn = document.getElementById('play');
slider.max = String(Math.max(0, N - 1));

let idx = 0, timer = null;
function render() {
  deckgl.setProps({layers: [basemap, shadeLayer(idx), buildings]});
  const f = P.frames[idx] || {};
  document.getElementById('t_time').textContent = f.label || '--';
  document.getElementById('t_alt').textContent = (f.sun_alt != null ? f.sun_alt : '--');
  document.getElementById('t_pct').textContent = (f.shade_pct != null ? f.shade_pct : '--');
  slider.value = String(idx);
}
function stop() { if (timer) { clearInterval(timer); timer = null; } playBtn.innerHTML = '&#9654;'; }
function play() {
  if (N <= 1) return;
  playBtn.innerHTML = '&#10073;&#10073;';
  timer = setInterval(() => { idx = (idx + 1) % N; render(); }, P.intervalMs);
}
playBtn.onclick = () => { timer ? stop() : play(); };
slider.oninput = () => { stop(); idx = parseInt(slider.value, 10) || 0; render(); };

// Start near solar noon (middle of the day) for a sensible first frame.
idx = Math.floor(N / 2);
render();
</script>
</body>
</html>
"""
