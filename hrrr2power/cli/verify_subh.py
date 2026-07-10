#!/usr/bin/env python3
"""
Audit already-downloaded HRRR grib2 files for integrity (any product -
subh or prs - since --grib-dir already points at just one product's data).

download-subh/download-prs/extract-site's skip-check only checks that a
file exists and is large enough - it trusts that whatever passed the full
GRIB validity check right after being downloaded is still good, since
re-doing that check on every run for every file is what made repeated runs
slow. Run this command to re-verify that trust on demand: it actually
parses every GRIB message in every file under --grib-dir via eccodes, which
catches a message truncated or corrupted anywhere in the file - not just a
transfer cut off at the very end (subh's files are 4 sub-hourly messages
appended by separate curl calls, so a message truncated in the *middle*
while a later append still completed fine would pass a header/footer-only
check but fail here) - and requires the exact expected message count to be
present, catching a whole message silently missing (e.g. one of several
curl calls failing outright) that a per-message check alone can't see.

This is meant for auditing data that predates this check (e.g. downloaded
before hrrr2power started validating this thoroughly at download time) or
for occasional spot-checks (e.g. after a suspected filesystem issue) - not
for routine use, since it's real per-file work (~60ms/file) rather than a
cheap stat.

Usage:
    verify-subh --grib-dir /path/to/15min_grib2
    verify-subh --grib-dir /path/to/hourly_grib2 --expected-message-count 1   # prs data
    verify-subh --grib-dir /path/to/15min_grib2 --workers 32
    verify-subh --grib-dir /path/to/15min_grib2 --delete   # remove bad files so they re-download
"""

import argparse
from functools import partial
from multiprocessing import Pool
from pathlib import Path

from tqdm import tqdm

from hrrr2power.download import is_valid_grib_file


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--grib-dir", required=True, help="Root save_dir containing one folder per variable")
    parser.add_argument("--workers", type=int, default=8, help="Parallel workers (default: 8)")
    parser.add_argument("--expected-message-count", type=int, default=4,
                         help="Messages expected per file (default: 4, subh's value; prs's F00 analysis "
                              "files have 1)")
    parser.add_argument("--delete", action="store_true",
                         help="Delete invalid files so they get re-downloaded on the next download-subh/extract-site run")
    args = parser.parse_args()

    grib_dir = Path(args.grib_dir)
    files = sorted(grib_dir.glob("*/*/hrrr.t*z.*.grib2"))
    print(f"Checking {len(files)} file(s) under {grib_dir} with {args.workers} worker(s), "
          f"expecting {args.expected_message_count} message(s)/file...")

    check = partial(is_valid_grib_file, expected_message_count=args.expected_message_count)
    bad = []
    with Pool(max(1, min(args.workers, len(files) or 1))) as pool:
        for f, ok in tqdm(
            zip(files, pool.imap(check, files, chunksize=20)),
            total=len(files),
            desc="Verifying",
        ):
            if not ok:
                bad.append(f)

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
