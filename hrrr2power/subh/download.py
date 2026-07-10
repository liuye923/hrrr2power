"""Core FastHerbie download machinery for HRRR subh (subhourly) fxx=1 subsets.

Downloads only matching bytes from each GRIB2 file (byte-range HTTP GET),
NOT the full ~300 MB subh file, then moves Herbie's hashed subset files
(subset_<hash>__...) out of the save_dir/hrrr/YYYYMMDD/ layout Herbie
defaults to, and into one folder per variable:

    <save_dir>/<name>/YYYYMMDD/hrrr.tHHz.wrfsubhf01.grib2

Each downloaded file already corresponds to one variable (not one valid
time) - HRRR subh f01 packs all 4 sub-hourly steps (:15/:30/:45/:60) into a
single set of messages per variable, so one download call retrieves the
whole day-hour's worth of that variable's sub-hourly steps.
"""

import time
from multiprocessing import Pool
from pathlib import Path
from typing import Any, cast

import eccodes
import pandas as pd
from herbie.fast import FastHerbie
from tqdm import tqdm

from hrrr2power.logging_utils import redirect_stdout_to_file

# HRRR subh only has fxx=1 for the 15-min sub-hourly steps.
# All four 15-min valid times (+15, +30, +45, +60 min) are packed in fxx=1.
FXX = [1]

# Below this size (bytes) a downloaded file is treated as corrupt/truncated,
# not a real subset. Herbie's subset() shells out to `curl` via os.system(),
# which does NOT check curl's exit code - a network hiccup mid-transfer can
# silently leave a 0-byte or truncated file that no Python exception ever
# flags, and which would otherwise pass an exists()-only skip check forever.
MIN_VALID_SIZE = 1000


def is_href_error(exc: Exception) -> bool:
    """Return True when exception looks like Herbie's transient missing-href error."""
    msg = str(exc)
    return "'href'" in msg or '"href"' in msg


def is_valid_grib_file(path: Path) -> bool:
    """Full integrity check: actually parses every GRIB message in the file
    via eccodes. A header/footer-only check (magic bytes + trailing 7777)
    only catches a transfer truncated at the very end - each of these files
    is 4 sub-hourly messages appended by separate curl calls, so a message
    truncated or corrupted in the *middle* while a later append still
    completed fine would pass a header/footer check but fail here
    (confirmed in practice: found 7 such files out of 61320 that a
    header/footer check missed).

    Costs ~60ms/file (parsing, not just an open()) vs ~0.02ms for a stat -
    only worth paying once, right after a fresh download (network I/O
    already dwarfs it) or during a standalone verify-subh audit pass. The
    day-to-day skip-check (file_already_downloaded) trusts this already
    ran once and does not call this.
    """
    if not path.exists() or path.stat().st_size < MIN_VALID_SIZE:
        return False
    try:
        with open(path, "rb") as f:
            n_messages = 0
            while True:
                gid = eccodes.codes_grib_new_from_file(f)
                if gid is None:
                    break
                eccodes.codes_release(gid)
                n_messages += 1
        return n_messages > 0
    except Exception:
        return False


def file_already_downloaded(path: Path) -> bool:
    """Cheap skip-check: existence + size only, no open().

    Trusts that anything on disk already passed is_valid_grib_file() right
    after being downloaded (see run_fastherbie_download). Re-opening every
    already-downloaded file on every run to re-verify that was the actual
    cost of the old skip-check: ~23ms/file (one open() on this network
    filesystem) vs ~0.02ms/file for a stat - over thousands of files that's
    minutes spent re-checking files nothing has touched since they were
    validated. Run verify-subh to periodically re-check for corruption
    that happened after the fact (e.g. this file getting truncated by an
    unrelated process).
    """
    return path.exists() and path.stat().st_size >= MIN_VALID_SIZE


def expected_output_path(init_time: pd.Timestamp, search_name: str, save_dir: Path) -> Path:
    """Final on-disk path for a given (init_time, variable), post-relocate."""
    return (
        save_dir
        / search_name
        / f"{init_time:%Y%m%d}"
        / f"hrrr.t{init_time.hour:02d}z.wrfsubhf01.grib2"
    )


def relocate_subset_file(path: Path, search_name: str, save_dir: Path) -> Path:
    """Move Herbie's hashed subset file into <save_dir>/<search_name>/YYYYMMDD/.

    Herbie saves to <save_dir>/hrrr/YYYYMMDD/subset_<hash>__hrrr.tHHz.wrfsubhf01.grib2.
    Since each subset file already corresponds to one variable (not one valid
    time), use the variable name as the top-level folder instead of a
    cryptic hash, and drop the "hrrr/" model-name level Herbie inserts. The
    YYYYMMDD folder Herbie already created is reused as-is.
    """
    date_str = path.parent.name
    orig_name = path.name.split("__", 1)[-1]
    new_dir = save_dir / search_name / date_str
    new_dir.mkdir(parents=True, exist_ok=True)
    new_path = new_dir / orig_name
    path.rename(new_path)
    try:
        path.parent.rmdir()
    except OSError:
        pass  # not empty (other variables still downloading into it) - fine
    return new_path


def run_fastherbie_download(
    init_times: list[pd.Timestamp],
    fxx: list[int],
    search_string: str,
    search_name: str,
    workers: int,
    retries: int,
    retry_wait: float,
    save_dir: Path,
    log_file: Path,
) -> bool:
    """Run FastHerbie download with retry. Returns True if completed successfully.

    Herbie's own verbose download messages (colored banners, curl commands,
    per-message dumps from verbose=True) are redirected to log_file instead
    of the console - only hrrr2power's own progress lines print here.
    """
    max_attempts = max(1, retries + 1)
    last_error_was_href = False

    for attempt in range(1, max_attempts + 1):
        try:
            current_workers = max(1, workers // 2) if (attempt > 1 and last_error_was_href) else workers
            with redirect_stdout_to_file(log_file):
                fh = FastHerbie(
                    cast(Any, init_times),
                    model="hrrr",
                    product="subh",
                    fxx=fxx,
                    max_threads=current_workers,
                )

                out_files = fh.download(
                    searchString=search_string,
                    save_dir=save_dir,
                    verbose=True,
                    max_threads=current_workers,
                )
            relocated = [
                relocate_subset_file(Path(f), search_name, save_dir=save_dir)
                for f in out_files if f is not None
            ]

            invalid = [p for p in relocated if not is_valid_grib_file(p)]
            if invalid:
                for p in invalid:
                    p.unlink(missing_ok=True)
                raise RuntimeError(
                    f"{len(invalid)} empty/truncated file(s) downloaded for {search_name}: {invalid}"
                )

            return True
        except Exception as exc:
            if attempt >= max_attempts:
                print(
                    f"FastHerbie failed for {search_name} after {max_attempts} "
                    f"attempt(s): {exc}"
                )
                return False

            label = "href error" if is_href_error(exc) else "error"
            last_error_was_href = is_href_error(exc)
            print(
                f"FastHerbie {label} for {search_name} on attempt "
                f"{attempt}/{max_attempts}; retrying in {retry_wait:.1f}s..."
            )
            time.sleep(retry_wait)

    return False


def _download_one_task(task: tuple) -> tuple[str, pd.Timestamp, bool]:
    """Worker for the flattened (variable, hour) task pool.

    Module-level (not a closure) so multiprocessing can pickle it. Each
    task downloads exactly one (variable, hour) pair, so FastHerbie's own
    max_threads is moot here (a 1-item DATES list) - the real concurrency
    comes from how many of these tasks the outer Pool in download_one_day
    runs at once. That flattens what used to be two nested layers (one
    variable at a time, threaded across its hours) into a single layer:
    a worker per (variable, hour) pair. Pooling only across variables
    degenerates to serial when there's just one variable, or stalls on
    whichever variable has the most missing hours while other workers
    sit idle; flattening across both dimensions keeps every worker busy
    regardless of how the missing data happens to be distributed -
    benchmarked ~3x faster than the old sequential-variables approach.
    """
    search_name, search_string, init_time, retries, retry_wait, save_dir, log_file = task
    ok = run_fastherbie_download(
        init_times=[init_time],
        fxx=FXX,
        search_string=search_string,
        search_name=search_name,
        workers=1,
        retries=retries,
        retry_wait=retry_wait,
        save_dir=save_dir,
        log_file=log_file,
    )
    return search_name, init_time, ok


def download_one_day(
    day: pd.Timestamp,
    init_hours: list[int],
    workers: int,
    retries: int,
    retry_wait: float,
    save_dir: Path,
    search_patterns: list[tuple[str, str]],
    log_file: Path,
    overwrite: bool = False,
) -> tuple[int, int, int]:
    """Download one day and return (expected_requests, failed_requests, skipped_requests)."""
    init_times = [day + pd.Timedelta(hours=h) for h in init_hours]
    expected_requests = len(init_times) * len(FXX) * len(search_patterns)

    print(f"\n=== {day.strftime('%Y-%m-%d')} ===")
    print(f"Init hours   : {init_hours}")
    print(f"Forecast hrs : {FXX}  (subh fxx=1 contains all 4 x 15-min steps)")
    print(f"Variables    : {[name for name, _ in search_patterns]}")
    print(f"Planned requests: {expected_requests}")

    tasks = []
    skipped_requests = 0
    for search_name, search_string in search_patterns:
        if overwrite:
            pending_times = init_times
        else:
            pending_times = [
                t for t in init_times
                if not file_already_downloaded(expected_output_path(t, search_name, save_dir=save_dir))
            ]
        skipped_requests += len(init_times) - len(pending_times)
        tasks.extend(
            (search_name, search_string, t, retries, retry_wait, save_dir, log_file)
            for t in pending_times
        )

    print(f"Pending requests: {len(tasks)} ({skipped_requests} already downloaded, skipped)")

    if not tasks:
        return expected_requests, 0, skipped_requests

    failed_requests = 0
    with Pool(min(workers, len(tasks))) as pool:
        for search_name, init_time, ok in tqdm(
            pool.imap_unordered(_download_one_task, tasks),
            total=len(tasks),
            desc=day.strftime("%Y-%m-%d"),
        ):
            if not ok:
                failed_requests += 1
                print(
                    f"SKIP {search_name} {init_time.strftime('%Y-%m-%d %H:%M')} "
                    f"f{FXX[0]:02d} after retries."
                )

    return expected_requests, failed_requests, skipped_requests


def download_range(days: list[pd.Timestamp], settings: dict) -> tuple[int, int, int]:
    """Download every day in `days` using a resolved settings dict.

    `settings` is the dict returned by hrrr2power.subh.config.resolve_download_settings():
    save_dir, init_hours, workers, retries, retry_wait, overwrite, search_patterns,
    log_file (defaults to <save_dir>/herbie_download.log if not set by the caller).
    Returns totals across all days: (expected, failed, skipped).
    """
    log_file = settings.get("log_file") or (Path(settings["save_dir"]) / "herbie_download.log")

    total_expected = total_failed = total_skipped = 0
    for day in days:
        expected, failed, skipped = download_one_day(
            day=day,
            init_hours=settings["init_hours"],
            workers=settings["workers"],
            retries=settings["retries"],
            retry_wait=settings["retry_wait"],
            overwrite=settings["overwrite"],
            save_dir=settings["save_dir"],
            search_patterns=settings["search_patterns"],
            log_file=log_file,
        )
        total_expected += expected
        total_failed += failed
        total_skipped += skipped
    return total_expected, total_failed, total_skipped
