"""Core FastHerbie download machinery, shared by every HRRR product
(hrrr2power.subh, hrrr2power.prs, ...). Nothing in this file is specific to
any one product - each product's own download.py picks its constants
(fxx, expected_message_count, filename_template, product name) and passes
them into the functions here.

Downloads only matching bytes from each GRIB2 file (byte-range HTTP GET),
NOT the full file, then moves Herbie's hashed subset files
(subset_<hash>__...) out of the save_dir/hrrr/YYYYMMDD/ layout Herbie
defaults to, and into one folder per variable:

    <save_dir>/<name>/YYYYMMDD/<filename_template>

Each downloaded file already corresponds to one variable (not one valid
time).
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


def is_valid_grib_file(path: Path, expected_message_count: int = 4) -> bool:
    """Full integrity check: actually parses every GRIB message in the file
    via eccodes, and requires all `expected_message_count` of them to be
    present. A header/footer-only check (magic bytes + trailing 7777) only
    catches a transfer truncated at the very end - subh's files are 4
    sub-hourly messages appended by separate curl calls, so a message
    truncated or corrupted in the *middle* while a later append still
    completed fine would pass a header/footer check but fail here
    (confirmed in practice: found 7 such files out of 61320 that a
    header/footer check missed). Requiring the full message count on top of
    that also catches a whole message going missing - e.g. one of several
    curl calls silently failing - which every-message-parses-fine alone
    can't detect, since nothing about the remaining messages looks wrong
    (confirmed in practice: found downloaded files missing exactly one
    message that silently produced a NaN gap in extracted output).

    Costs ~60ms/file (parsing, not just an open()) vs ~0.02ms for a stat -
    only worth paying once, right after a fresh download (network I/O
    already dwarfs it) or during a standalone verify-subh audit pass. The
    day-to-day skip-check (file_already_downloaded) trusts this already
    ran once and does not call this.

    `expected_message_count` defaults to 4 (subh's value, kept as the
    default so any caller that doesn't pass it explicitly keeps today's
    exact behavior) - other products pass their own count (e.g. prs's
    single-message-per-file analysis fields pass 1).
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
        return n_messages == expected_message_count
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


def expected_output_path(
    init_time: pd.Timestamp, search_name: str, save_dir: Path, filename_template: str, fxx: int
) -> Path:
    """Final on-disk path for a given (init_time, variable, fxx), post-relocate.

    filename_template is a str.format() template taking `hour` (int) and
    `fxx` (int), e.g. "hrrr.t{hour:02d}z.wrfprsf{fxx:02d}.grib2".
    """
    return (
        save_dir
        / search_name
        / f"{init_time:%Y%m%d}"
        / filename_template.format(hour=init_time.hour, fxx=fxx)
    )


def _parse_local_idx(idx_path: Path) -> pd.DataFrame:
    """Parse a local NOAA/wgrib2-style plain-text .idx sidecar (e.g. one
    fetched by 01_download_from_aws.py/backfill_idx.py) into the same shape
    Herbie.inventory() returns (start_byte/end_byte/search_this) - lets
    extract_from_local_archive() find a message's byte range with zero
    network requests, instead of Herbie's own .inventory(), which always
    fetches the idx over HTTP regardless of whether the grib2 file itself
    is local.

    Mirrors Herbie's own idx parsing exactly (herbie.core.Herbie.
    index_as_dataframe's "wgrib2" branch) so the same search_string
    patterns already used throughout this package (matched against idx
    lines like ":TMP:2 m above ground:") work unchanged here. A message's
    end_byte is inferred from the *next* message's start (idx lines only
    give a start offset) - the last message in the file gets NaN, meaning
    "read to EOF".
    """
    df = pd.read_csv(
        idx_path, sep=":",
        names=["grib_message", "start_byte", "reference_time", "variable",
               "level", "forecast_time", "?", "??", "???"],
    )
    df["start_byte"] = df["start_byte"].astype(int)
    df["end_byte"] = df["start_byte"].shift(-1) - 1
    # Reorder so end_byte comes *before* variable - df.loc[:, "variable":]
    # below takes every column from "variable" to the last one to build
    # search_this, and end_byte was just appended at the end by the
    # assignment above, so without this reindex it would leak into the
    # search string (confirmed in practice: search_this came out as
    # ":TMP:2 m above ground:anl:304538232.0" instead of stopping at "anl:").
    df = df.reindex(columns=[
        "grib_message", "start_byte", "end_byte", "reference_time",
        "variable", "level", "forecast_time", "?", "??", "???",
    ])
    df = df.dropna(how="all", axis=1)
    df["search_this"] = (
        df.loc[:, "variable":]
        .astype(str)
        .apply(lambda x: ":" + ":".join(x).rstrip(":").replace(":nan:", ":"), axis=1)
    )
    return df


def count_matching_messages(
    sample_date: pd.Timestamp,
    product: str,
    fxx: int,
    search_string: str,
    local_archive_dir: Path | None = None,
    local_archive_filename_template: str | None = None,
) -> int:
    """Count how many grib messages a search_string pattern matches for one
    sample hour - lets a variable's expected message count be computed
    automatically (e.g. all 40 pressure levels for a wildcard level regex
    like r":UGRD:\\d+(\\.\\d+)? mb:") instead of needing to be hand-counted
    and hardcoded per variable. Uses the same local-.idx-first-then-Herbie-
    .inventory()-fallback lookup as extract_from_local_archive, so it costs
    zero network requests whenever a local .idx is already available.
    """
    if local_archive_dir is not None and local_archive_filename_template is not None:
        archive_file = (
            Path(local_archive_dir) / f"{sample_date:%Y%m%d}"
            / local_archive_filename_template.format(hour=sample_date.hour, fxx=fxx)
        )
        local_idx = Path(f"{archive_file}.idx")
        if local_idx.exists():
            idx_df = _parse_local_idx(local_idx)
            return int(idx_df["search_this"].str.contains(search_string, regex=True).sum())

    from herbie import Herbie
    H = Herbie(sample_date, model="hrrr", product=product, fxx=fxx, verbose=False)
    return len(H.inventory(search_string))


def extract_from_local_archive(
    archive_file: Path,
    herbie_date: pd.Timestamp,
    product: str,
    fxx: int,
    search_string: str,
    out_path: Path,
    expected_message_count: int,
) -> str | None:
    """Extract one variable's message(s) for one (date, hour, fxx) directly
    out of an already-downloaded full grib2 file, instead of downloading a
    byte-range subset over HTTP - skips the network data transfer entirely
    (the full file, hundreds of MB, already sits on local disk).

    Finds the message's byte range from a local .idx sidecar next to
    archive_file if one exists (see _parse_local_idx - zero network
    requests), otherwise falls back to Herbie's own .inventory() (a tiny
    (KB) remote request for the idx text, not the actual grib data).
    Either way this reuses the exact same idx format/search-string
    matching Herbie itself uses rather than inventing a new one.

    Verified against real data (2025-06-01 00z prs t2m): a message
    extracted this way decodes to the exact same site-pixel value as the
    equivalent Herbie network subset download (304.47833 K, byte-identical
    source range) - true for both the local-idx and Herbie-inventory
    lookup paths. Opening the *whole* archive file with cfgrib instead of
    doing a byte-range single-message extraction was measured ~35-140s/file
    (depends on whether cfgrib's own on-disk idx cache, which this package
    otherwise disables, happens to be reusable) - this function never does
    that; it only ever reads the specific message bytes needed, which
    decode exactly as fast as any other single-message subset file.

    Returns "local_idx"/"local_inventory" (whichever byte-range lookup
    succeeded) on success, so the caller can report which one actually ran
    - lets download_one_day's progress bar show, live, whether a run is
    genuinely hitting zero-network local reads or still paying for a
    per-file Herbie.inventory() request. Returns None on any failure
    (message not in this file's inventory, read/decode error, wrong
    message count) so the caller falls back to a normal network download -
    a local archive is meant to be pure upside, never a new way to fail a
    download that would otherwise have succeeded.
    """
    try:
        local_idx = Path(f"{archive_file}.idx")
        if local_idx.exists():
            idx_df = _parse_local_idx(local_idx)
            idx_df = idx_df[idx_df["search_this"].str.contains(search_string, regex=True)]
            method = "local_idx"
        else:
            from herbie import Herbie

            with redirect_stdout_to_file(out_path.parent / "herbie_local_archive.log"):
                H = Herbie(herbie_date, model="hrrr", product=product, fxx=fxx, verbose=False)
                idx_df = H.inventory(search_string)
            method = "local_inventory"
        if idx_df.empty:
            return None

        # A multi-message search_string (e.g. a wildcard level regex
        # matching all 40 pressure levels) does NOT match contiguous bytes
        # in the file - HRRR interleaves every variable within each level,
        # so one variable's messages across levels are scattered throughout
        # the whole file. A single min-to-max read would pull in every
        # *other* variable's bytes in between too (confirmed in practice:
        # 40 non-contiguous U-wind messages spanned 256MB of a 405MB file,
        # i.e. ~63% of everything else in it), which is not just wasteful
        # but fails the expected_message_count check outright since the
        # read would contain hundreds of unrelated messages. Mirror
        # Herbie's own download() grouping instead (idx_df.grib_message.
        # diff().ne(1).cumsum()): only messages with strictly consecutive
        # grib_message numbers are truly adjacent in the file, so group by
        # that and do one read per contiguous run, concatenating the
        # results - same as Herbie appending multiple curl calls into one
        # output file, just via local seeks instead of HTTP ranges.
        idx_df = idx_df.sort_values("grib_message")
        groups = idx_df["grib_message"].diff().ne(1).cumsum()

        out_path.parent.mkdir(parents=True, exist_ok=True)
        with open(archive_file, "rb") as fin, open(out_path, "wb") as fout:
            for _, group in idx_df.groupby(groups):
                start_byte = int(group["start_byte"].min())
                end_byte = group["end_byte"].max()
                fin.seek(start_byte)
                if pd.isna(end_byte):
                    # Last message in the file - a wgrib2-style idx infers
                    # each message's end from the *next* message's start,
                    # so there's nothing to infer from for the final one;
                    # read to EOF.
                    fout.write(fin.read())
                else:
                    fout.write(fin.read(int(end_byte) - start_byte + 1))

        if not is_valid_grib_file(out_path, expected_message_count):
            out_path.unlink(missing_ok=True)
            return None
        return method
    except Exception:
        out_path.unlink(missing_ok=True)
        return None


def relocate_subset_file(path: Path, search_name: str, save_dir: Path) -> Path:
    """Move Herbie's hashed subset file into <save_dir>/<search_name>/YYYYMMDD/.

    Herbie saves to <save_dir>/hrrr/YYYYMMDD/subset_<hash>__<orig_name>.
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
    product: str,
    expected_message_count: int,
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
                    product=product,
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

            invalid = [p for p in relocated if not is_valid_grib_file(p, expected_message_count)]
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


def _download_one_task(task: tuple) -> tuple[str, pd.Timestamp, int, bool, str]:
    """Worker for the flattened (variable, hour, fxx) task pool.

    Module-level (not a closure) so multiprocessing can pickle it. Each
    task downloads exactly one (variable, hour, fxx) triple, so FastHerbie's
    own max_threads is moot here (a 1-item DATES list) - the real
    concurrency comes from how many of these tasks the outer Pool in
    download_one_day runs at once. That flattens what used to be nested
    layers (one variable at a time, threaded across its hours) into a
    single layer: a worker per (variable, hour, fxx) triple. Pooling only
    across variables degenerates to serial when there's just one variable,
    or stalls on whichever variable has the most missing hours while other
    workers sit idle; flattening across every dimension keeps every worker
    busy regardless of how the missing data happens to be distributed -
    benchmarked ~3x faster than the old sequential-variables approach.

    If local_archive_dir is set and the matching full-file archive entry
    exists for this (date, hour, fxx), tries extract_from_local_archive()
    first - falls through to the normal network download on any failure
    (message not found, read/decode error), so a misconfigured or
    partial archive can never make a download fail that would otherwise
    have succeeded over the network.

    The returned `source` is one of "local_idx" (local file + local .idx,
    zero network), "local_inventory" (local file, but the byte range came
    from Herbie's network .inventory() since no local .idx existed), or
    "network" (no local_archive_dir hit at all - the normal byte-range
    subset download) - download_one_day's progress bar reports live counts
    of each so a run's actual data source is visible while it's happening,
    not just guessed at from how fast/slow it feels.
    """
    (search_name, search_string, init_time, fxx_value, retries, retry_wait, save_dir, log_file,
     product, expected_message_count, filename_template, local_archive_dir,
     local_archive_filename_template) = task

    if local_archive_dir is not None and local_archive_filename_template is not None:
        archive_file = (
            Path(local_archive_dir) / f"{init_time:%Y%m%d}"
            / local_archive_filename_template.format(hour=init_time.hour, fxx=fxx_value)
        )
        if archive_file.exists():
            out_path = expected_output_path(init_time, search_name, save_dir, filename_template, fxx_value)
            method = extract_from_local_archive(
                archive_file=archive_file,
                herbie_date=init_time,
                product=product,
                fxx=fxx_value,
                search_string=search_string,
                out_path=out_path,
                expected_message_count=expected_message_count,
            )
            if method is not None:
                return search_name, init_time, fxx_value, True, method

    ok = run_fastherbie_download(
        init_times=[init_time],
        fxx=[fxx_value],
        search_string=search_string,
        search_name=search_name,
        workers=1,
        retries=retries,
        retry_wait=retry_wait,
        save_dir=save_dir,
        log_file=log_file,
        product=product,
        expected_message_count=expected_message_count,
    )
    return search_name, init_time, fxx_value, ok, "network"


def download_one_day(
    day: pd.Timestamp,
    init_hours: list[int],
    workers: int,
    retries: int,
    retry_wait: float,
    save_dir: Path,
    search_patterns: list[tuple[str, str, int | None]],
    log_file: Path,
    product: str,
    fxx: list[int],
    expected_message_count: int,
    filename_template: str,
    overwrite: bool = False,
    local_archive_dir: Path | None = None,
    local_archive_filename_template: str | None = None,
) -> tuple[int, int, int]:
    """Download one day and return (expected_requests, failed_requests, skipped_requests).

    Each search_patterns entry is (name, search_string, message_count) -
    message_count is None for the common one-message-per-file case (uses
    expected_message_count, the product-wide default) or an explicit int
    for a variable whose search_string matches more than one message (e.g.
    a wildcard level regex matching every pressure/hybrid level at once -
    see hrrr2power.config._normalize_pattern).
    """
    init_times = [day + pd.Timedelta(hours=h) for h in init_hours]
    expected_requests = len(init_times) * len(fxx) * len(search_patterns)

    print(f"\n=== {day.strftime('%Y-%m-%d')} ===")
    print(f"Init hours   : {init_hours}")
    print(f"Product      : {product}  fxx={fxx}")
    print(f"Variables    : {[name for name, _, _ in search_patterns]}")
    if local_archive_dir is not None:
        print(f"Local archive: {local_archive_dir}  (tried first per file, falls back to network)")
    print(f"Planned requests: {expected_requests}")

    tasks = []
    skipped_requests = 0
    for search_name, search_string, message_count in search_patterns:
        effective_message_count = message_count if message_count is not None else expected_message_count
        for fxx_value in fxx:
            if overwrite:
                pending_times = init_times
            else:
                pending_times = [
                    t for t in init_times
                    if not file_already_downloaded(
                        expected_output_path(
                            t, search_name, save_dir=save_dir,
                            filename_template=filename_template, fxx=fxx_value,
                        )
                    )
                ]
            skipped_requests += len(init_times) - len(pending_times)
            tasks.extend(
                (search_name, search_string, t, fxx_value, retries, retry_wait, save_dir, log_file,
                 product, effective_message_count, filename_template, local_archive_dir,
                 local_archive_filename_template)
                for t in pending_times
            )

    print(f"Pending requests: {len(tasks)} ({skipped_requests} already downloaded, skipped)")

    if not tasks:
        return expected_requests, 0, skipped_requests

    failed_requests = 0
    source_counts = {"local_idx": 0, "local_inventory": 0, "network": 0}
    with Pool(min(workers, len(tasks))) as pool:
        progress = tqdm(
            pool.imap_unordered(_download_one_task, tasks),
            total=len(tasks),
            desc=day.strftime("%Y-%m-%d"),
        )
        for search_name, init_time, fxx_value, ok, source in progress:
            if not ok:
                failed_requests += 1
                print(
                    f"SKIP {search_name} {init_time.strftime('%Y-%m-%d %H:%M')} "
                    f"f{fxx_value:02d} after retries."
                )
            else:
                source_counts[source] += 1
            # Live, per-task breakdown of where bytes are actually coming
            # from - local_idx is zero-network, local_inventory still pays
            # for one small Herbie .inventory() request per file, network
            # is the full byte-range subset download. Visible while the
            # bar is running, not just as a summary after the fact, so a
            # run can be confirmed to be hitting the local archive (or
            # not) without waiting for the whole day to finish.
            if local_archive_dir is not None:
                progress.set_postfix(source_counts, refresh=False)

    if local_archive_dir is not None:
        print(f"  (local_idx={source_counts['local_idx']}, "
              f"local_inventory={source_counts['local_inventory']}, "
              f"network={source_counts['network']}, out of {len(tasks)})")

    return expected_requests, failed_requests, skipped_requests


def download_range(
    days: list[pd.Timestamp],
    settings: dict,
    product: str,
    fxx: list[int],
    expected_message_count: int,
    filename_template: str,
) -> tuple[int, int, int]:
    """Download every day in `days` using a resolved settings dict.

    `settings` is the dict returned by hrrr2power.config.resolve_download_settings():
    save_dir, init_hours, workers, retries, retry_wait, overwrite, search_patterns,
    log_file (defaults to <save_dir>/herbie_download.log if not set by the caller).
    `product`/`fxx`/`expected_message_count`/`filename_template` are the
    calling product's own constants (see e.g. hrrr2power.subh.download).
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
            product=product,
            fxx=fxx,
            expected_message_count=expected_message_count,
            filename_template=filename_template,
            local_archive_dir=settings.get("local_archive_dir"),
            local_archive_filename_template=settings.get("local_archive_filename_template"),
        )
        total_expected += expected
        total_failed += failed
        total_skipped += skipped
    return total_expected, total_failed, total_skipped
