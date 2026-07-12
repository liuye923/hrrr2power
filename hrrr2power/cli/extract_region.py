#!/usr/bin/env python3
"""
Extract a lat/lon bounding-box region from the per-variable subset files
produced by download-subh/download-prs/download-nat - crops to the
smallest rectangular index range covering the box, keeping the native 2D
grid structure (y, x dims), unlike extract-site which reduces to points.

Input layout: same as extract-site (one folder per downloaded variable).

Config needs a "region" block instead of extract-site's
site_locations_file/num_cells:

    region:
      min_lat: 30.0
      max_lat: 36.0
      min_lon: -102.0
      max_lon: -95.0

This one command works for any supported product - set the config's
top-level "product" key ("subh", "prs", or "nat"; defaults to "subh" if
omitted).

Before extracting, ensure_downloaded() checks whether the grib files needed
for the config's variables/date range exist, and downloads whatever is
missing (skipped automatically if already present). Use --skip-download to
bypass this and only extract from what's already on disk.

--workers controls both steps: it overrides the config's download.workers
for the download step, and parallelizes the extraction step across
processes (default: config's own download.workers for downloading, serial
for extraction).

Usage (after `pip install -e .` from the scripts/ directory):
    extract-region --config config/region_example.yaml
    extract-region --config config/region_example.yaml --workers 16
    extract-region --config config/region_example.yaml --skip-download
"""

import argparse
import warnings
from functools import partial
from multiprocessing import Pool
from pathlib import Path

import cfgrib
from tqdm import tqdm

from hrrr2power.config import load_yaml, resolve_days, resolve_download_cfg
from hrrr2power.grid import find_region_indices


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
    parser = argparse.ArgumentParser(description="Extract a lat/lon bounding-box region from subset files.")
    parser.add_argument("--config", dest="config_file", required=True, help="Path to a region-extraction YAML config")
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
    concat_hourly_region = product.concat_hourly_region
    discover_groups = product.discover_groups
    ensure_downloaded = product.ensure_downloaded
    process_one_region = product.process_one_region

    # Resolve fxx once regardless of --skip-download - see extract_site.py's
    # identical block for why (fxx is top-level, folded in via
    # resolve_download_cfg, extraction targets exactly one fxx per run).
    dl_cfg = resolve_download_cfg(config)
    variable_names = product.collect_variable_names(config)
    resolved_fxx = product.resolve_download_settings(dl_cfg, variable_names=variable_names)["fxx"]
    fxx = resolved_fxx[0]
    if len(resolved_fxx) > 1:
        print(f"Note: fxx has {len(resolved_fxx)} values {resolved_fxx} - "
              f"extracting only fxx={fxx} (the first).")

    if not args.skip_download:
        ensure_downloaded(config, workers=args.workers)

    grib_dir = Path(config["grib_dir"])
    region_cfg = config["region"]
    sample = next(grib_dir.glob(f"*/*/{product.FILENAME_GLOB_TEMPLATE.format(fxx=fxx)}"), None)
    if sample is None:
        raise SystemExit(f"No grib files found under {grib_dir} to resolve the region's grid indices")
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        sample_ds = cfgrib.open_datasets(str(sample), backend_kwargs=CFGRIB_KWARGS)[0]
    y_slice, x_slice = find_region_indices(
        region_cfg["min_lat"], region_cfg["max_lat"],
        region_cfg["min_lon"], region_cfg["max_lon"],
        sample_ds["latitude"].values, sample_ds["longitude"].values,
    )
    print(f"Region: lat=[{region_cfg['min_lat']},{region_cfg['max_lat']}] "
          f"lon=[{region_cfg['min_lon']},{region_cfg['max_lon']}] "
          f"-> y={y_slice.start}:{y_slice.stop}, x={x_slice.start}:{x_slice.stop} "
          f"({y_slice.stop - y_slice.start} x {x_slice.stop - x_slice.start} grid cells)")

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
    worker = partial(
        process_one_region, region=(y_slice, x_slice), config=config,
        hourly_dir=hourly_dir, skip_existing=skip_existing,
    )

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
        concat_hourly_region(hourly_dir, output_dir, config, workers=extract_workers)


if __name__ == "__main__":
    main()
