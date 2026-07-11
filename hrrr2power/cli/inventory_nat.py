#!/usr/bin/env python3
"""
Dump the HRRR nat fxx=0 (analysis) grib inventory as a variable dictionary,
for reference when writing search_patterns entries in a download config (see
config/default_download_nat.yaml).

Writes:
  - hrrr_nat_f00_inventory.txt : every grib message
  - hrrr_nat_f00_variables.txt : deduplicated (variable, level) pairs with a
    ready-to-use ":VARIABLE:LEVEL:" search pattern

Usage (after `pip install -e .` from the scripts/ directory):
    inventory-nat
    inventory-nat --date 2025-06-01 --init-hour 0
"""

import argparse
from pathlib import Path

from hrrr2power.nat.inventory import fetch_inventory, unique_variables

# Default output location: scripts/ (three levels up from this file:
# cli/ -> hrrr2power/ -> scripts/), matching where the reference files have
# always lived.
DEFAULT_OUT_DIR = Path(__file__).resolve().parents[2]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--date", default="2025-06-01", help="Sample UTC date (YYYY-MM-DD)")
    parser.add_argument("--init-hour", type=int, default=0, help="Sample init hour (UTC)")
    parser.add_argument("--fxx", type=int, default=0, help="Forecast hour (this package only uses nat fxx=0)")
    parser.add_argument("--out-dir", type=Path, default=DEFAULT_OUT_DIR, help="Where to write the .txt files")
    args = parser.parse_args()

    df = fetch_inventory(args.date, args.init_hour, args.fxx)

    full_file = args.out_dir / "hrrr_nat_f00_inventory.txt"
    cols = ["grib_message", "variable", "level", "forecast_time", "search_this"]
    df[cols].to_string(full_file.open("w"), index=False)
    print(f"Full inventory ({len(df)} messages) -> {full_file}")

    unique = unique_variables(df)
    var_file = args.out_dir / "hrrr_nat_f00_variables.txt"
    unique.to_string(var_file.open("w"), index=False)
    print(f"Unique variables ({len(unique)}) -> {var_file}")


if __name__ == "__main__":
    main()
