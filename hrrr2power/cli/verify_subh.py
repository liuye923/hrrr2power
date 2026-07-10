#!/usr/bin/env python3
"""
Audit already-downloaded HRRR subh grib2 files for integrity.

download-subh/extract-site's skip-check only checks that a file exists and
is large enough - it trusts that whatever passed the full GRIB validity
check right after being downloaded is still good, since re-opening every
file on every run to re-verify that is what made repeated runs slow. Run
this command to re-verify that trust on demand: it opens every file under
--grib-dir and checks both the leading GRIB magic bytes and the trailing
GRIB2 end-of-message marker (b"7777"), which catches downloads that were
interrupted mid-transfer (e.g. a dropped connection killing curl between
one of the 4 sub-hourly message-group appends) - a state the leading magic
bytes alone would not detect.

Usage:
    verify-subh --grib-dir /path/to/15min_grib2
    verify-subh --grib-dir /path/to/15min_grib2 --delete   # remove bad files so they re-download
"""

import argparse
from pathlib import Path

from tqdm import tqdm

from hrrr2power.subh.download import is_valid_grib_file


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--grib-dir", required=True, help="Root save_dir containing one folder per variable")
    parser.add_argument("--delete", action="store_true",
                         help="Delete invalid files so they get re-downloaded on the next download-subh/extract-site run")
    args = parser.parse_args()

    grib_dir = Path(args.grib_dir)
    files = sorted(grib_dir.glob("*/*/hrrr.t*z.wrfsubhf01.grib2"))
    print(f"Checking {len(files)} file(s) under {grib_dir}...")

    bad = [f for f in tqdm(files, desc="Verifying") if not is_valid_grib_file(f)]

    print(f"\n{len(bad)}/{len(files)} invalid file(s) found.")
    for f in bad:
        print(f"  {f}")

    if bad and args.delete:
        for f in bad:
            f.unlink()
        print(f"Deleted {len(bad)} invalid file(s).")
    elif bad:
        print("Re-run with --delete to remove them so they get re-downloaded.")


if __name__ == "__main__":
    main()
