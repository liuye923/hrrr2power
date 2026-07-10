#!/usr/bin/env python3
"""
Download HRRR subh (subhourly) fxx=1 subset files - fully yaml-driven.

To run a download, create a run config that references the shared base
settings (config/default_download_subh.yaml) and adds a date range plus
(optionally) which variables to fetch, e.g. config/download_hrrr_subh_example.yaml:

    configure_file: config/default_download_subh.yaml
    start_date: "2025-12-01"
    end_date: "2025-12-31"
    variables: [t2m, d2m, u80, v80, psfc]   # omit to download every variable
                                             # listed in configure_file
    # init_hours/workers/retries/retry_wait/overwrite/save_dir may also be
    # set here to override configure_file's defaults for this run.

Usage (after `pip install -e .` from the scripts/ directory):
    download-subh --config config/download_hrrr_subh_example.yaml
"""

import argparse
from pathlib import Path

from hrrr2power.config import load_yaml, resolve_days
from hrrr2power.subh.config import resolve_download_settings
from hrrr2power.subh.download import FXX, download_range


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--config", required=True, help="Download run yaml (see module docstring)")
    args = parser.parse_args()

    run_cfg = load_yaml(args.config)
    days = resolve_days(run_cfg)
    settings = resolve_download_settings(run_cfg)
    settings["log_file"] = settings["log_file"] or (settings["save_dir"] / "herbie_download.log")

    print("HRRR subhourly (15-min) download")
    print(f"Config      : {args.config}")
    print(f"Days        : {days[0]:%Y-%m-%d} .. {days[-1]:%Y-%m-%d} ({len(days)} day(s))")
    print(f"Variables   : {[name for name, _ in settings['search_patterns']]}")
    print(f"Init hours  : {settings['init_hours']}")
    print(f"Forecast hrs: {FXX}  (fixed; subh fxx=1 contains all 4 x 15-min steps)")
    print(f"Workers     : {settings['workers']}")
    print(f"Retries     : {settings['retries']}")
    print(f"Retry wait  : {settings['retry_wait']}s")
    print(f"Overwrite   : {settings['overwrite']}  (skip already-downloaded files unless set)")
    print(f"Save dir    : {settings['save_dir']}")
    print(f"Herbie log  : {settings['log_file']}  (verbose Herbie output goes here, not the console)")

    expected, failed, skipped = download_range(days, settings)

    print("\n=== Summary ===")
    print(f"Days processed     : {len(days)}")
    print(f"Expected requests  : {expected}")
    print(f"Skipped (existing) : {skipped}")
    print(f"Failed requests    : {failed}")
    print("Done.")


if __name__ == "__main__":
    main()
