# hrrr2power

Download and extract weather-model data for power-site timeseries. The
first supported product is **HRRR subh** (subhourly, 15-minute output);
the package is structured so other products can be added as siblings of
`hrrr2power.subh` without touching the generic code.

## Features

- Byte-range subset downloads via [Herbie](https://herbie.readthedocs.io/)
  (only the requested variables, not the full ~300 MB file per hour)
- Downloads are flattened to one task per (variable, hour) and run through
  a `multiprocessing.Pool(workers)`, with automatic per-task retry on
  transient errors - `workers` is real, literal download concurrency, not
  just threads within one variable at a time. Pooling only by variable
  degenerates to serial when there are few variables, or stalls on
  whichever variable has the most missing hours while other workers sit
  idle; flattening across both dimensions keeps every worker busy
  regardless of how missing data happens to be distributed (benchmarked
  ~3x faster than the old sequential-variables approach)
- Skip-if-already-downloaded by default (idempotent re-runs); the skip-check
  is a cheap stat-only existence/size check, trusting the full GRIB
  integrity check that already ran right after each file was downloaded -
  run `verify-subh` to re-audit that trust on demand (e.g. after a
  connection drop mid-download)
- Nearest-grid-point site extraction (haversine, no full-grid download needed)
- Correctly stitches HRRR subh's disjoint `:15/:30/:45` vs `:60` minute
  message groups into one continuous time axis
- Config-driven: yaml files control what to download/extract; the same
  logic is also directly importable as a Python API
- Four CLI commands after install: `download-subh`, `extract-site`, `inventory-subh`, `verify-subh`

## Installation

```bash
module load conda
conda activate compute      # or whichever env has herbie-data/cfgrib/xarray/pandas/pyyaml/tqdm
cd download_fastheribie/scripts
pip install -e . --no-deps  # --no-deps: this env already has all the dependencies
```

`--no-deps` is only there to avoid pip trying to reinstall packages (like
`cfgrib`) that are already present and version-pinned in the conda env. Drop
it for a fresh environment that doesn't have the dependencies yet.

Re-run `pip install -e .` whenever `pyproject.toml`'s `[project.scripts]`
changes (new console command added/renamed) - editable installs pick up
code changes to existing modules automatically, but not new entry points.

## Package layout

```
scripts/
  pyproject.toml
  hrrr2power/
    __init__.py
    config.py         # generic: load_yaml, resolve_days (start/end date or explicit list)
    grid.py            # generic: nearest-grid-point lookup, works with any cfgrib file
    subh/               # HRRR subh (subhourly, fxx=1) product
      config.py          # resolve_download_settings: merges a config's overrides
                          # on top of its configure_file base
      download.py          # FastHerbie download/retry/relocate + download_range()
      combine.py             # stitches cfgrib's disjoint :60-step groups back together
      extract.py               # site pixel extraction, discover_groups, ensure_downloaded
      inventory.py              # fetch_inventory/unique_variables (Herbie .inventory() dump)
    cli/
      download_subh.py     # entry point for `download-subh`
      extract_site.py        # entry point for `extract-site`
      inventory_subh.py       # entry point for `inventory-subh`
      verify_subh.py           # entry point for `verify-subh`
  config/
    default_download_subh.yaml       # base download settings + variable dictionary
    download_hrrr_subh_example.yaml    # example standalone bulk-download config
    site_Southern_Co_2024_2025.15min.yaml  # example site-extraction config
```

## Quick start

### 1. Discover available variables

```bash
inventory-subh --date 2025-06-01 --init-hour 0
```

Writes `hrrr_subh_f01_variables.txt` — a deduplicated `(variable, level,
pattern)` table you can copy `pattern` values from straight into
`search_patterns` in `default_download_subh.yaml`.

### 2. Configure the base download settings once

`config/default_download_subh.yaml` is the single source of truth for
*how* to download each variable (its Herbie search pattern) and the default
`init_hours`/`workers`/`retries`/`retry_wait`/`overwrite`/`save_dir`:

```yaml
save_dir: /path/to/15min_grib2
init_hours: [0, 1, 2, ..., 23]
workers: 4
retries: 3
retry_wait: 5.0
overwrite: false

search_patterns:
  t2m:  ":TMP:2 m above ground:"
  d2m:  ":DPT:2 m above ground:"
  u80:  ":UGRD:80 m above ground:"
  v80:  ":VGRD:80 m above ground:"
  psfc: ":PRES:surface:"
```

Every downloaded variable gets its own folder:
`<save_dir>/<name>/YYYYMMDD/hrrr.tHHz.wrfsubhf01.grib2`.

### 3a. Standalone bulk download

Create a run config that references the base file and adds a date range
(and optionally narrows down which variables):

```yaml
# config/download_hrrr_subh_2025Q1.yaml
configure_file: config/default_download_subh.yaml
start_date: "2025-01-01"
end_date: "2025-03-31"
variables: [t2m, d2m, u80, v80, psfc]   # omit to download everything in configure_file
# init_hours/workers/retries/retry_wait/overwrite/save_dir may override the base file here
```

```bash
download-subh --config config/download_hrrr_subh_2025Q1.yaml
```

### 3b. Site (point) extraction, with auto-download

A site-extraction config additionally needs `site_locations_file`,
`grib_dir`, `output_dir`, a `processing` date range, and a `variables`
block describing what to pull out and how to rename it (keyed by GRIB
`typeOfLevel[_stepType]`):

```yaml
# config/site_Southern_Co_2024_2025.15min.yaml
site_locations_file: /path/to/site_locations.csv   # columns: site_name, latitude, longitude
grib_dir: /path/to/15min_grib2
output_dir: /path/to/output

processing:
  start_date: "20250101"
  end_date: "20250105"

download:
  configure_file: config/default_download_subh.yaml
  # any of workers/retries/retry_wait/overwrite/init_hours/save_dir here
  # override configure_file's defaults for this extraction run

variables:
  heightAboveGround:
    enabled: true
    vars: {t2m: t2m, d2m: d2m, u: u80, v: v80}
  surface:
    enabled: true
    vars: {sp: psfc}

options:
  skip_existing: true
  concatenate: true
  keep_hourly: true
```

```bash
extract-site --config config/site_Southern_Co_2024_2025.15min.yaml
extract-site --config config/site_Southern_Co_2024_2025.15min.yaml --workers 24
extract-site --config config/site_Southern_Co_2024_2025.15min.yaml --skip-download
```

By default, `extract-site` checks whether the grib files it needs exist and
downloads whatever is missing before extracting (`--skip-download` to
disable). Output: one netCDF per (date, hour) under `output_dir/hourly/`,
optionally concatenated into a single `hrrr_sites_<start>_<end>.nc`.
Concatenation trusts the filenames' chronological order (`combine="nested"`)
instead of re-inferring it (`combine="by_coords"`), which is the difference
between ~1 minute and several+ minutes once there are hundreds of hourly
files.

Each hourly netCDF is verified readable right after being written (an
interrupted or failed `to_netcdf()` can otherwise leave a corrupted-but
nonzero-size file that `skip_existing` would trust forever - confirmed in
practice on an 8760-file run) - a failed write is deleted and retried on
the next run rather than silently kept. `site_name`/`latitude`/`longitude`
are also re-read fresh from a single hourly file after concatenation,
rather than trusted from `open_mfdataset`'s own coordinate handling - at
file counts in the thousands that can silently end up empty/NaN instead of
raising an error (also confirmed in practice, on the same run).

### 4. Verify integrity of already-downloaded files

Trust is established once, at write time, not on every read: right after a
file downloads, `download-subh`/`extract-site` fully parses every GRIB
message in it via eccodes (catching a message truncated or corrupted
*anywhere* in the file, not just a transfer cut off at the very end - each
file is 4 sub-hourly messages appended by separate `curl` calls, so a
middle one can be broken while the last one completes fine). Everything
downstream - the skip-check on later runs, `extract-site` itself - just
trusts that already happened and never re-checks.

`verify-subh` exists for data that predates this check, or for occasional
spot-checks (e.g. after a suspected filesystem issue) - not for routine
use, since it's real per-file work (~60ms/file: parsing, not just an
`open()`), parallelized across `--workers` (default 8):

```bash
verify-subh --grib-dir /path/to/15min_grib2
verify-subh --grib-dir /path/to/15min_grib2 --workers 32
verify-subh --grib-dir /path/to/15min_grib2 --delete   # remove bad files so download-subh/extract-site re-fetch them
```

This is slower than the normal skip-check (it opens every file), so it's
meant to be run occasionally on demand - e.g. after a connection drop
during a download - not as part of every run.

## Python API

Every CLI command is a thin wrapper around plain functions that take dicts,
not yaml files — call them directly for notebook/pipeline use:

```python
import pandas as pd
from hrrr2power.subh.config import resolve_download_settings
from hrrr2power.subh.download import download_range

settings = resolve_download_settings(
    {"configure_file": "config/default_download_subh.yaml", "init_hours": [0]},
    variable_names=["t2m", "psfc"],
)
expected, failed, skipped = download_range([pd.Timestamp("2025-01-06")], settings)
```

```python
from hrrr2power.grid import generate_grid_indices
from hrrr2power.subh.extract import CFGRIB_KWARGS, discover_groups, extract_site_data

sites_raw = pd.DataFrame({
    "site_name": ["TestSite"],
    "latitude": [33.825],
    "longitude": [-101.099722],
})
sites = generate_grid_indices(sites_raw, sample_grib_file, backend_kwargs=CFGRIB_KWARGS)

config = {
    "variables": {
        "heightAboveGround": {"enabled": True, "vars": {"t2m": "t2m"}},
        "surface": {"enabled": True, "vars": {"sp": "psfc"}},
    }
}
date_str, hour_str, files = next(discover_groups(grib_dir, [pd.Timestamp("2025-01-06")]))
ds = extract_site_data(files, sites, config)  # xarray.Dataset, in memory - no netCDF required
```

## Notes on HRRR subh

- Only `fxx=1` carries the 15-minute sub-hourly steps (`+0:15/+0:30/+0:45/+1:00`);
  `fxx=0` is a single-timestep analysis despite the "subh" name.
- Each downloaded variable file already contains all 4 sub-hourly steps -
  Herbie's byte-range search matches the variable's messages across all 4
  time blocks in one call, so there's one file per (date, hour, variable),
  not per (date, hour, variable, sub-hourly-step).
- cfgrib splits those 4 steps into two disjoint groups per file (a 3-step
  group for `:15/:30/:45`, and a 1-step group for `:60`, because `:60` is
  encoded with `step` in hours rather than minutes). `hrrr2power.subh.combine`
  reconstructs the missing `valid_time` for the undecodable messages and
  concatenates both groups into one time axis.
