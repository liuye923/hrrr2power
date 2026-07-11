#!/usr/bin/env python3
"""
Extract HRRR site (point) timeseries from the per-variable subset files
produced by download-subh/download-prs/download-nat.

Input layout (one folder per downloaded variable):
    <grib_dir>/<name>/YYYYMMDD/<hourly file>
    where <name> is an output_name from this config's "variables" block
    (e.g. u80/v80/t2m/d2m/psfc) - discovered by globbing, not hardcoded.

For a given (date, hour), the sibling files across all variable folders are
opened together, filtered against the config's "variables" block (keyed by
GRIB typeOfLevel[_stepType]), reduced to the requested site locations via
nearest-grid-point indices, and written to one netCDF per hour. Optionally
concatenated into a single file for the whole period.

This one command works for any supported product - set the config's
top-level "product" key ("subh", "prs", or "nat"; defaults to "subh" if
omitted, so existing configs need no changes).

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

from hrrr2power.config import load_yaml, resolve_days, resolve_download_cfg
from hrrr2power.grid import generate_grid_indices


def _product_module(product: str):
    if product == "subh":
        import hrrr2power.subh.extract as m
    elif product == "prs":
        import hrrr2power.prs.extract as m
    elif product == "nat":
        import hrrr2power.nat.extract as m
    else:
        raise SystemExit(f"Unknown product {product!r} - expected 'subh', 'prs', or 'nat'")
    return m


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
    product = _product_module(config.get("product", "subh"))
    CFGRIB_KWARGS = product.CFGRIB_KWARGS
    concat_hourly = product.concat_hourly
    discover_groups = product.discover_groups
    ensure_downloaded = product.ensure_downloaded
    process_one = product.process_one

    # Resolve fxx once regardless of --skip-download, so discover_groups
    # knows which lead time's filenames to look for. Set as a top-level
    # "fxx" key (like "product") rather than nested under "download:", since
    # it picks a product variant/lead-time rather than a download mechanic -
    # resolve_download_cfg folds it into the download settings so the actual
    # download step (ensure_downloaded, below) agrees with this. Downloading
    # can fetch several fxx values in one run (e.g. fxx: [0, 6, 12]), but
    # extraction targets exactly one per run - grouping sibling files by
    # (date, hour) alone would be ambiguous if more than one lead time's
    # file could match the same slot.
    dl_cfg = resolve_download_cfg(config)
    variable_names = product.collect_variable_names(config)
    resolved_fxx = product.resolve_download_settings(dl_cfg, variable_names=variable_names)["fxx"]
    fxx = resolved_fxx[0]
    if len(resolved_fxx) > 1:
        print(f"Note: fxx has {len(resolved_fxx)} values {resolved_fxx} - "
              f"extracting only fxx={fxx} (the first).")

    if not args.skip_download:
        ensure_downloaded(config, workers=args.workers)

    site_locations_file = Path(config["site_locations_file"])
    sites_raw = pd.read_csv(site_locations_file)
    sites_raw["site_name"] = sites_raw["site_name"].str.strip()
    print(f"Sites: {len(sites_raw)}")

    grib_dir = Path(config["grib_dir"])
    num_cells = config.get("num_cells", 1)
    if num_cells not in (1, 4):
        raise SystemExit(f"num_cells must be 1 or 4, got {num_cells!r}")
    indices_file = site_locations_file.parent / (
        "site_grid_indices_4cells.csv" if num_cells == 4 else "site_grid_indices.csv"
    )

    if indices_file.exists() and not config.get("auto_find_indices", False):
        sites = pd.read_csv(indices_file)
        print(f"Loaded grid indices <- {indices_file}")
    else:
        sample = next(grib_dir.glob(f"*/*/{product.FILENAME_GLOB_TEMPLATE.format(fxx=fxx)}"), None)
        if sample is None:
            raise SystemExit(f"No grib files found under {grib_dir} to build grid indices")
        print(f"Generating grid indices from sample: {sample}")
        sites = generate_grid_indices(sites_raw, sample, backend_kwargs=CFGRIB_KWARGS, num_cells=num_cells)
        indices_file.parent.mkdir(parents=True, exist_ok=True)
        sites.to_csv(indices_file, index=False)
        print(f"Saved grid indices -> {indices_file}")

    days = resolve_days(config["processing"])
    tasks = list(discover_groups(grib_dir, days, fxx=fxx))
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
        concat_hourly(hourly_dir, output_dir, config, workers=extract_workers)


if __name__ == "__main__":
    main()
