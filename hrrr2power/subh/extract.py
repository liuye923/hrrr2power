"""Site (point) timeseries extraction from downloaded HRRR subh subset files."""

import warnings
from collections import defaultdict
from multiprocessing import Pool
from pathlib import Path

import cfgrib
import pandas as pd
import xarray as xr

from hrrr2power.config import resolve_days

from .combine import combine_named_arrays
from .config import resolve_download_settings
from .download import download_range

CFGRIB_KWARGS = {
    "decode_times": True,
    "decode_timedelta": False,
    # needed to recover valid_time on subhourly messages cfgrib/eccodes
    # can't decode on its own (see combine.recover_valid_time)
    "read_keys": ["lengthOfTimeRange", "forecastTime", "indicatorOfUnitForTimeRange"],
    # Disable cfgrib's on-disk .idx cache. It writes that cache via an
    # exclusive file-create + separate pickle.dump(); if that gets
    # interrupted (Ctrl-C, or another worker racing to open the same file)
    # it leaves a permanent 0-byte stub - every future open then hits
    # "Can't read index file ...: EOFError: Ran out of input" forever,
    # since cfgrib never rewrites a file that already exists. It falls back
    # to rebuilding the index in-memory when that happens, so extraction
    # still succeeds, but noisily and with a wasted rescan every time.
    # These files are small enough that rebuilding in-memory on every open
    # (indexpath="") is cheap and removes the failure mode entirely.
    "indexpath": "",
}


def collect_variable_names(config: dict) -> list[str]:
    """Collect the output_name of every enabled variable in an extraction config.

    The "variables" section maps {short_name: output_name} per GRIB
    typeOfLevel[_stepType] group - this flattens it to just the output names,
    which double as the download folder names.
    """
    names = []
    for level_config in config.get("variables", {}).values():
        if not level_config.get("enabled", False):
            continue
        names.extend(level_config.get("vars", {}).values())
    return names


def ensure_downloaded(config: dict, workers: int | None = None) -> None:
    """Download any missing grib files for the config's variables/date range.

    Reuses hrrr2power.subh.download.download_range(), which already skips
    files that already exist on disk - so calling this is cheap/idempotent
    when everything is already downloaded.

    Base download settings (save_dir, init_hours, workers, retries,
    retry_wait, overwrite, search_patterns) come from the yaml named by this
    config's download.configure_file (default: config/default_download_subh.yaml);
    this config's own "download:" section can override any of them except
    search_patterns, which is always looked up from the base file by name.

    `workers`, if given, overrides the resolved download.workers - this is
    how extract-site's --workers flag reaches the download step, so one
    flag controls both download and extraction concurrency instead of
    --workers silently only affecting extraction while download stayed
    pinned to whatever the yaml said.
    """
    dl_cfg = config.get("download", {})
    variable_names = collect_variable_names(config)
    settings = resolve_download_settings(dl_cfg, variable_names=variable_names)
    if workers is not None:
        settings["workers"] = workers

    if not settings["search_patterns"]:
        print("No enabled variables - nothing to download.")
        return

    # Extraction and download must agree on where files live.
    settings["save_dir"] = Path(config["grib_dir"])
    settings["log_file"] = settings["log_file"] or (settings["save_dir"] / "herbie_download.log")

    days = resolve_days(config["processing"])

    print(f"Checking/downloading {len(settings['search_patterns'])} variable(s) x "
          f"{len(days)} day(s) x {len(settings['init_hours'])} init hour(s)...")
    print(f"Herbie log: {settings['log_file']} (verbose Herbie output goes here, not the console)")

    expected, failed, skipped = download_range(days, settings)

    print(f"Download check done: {skipped} already present, "
          f"{expected - skipped - failed} newly downloaded, {failed} failed.\n")


def extract_site_data(files: list[Path], sites: pd.DataFrame, config: dict) -> xr.Dataset | None:
    """Open the sibling variable files for one (date, hour) and pull out site pixels."""
    var_arrays = defaultdict(list)
    reference_time = None
    variables_config = config.get("variables", {})

    for f in files:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            datasets = cfgrib.open_datasets(str(f), backend_kwargs=CFGRIB_KWARGS)

        for ds in datasets:
            if reference_time is None:
                reference_time = ds["time"].values

            first_var = list(ds.data_vars)[0]
            level_type = ds[first_var].attrs.get("GRIB_typeOfLevel", "unknown")
            step_type = ds[first_var].attrs.get("GRIB_stepType", "instant")
            config_key = f"{level_type}_{step_type}" if step_type != "instant" else level_type

            level_config = variables_config.get(config_key, {})
            if not level_config.get("enabled", False):
                continue

            vars_config = level_config.get("vars", {})
            vars_to_extract = [v for v in vars_config if v in ds.data_vars]
            if not vars_to_extract:
                continue

            site_data = ds[vars_to_extract].isel(
                x=xr.DataArray(sites["x_index"].values, dims="site"),
                y=xr.DataArray(sites["y_index"].values, dims="site"),
            )
            site_data = site_data.rename({v: vars_config[v] for v in vars_to_extract})
            for name, da in site_data.data_vars.items():
                var_arrays[name].append(da)

    if not var_arrays:
        return None

    combined_vars = combine_named_arrays(var_arrays, reference_time)
    combined = xr.merge(list(combined_vars.values()), compat="override")
    combined = combined.assign_coords(
        site_name=("site", sites["site_name"].values),
        latitude=("site", sites["latitude"].values),
        longitude=("site", sites["longitude"].values),
    )

    if "time" in combined.coords:
        combined = combined.drop_vars("time", errors="ignore")
    if "valid_time" in combined.coords:
        combined = combined.rename({"valid_time": "time"})

    for var in list(combined.coords) + list(combined.data_vars):
        combined[var].attrs.pop("dtype", None)

    return combined


def discover_groups(grib_dir: Path, days: list[pd.Timestamp]):
    """Yield (date_str, hour_str, [sibling files]) for every available hour.

    Globs <grib_dir>/*/<date>/hrrr.t*z.wrfsubhf01.grib2 so it doesn't need to
    know the variable folder names up front - whatever folders exist under
    grib_dir are picked up automatically.
    """
    for day in days:
        date_str = day.strftime("%Y%m%d")
        files = sorted(grib_dir.glob(f"*/{date_str}/hrrr.t*z.wrfsubhf01.grib2"))
        by_hour = defaultdict(list)
        for f in files:
            hour_str = f.name.split(".t")[1][:2]
            by_hour[hour_str].append(f)
        for hour_str, group_files in sorted(by_hour.items()):
            yield date_str, hour_str, group_files


def process_one(task, sites: pd.DataFrame, config: dict, hourly_dir: Path, skip_existing: bool):
    date_str, hour_str, files = task
    out_file = hourly_dir / f"hrrr_sites_{date_str}_{hour_str}z.nc"

    if skip_existing and out_file.exists() and out_file.stat().st_size > 1000:
        return out_file

    # A corrupted/unreadable input grib file must not crash the whole pool -
    # one bad file out of thousands would otherwise take down every other
    # task's in-flight work with it via pool.imap() re-raising on the main
    # process (download.py's per-task retry already isolates failures this
    # way on the download side; extraction didn't).
    try:
        ds = extract_site_data(files, sites, config)
    except Exception as exc:
        print(f"SKIP {date_str} {hour_str}z: failed to read input file(s): {exc}")
        return None

    if ds is None:
        return None

    ds.to_netcdf(out_file)

    # Verify the write actually produced a readable file before trusting it -
    # an interrupted or failed to_netcdf() write can leave a corrupted-but-
    # nonzero-size HDF5 file behind, which the skip_existing exists()+size
    # check above would otherwise trust forever (confirmed in practice: an
    # 8KB corrupted file survived a whole 8760-file run undetected until
    # concat_hourly tried to open it). Checked once here, right after
    # writing, not on every skip_existing check.
    try:
        with xr.open_dataset(out_file):
            pass
    except Exception:
        out_file.unlink(missing_ok=True)
        return None

    return out_file


def _load_one_hourly(path_str: str) -> xr.Dataset:
    """Worker for concat_hourly's parallel load - must be module-level so
    multiprocessing can pickle it. Loads fully into memory (not lazy) and
    closes the source file immediately.
    """
    with xr.open_dataset(path_str) as ds:
        return ds.load()


def concat_hourly(hourly_dir: Path, output_dir: Path, config: dict, workers: int = 8):
    """Concatenate every hourly site-extraction file into one netCDF.

    Site-extracted data is always small (a handful of sites x variables,
    never the full grid) - the whole concatenated dataset comfortably fits
    in memory, so there's no reason to pay for open_mfdataset's dask-based
    lazy machinery here. That machinery is built for data too big to fit
    in memory, and at file counts in the thousands it actively hurts:
    open_mfdataset opens files serially by default, its file-handle LRU
    cache (128 by default) thrashes once file count exceeds that by a lot,
    and building a dask graph node per (tiny) file has its own overhead.
    Measured ~45x faster (26.8s vs 20+ minutes) reading files in parallel
    directly into memory and concatenating with plain xr.concat, for the
    same 8760-file/550MB southern_co case - see concat_hourly_dask() below,
    kept for the different case of concatenating full-grid (not
    site-extracted) data too large to fit in memory, where dask's laziness
    is actually necessary.
    """
    files = sorted(hourly_dir.glob("hrrr_sites_*.nc"))
    if not files:
        print("No hourly files to concatenate.")
        return

    print(f"Concatenating {len(files)} hourly files...")
    with Pool(min(max(workers, 1), len(files))) as pool:
        datasets = pool.map(_load_one_hourly, [str(f) for f in files], chunksize=20)

    ds = xr.concat(datasets, dim="time", coords="minimal", compat="override", data_vars="minimal")
    ds = ds.sortby("time")
    # A plain if/raise, not assert - this check exists specifically to
    # catch silent data corruption, so it must not be a no-op under
    # `python -O`/PYTHONOPTIMIZE, which strips assert statements.
    if not (ds.site_name.values != "").all():
        raise ValueError("site_name empty after concat")

    proc = config.get("processing", {})
    out_file = output_dir / f"hrrr_sites_{proc.get('start_date')}_{proc.get('end_date')}.nc"
    ds.to_netcdf(out_file)
    ds.close()
    print(f"Saved concatenated file -> {out_file}")

    if not config.get("options", {}).get("keep_hourly", True):
        for f in files:
            f.unlink()
        print("Removed hourly files (keep_hourly: false).")


def concat_hourly_dask(hourly_dir: Path, output_dir: Path, config: dict):
    """Dask/open_mfdataset-based concat - NOT used by default (see
    concat_hourly() above), kept for concatenating data too large to fit
    in memory (e.g. full-grid, not site-extracted, output), where dask's
    lazy loading is necessary rather than just unhelpful overhead.
    """
    files = sorted(hourly_dir.glob("hrrr_sites_*.nc"))
    if not files:
        print("No hourly files to concatenate.")
        return

    print(f"Concatenating {len(files)} hourly files...")
    # combine="nested" with an explicit concat_dim trusts the file order we
    # already know is chronological (hrrr_sites_YYYYMMDD_HHz.nc sorts
    # lexicographically = chronologically), instead of combine="by_coords"'s
    # default behavior of inferring sort order and cross-checking coordinate
    # compatibility across every file - which dominates runtime once there
    # are hundreds+ of files (measured ~3x slower, and worsening with file
    # count, vs "nested" here). coords="minimal"/compat="override" skip the
    # equivalent equality checks on scalar coords (site_name, lat/lon, etc.)
    # since every hourly file already shares them by construction.
    ds = xr.open_mfdataset(
        [str(f) for f in files],
        combine="nested",
        concat_dim="time",
        coords="minimal",
        compat="override",
        data_vars="minimal",
    )
    ds = ds.sortby("time")

    # site_name/latitude/longitude are constant across every hourly file
    # (assigned once from the same site list in extract_site_data) - but at
    # file counts in the thousands, open_mfdataset's "minimal" coords (which
    # lazily borrow them from just the first file) can silently end up
    # empty/NaN instead of raising an error (confirmed in practice on an
    # 8760-file concat). Read them fresh from a single file directly rather
    # than trusting open_mfdataset's handling of them.
    with xr.open_dataset(files[0]) as first:
        ds = ds.assign_coords(
            site_name=("site", first["site_name"].values),
            latitude=("site", first["latitude"].values),
            longitude=("site", first["longitude"].values),
        )
    # A plain if/raise, not assert - see concat_hourly() for why.
    if not (ds.site_name.values != "").all():
        raise ValueError("site_name still empty after re-assigning from first file")

    proc = config.get("processing", {})
    out_file = output_dir / f"hrrr_sites_{proc.get('start_date')}_{proc.get('end_date')}.nc"
    ds.to_netcdf(out_file)
    ds.close()
    print(f"Saved concatenated file -> {out_file}")

    if not config.get("options", {}).get("keep_hourly", True):
        for f in files:
            f.unlink()
        print("Removed hourly files (keep_hourly: false).")
