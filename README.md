# Toronto Shadow Mapper

An interactive web app that visualises **how building and tree shadows sweep
across a city neighbourhood over the course of a day**. Pilot area: **Liberty
Village / King West, Toronto, Canada**.

Pick a date, then scrub a **time-of-day slider** — or press **▶ Play** — and
watch the ground shade cast by buildings (and tree canopy) rotate and stretch
from morning to evening. Playback runs **client-side** in a single deck.gl
component, so the shadows glide smoothly without the map reloading or losing your
pan/zoom/tilt.

*Forked from [`city-shade-router`](https://github.com/prashkan/city-shade-router):
it reuses that project's shadow engine (building shadows via `pybdshadow`, tree
canopy from the Meta/WRI raster) but drops the pedestrian-routing half in favour
of a time-animated shade visualisation.*

## Features

- ☀️ **Time-animated shadows** — an in-map slider + ▶ Play button sweep the day
  from sunrise to sunset, animated **client-side** with a frame-to-frame crossfade
  for smooth, flicker-free playback (the basemap and camera never reload).
- 🏙️ **3D neighbourhood** — extruded OSM building footprints for spatial context.
- 🌳 **Buildings + trees, or buildings-only** — pick the shade sources; the
  buildings-only mode is lighter and faster.
- 🌅 **Daylight-only timeline** — auto-clamped to that day's real sunrise/sunset
  (shorter in winter, longer in summer); no wasted night compute.
- 📊 **Live metrics** — sun altitude and % of the neighbourhood in shade per moment.
- ⚡ **Frame caching** — shade is computed once per timestamp (per solar bucket)
  and cached, so scrubbing and replaying are instant.

**Stack:** Python · osmnx · geopandas · shapely · pybdshadow · rasterio ·
Streamlit · pydeck.

## How it works

1. **Buildings** (`src/data.py`) — OSM building footprints for the AOI; impute
   heights (3.5 m/level, else 4 m default). Cached to `data/`.
2. **Shadows** (`src/shadows.py`) — solar position for a Toronto-local timestamp;
   building shadows via `pybdshadow`; tree-canopy shade from the Meta/WRI 1 m
   global canopy-height raster (`src/canopy.py`).
3. **Frames** (`src/frames.py`) — union the two shade sources into one ground-shade
   layer per timestamp, clip to the AOI, cache it, and report shaded-area metrics.
4. **Dashboard** (`app.py` + `src/viz.py`) — build every daylight frame for the
   chosen day (as GeoJSON) and hand them to a single deck.gl component on a
   tokenless Carto basemap. A `requestAnimationFrame` loop advances a continuous
   playhead and crossfades the two nearest shade layers each frame, so the day
   animates smoothly client-side with no Streamlit reruns.

## Engineering guardrails

- **CRS safety:** all length / area / overlay math runs in **EPSG:32617**
  (UTM 17N, Toronto). Never compute `.area` in WGS84 degrees. Reproject to 4326
  only for rendering.
- **Night-safe:** when the sun is below the horizon the shade layer is empty and
  the app reports the whole neighbourhood as shaded.
- **Solar-bucket caching:** shade is keyed by a ~weekly solar bucket + timestamp,
  not the exact date — the sun barely moves day to day, so one precompute serves
  the whole week.

## Study area

- Centre `(43.6415, -79.4110)`, ~1.3 km half-extent — covers Liberty Village
  (SW) through King West Village (NE).
- Timezone `America/Toronto`; metric CRS `EPSG:32617` (UTM 17N).

## Setup

```bash
uv venv --python 3.11 .venv && source .venv/bin/activate
uv pip install -r requirements.txt
PYTHONPATH=. python src/data.py      # smoke test: download + cache buildings
python scripts/precompute.py         # optional: pre-warm today's shade frames
streamlit run app.py                 # full app
```

> **Tip:** the first use of each timestamp computes shade (~10-30 s, then
> cached). `scripts/precompute.py` warms frames up front (parallelised across CPU
> cores) so the slider and ▶ Play are instant. Use `--date 2024-06-21` for one
> day, `--days 3` for several, or `--year 2024` to warm one representative day
> per week for the whole year (every date instant).

## Tests

```bash
PYTHONPATH=. pytest -q          # config / timeline unit tests (no network)
```

## Status

- [x] Study area retargeted to Liberty Village / King West, Toronto
- [x] Shade engine (buildings + tree canopy) reused from `city-shade-router`
- [x] Per-timestamp shade frames + caching, clipped to the AOI (`src/frames.py`)
- [x] Daylight-only timeline (sunrise→sunset, season-aware)
- [x] Seamless client-side deck.gl animation with crossfade — in-map slider +
      ▶ Play (`src/viz.py`)
- [x] Buildings + trees / buildings-only shade modes
- [x] Whole-year precompute (`scripts/precompute.py --year`, both modes)
