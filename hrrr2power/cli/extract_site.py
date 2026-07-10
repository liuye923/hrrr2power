#!/usr/bin/env python3
"""
Extract HRRR site (point) timeseries from the per-variable subset files
produced by download-subh.

Input layout (one folder per downloaded variable):
    <grib_dir>/<name>/YYYYMMDD/hrrr.tHHz.wrfsubhf01.grib2
    where <name> is an output_name from this config's "variables" block
    (e.g. u80/v80/t2m/d2m/psfc) - discovered by globbing, not hardcoded.

For a given (date, hour), the sibling files across all variable folders are
opened together, filtered against the config's "variables" block (keyed by
GRIB typeOfLevel[_stepType]), reduced to the requested site locations via
nearest-grid-point indices, and written to one netCDF per hour. Optionally
concatenated into a single file for the whole period.

Before extracting, ensure_downloaded() checks whether the grib files needed
for the config's variables/date range exist, and downloads whatever is
missing (skipped automatically if already present). Use --skip-download to
bypass this and only extract from what's already on disk.

--workers controls both steps: it overrides the config's download.workers
for the download step, and parallelizes the extraction step across
processes (default: config's own download.workers for downloading, serial
for extraction).

Usage (after `pip install -e .` from the scripts/ directory):
    extract-site --config config/site_Southern_Co_2024_2025.15min.yaml
    extract-site --config config/site_Southern_Co_2024_2025.15min.yaml --workers 16
    extract-site --config config/site_Southern_Co_2024_2025.15min.yaml --skip-download
"""

import argparse
from functools import partial
from multiprocessing import Pool
from pathlib import Path

import pandas as pd
from tqdm import tqdm

from hrrr2power.config import load_yaml, resolve_days
from hrrr2power.grid import generate_grid_indices
from hrrr2power.subh.extract import CFGRIB_KWARGS, concat_hourly, discover_groups, ensure_downloaded, process_one


def main() -> None:
    parser = argparse.ArgumentParser(description="Extract HRRR site timeseries from subset files.")
    parser.add_argument("--config", dest="config_file", required=True, help="Path to a site-extraction YAML config")
    parser.add_argument("--workers", type=int, default=None,
                         help="Parallel workers for both the download step (overrides the config's "
                              "download.workers) and the extraction step (default: config's own "
                              "download.workers for downloading, serial for extraction)")
    parser.add_argument("--skip-download", action="store_true",
                         help="Don't check/download missing grib files first; extract from what's already on disk")
    args = parser.parse_args()

    config = load_yaml(args.config_file)

    if not args.skip_download:
        ensure_downloaded(config, workers=args.workers)

    site_locations_file = Path(config["site_locations_file"])
    sites_raw = pd.read_csv(site_locations_file)
    sites_raw["site_name"] = sites_raw["site_name"].str.strip()
    print(f"Sites: {len(sites_raw)}")

    grib_dir = Path(config["grib_dir"])
    num_cells = config.get("num_cells", 1)
    indices_file = site_locations_file.parent / (
        "site_grid_indices_4cells.csv" if num_cells == 4 else "site_grid_indices.csv"
    )

    if indices_file.exists() and not config.get("auto_find_indices", False):
        sites = pd.read_csv(indices_file)
        print(f"Loaded grid indices <- {indices_file}")
    else:
        sample = next(grib_dir.glob("*/*/hrrr.t*z.wrfsubhf01.grib2"), None)
        if sample is None:
            raise SystemExit(f"No grib files found under {grib_dir} to build grid indices")
        print(f"Generating grid indices from sample: {sample}")
        sites = generate_grid_indices(sites_raw, sample, backend_kwargs=CFGRIB_KWARGS)
        indices_file.parent.mkdir(parents=True, exist_ok=True)
        sites.to_csv(indices_file, index=False)
        print(f"Saved grid indices -> {indices_file}")

    days = resolve_days(config["processing"])
    tasks = list(discover_groups(grib_dir, days))
    print(f"Found {len(tasks)} (date, hour) groups to process")

    if not tasks:
        print("Nothing to do.")
        return

    output_dir = Path(config["output_dir"])
    hourly_dir = output_dir / "hourly"
    hourly_dir.mkdir(parents=True, exist_ok=True)

    skip_existing = config.get("options", {}).get("skip_existing", True)
    worker = partial(process_one, sites=sites, config=config, hourly_dir=hourly_dir, skip_existing=skip_existing)

    extract_workers = args.workers if args.workers is not None else 1
    if extract_workers > 1:
        with Pool(extract_workers) as pool:
            results = list(tqdm(pool.imap(worker, tasks), total=len(tasks), desc="Extracting"))
    else:
        results = [worker(t) for t in tqdm(tasks, desc="Extracting")]

    success = sum(1 for r in results if r is not None)
    print(f"\nComplete: {success}/{len(tasks)} successful")
    print(f"Output: {output_dir}")

    if config.get("options", {}).get("concatenate", False) and success > 0:
        concat_hourly(hourly_dir, output_dir, config)


if __name__ == "__main__":
    main()
