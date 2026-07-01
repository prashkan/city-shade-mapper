"""Pre-warm the shade-frame caches for a day's daytime timestamps.

The app computes shade on first use of each timestamp (~10-30 s). Running this
once makes every step of the time slider (and the ▶ Play animation) load
instantly. Because each frame is independent, the work is parallelised across
CPU cores.

Examples
--------
    python scripts/precompute.py                    # today, 06:00-21:00 (Toronto)
    python scripts/precompute.py --days 3           # today + next 2 days
    python scripts/precompute.py --workers 8        # cap parallelism
    python scripts/precompute.py --date 2024-06-21  # a specific day
    python scripts/precompute.py --year 2024        # one rep day per week (whole year)
    python scripts/precompute.py --no-trees         # buildings-only frames
    python scripts/precompute.py --neighbourhood "Annex" --year 2024   # another area
"""
from __future__ import annotations

import argparse
import os

# Pin numeric/geo libraries to a single thread *per worker* so that running many
# workers in parallel doesn't oversubscribe cores. Must be set before
# numpy/GDAL import in the (forked) workers.
for _v in (
    "OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS",
    "NUMEXPR_NUM_THREADS", "VECLIB_MAXIMUM_THREADS", "GDAL_NUM_THREADS",
):
    os.environ.setdefault(_v, "1")

import sys
import time
import warnings
from concurrent.futures import ProcessPoolExecutor, as_completed
from datetime import datetime, timedelta
from pathlib import Path

warnings.filterwarnings("ignore")

# Allow running as `python scripts/precompute.py` from the repo root.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import config  # noqa: E402


def _build_one(iso: str, include_trees: bool, nb_name: str) -> tuple[str, float]:
    """Worker: build (and cache) the shade frame for one timestamp."""
    import warnings as _w

    _w.filterwarnings("ignore")
    from src import frames, neighbourhoods

    nb = neighbourhoods.get(nb_name)
    t = time.time()
    frames.build_frame(
        nb, when=datetime.fromisoformat(iso), include_trees=include_trees, use_cache=True
    )
    return iso, time.time() - t


def main() -> None:
    import config as _c  # noqa: F401 (ensure importable in child)
    from src import neighbourhoods

    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--date", default=None, help="YYYY-MM-DD (Toronto). Default: today.")
    ap.add_argument("--days", type=int, default=1, help="Consecutive days from --date.")
    ap.add_argument(
        "--year", type=int, default=None,
        help="Warm one representative day per weekly solar bucket for this year "
             "(makes every date in the year instant). Overrides --date/--days.",
    )
    ap.add_argument("--no-trees", action="store_true", help="Skip tree-canopy shade.")
    ap.add_argument(
        "--neighbourhood", default=neighbourhoods.DEFAULT_NEIGHBOURHOOD,
        help="Toronto neighbourhood name to warm (default: %(default)s).",
    )
    ap.add_argument(
        "--step", type=int, default=config.FRAME_STEP_MINUTES,
        help=f"Frame step in minutes (default {config.FRAME_STEP_MINUTES}). Use a "
             "smaller value to warm the finer 'Smoothness' settings.",
    )
    ap.add_argument(
        "--workers", type=int, default=0,
        help="Parallel processes (default: min(cores, #timestamps)).",
    )
    args = ap.parse_args()

    include_trees = not args.no_trees
    step = args.step
    nb = neighbourhoods.get(args.neighbourhood)  # validate name up front

    jobs: list[str] = []
    if args.year is not None:
        # One representative day per weekly solar bucket. solar_bucket() snaps
        # any date in a bucket to the same cache key, so warming the bucket's
        # midpoint day covers every date in that ~week.
        jan1 = datetime(args.year, 1, 1, tzinfo=config.TORONTO_TZ)
        n_days = 366 if (jan1.replace(month=12, day=31).timetuple().tm_yday == 366) else 365
        seen: set[str] = set()
        for bucket in range((n_days + config.SOLAR_BUCKET_DAYS - 1) // config.SOLAR_BUCKET_DAYS):
            rep_doy = bucket * config.SOLAR_BUCKET_DAYS + 1 + config.SOLAR_BUCKET_DAYS // 2
            rep_doy = min(rep_doy, n_days)
            day = jan1 + timedelta(days=rep_doy - 1)
            for t in config.day_frames(day, step):
                iso = config.solar_bucket(t).isoformat()
                if iso not in seen:
                    seen.add(iso)
                    jobs.append(iso)
        scope = f"{nb.name}, year {args.year}: {len(seen)} daylight frames across ~52 weekly buckets ({step} min)"
    else:
        base = (
            datetime.now(config.TORONTO_TZ).date()
            if not args.date
            else datetime.strptime(args.date, "%Y-%m-%d").date()
        )
        for di in range(args.days):
            day = datetime(base.year, base.month, base.day, tzinfo=config.TORONTO_TZ) + timedelta(days=di)
            for t in config.day_frames(day, step):
                jobs.append(t.isoformat())
        scope = (
            f"{nb.name}, {base} +{args.days - 1}d, {config.FRAME_START_HOUR:02d}:00-"
            f"{config.FRAME_END_HOUR:02d}:00 (daylight) every {step} min"
        )

    # Warm the neighbourhood's buildings (and canopy) caches single-threaded first,
    # so parallel workers don't race to download/build them concurrently.
    print(f"Preparing {nb.name} (buildings + canopy)…", flush=True)
    from src import frames as _frames

    _frames.build_frame(nb, when=datetime.fromisoformat(jobs[len(jobs) // 2]), include_trees=include_trees)

    workers = args.workers or min(os.cpu_count() or 4, len(jobs))
    print(
        f"Pre-warming {len(jobs)} timestamps across {workers} workers "
        f"({scope}, trees={'on' if include_trees else 'off'})…",
        flush=True,
    )

    t0 = time.time()
    done = 0
    with ProcessPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(_build_one, iso, include_trees, nb.name): iso for iso in jobs}
        for fut in as_completed(futures):
            iso, secs = fut.result()
            done += 1
            label = datetime.fromisoformat(iso).strftime("%Y-%m-%d %H:%M")
            print(f"[{done}/{len(jobs)}] {label} cached in {secs:5.1f}s", flush=True)

    print(f"Done. {len(jobs)} timestamps ready in {time.time() - t0:.0f}s wall-clock.", flush=True)


if __name__ == "__main__":
    main()
