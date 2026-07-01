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
    python scripts/precompute.py --no-trees         # buildings-only frames
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


def _build_one(iso: str, include_trees: bool) -> tuple[str, float]:
    """Worker: build (and cache) the shade frame for one timestamp."""
    import warnings as _w

    _w.filterwarnings("ignore")
    from src import frames

    t = time.time()
    frames.build_frame(
        when=datetime.fromisoformat(iso), include_trees=include_trees, use_cache=True
    )
    return iso, time.time() - t


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--date", default=None, help="YYYY-MM-DD (Toronto). Default: today.")
    ap.add_argument("--days", type=int, default=1, help="Consecutive days from --date.")
    ap.add_argument("--no-trees", action="store_true", help="Skip tree-canopy shade.")
    ap.add_argument(
        "--workers", type=int, default=0,
        help="Parallel processes (default: min(cores, #timestamps)).",
    )
    args = ap.parse_args()

    base = (
        datetime.now(config.TORONTO_TZ).date()
        if not args.date
        else datetime.strptime(args.date, "%Y-%m-%d").date()
    )
    include_trees = not args.no_trees

    jobs: list[str] = []
    for di in range(args.days):
        day = datetime(base.year, base.month, base.day, tzinfo=config.TORONTO_TZ) + timedelta(days=di)
        for t in config.day_frames(day):
            jobs.append(t.isoformat())

    workers = args.workers or min(os.cpu_count() or 4, len(jobs))
    print(
        f"Pre-warming {len(jobs)} timestamps across {workers} workers "
        f"({base} +{args.days - 1}d, {config.FRAME_START_HOUR:02d}:00-"
        f"{config.FRAME_END_HOUR:02d}:00 every {config.FRAME_STEP_MINUTES} min, "
        f"trees={'on' if include_trees else 'off'})…",
        flush=True,
    )

    t0 = time.time()
    done = 0
    with ProcessPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(_build_one, iso, include_trees): iso for iso in jobs}
        for fut in as_completed(futures):
            iso, secs = fut.result()
            done += 1
            label = datetime.fromisoformat(iso).strftime("%Y-%m-%d %H:%M")
            print(f"[{done}/{len(jobs)}] {label} cached in {secs:5.1f}s", flush=True)

    print(f"Done. {len(jobs)} timestamps ready in {time.time() - t0:.0f}s wall-clock.", flush=True)


if __name__ == "__main__":
    main()
