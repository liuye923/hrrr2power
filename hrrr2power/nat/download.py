"""HRRR nat product constants, thin bindings over the product-agnostic
download machinery in hrrr2power.download.

Restricted to fxx=0 (the analysis field) - each variable's file has exactly
one message, unlike subh's 4 packed sub-hourly steps, so there's no
message-group stitching needed (hrrr2power.combine.combine_named_arrays
already no-ops correctly on a single-message input). Confirmed live
against hrrr.20250601: all 7 variables this package already downloads for
subh/prs (t2m, d2m, u10, v10, u80, v80, psfc) exist in nat too, with the
exact same search_patterns strings and message shape as prs.
"""

from pathlib import Path

from hrrr2power.download import (
    download_range as _generic_download_range,
    expected_output_path as _generic_expected_output_path,
    file_already_downloaded,  # noqa: F401 - re-exported, product-agnostic
    is_href_error,  # noqa: F401 - re-exported, product-agnostic
    is_valid_grib_file as _generic_is_valid_grib_file,
    relocate_subset_file,  # noqa: F401 - re-exported, product-agnostic
)

PRODUCT = "nat"

# Default forecast hour: F00, the analysis field. Overridable per-run via a
# run/site config's "fxx" key (see hrrr2power.config.resolve_download_settings)
# - nat also serves real forecast lead times (e.g. fxx: 6, or fxx: [0, 6, 12]
# for several at once), unlike subh which only makes sense at fxx=1.
FXX = [0]

# One message per variable per hour regardless of which single fxx is
# requested (an analysis or forecast field, not subh's packed sub-hourly
# steps) - see hrrr2power.download.is_valid_grib_file's docstring for what
# requiring the exact count catches.
EXPECTED_MESSAGE_COUNT = 1

FILENAME_TEMPLATE = "hrrr.t{hour:02d}z.wrfnatf{fxx:02d}.grib2"


def is_valid_grib_file(path: Path) -> bool:
    return _generic_is_valid_grib_file(path, expected_message_count=EXPECTED_MESSAGE_COUNT)


def expected_output_path(init_time, search_name: str, save_dir: Path, fxx: int = FXX[0]) -> Path:
    return _generic_expected_output_path(init_time, search_name, save_dir, FILENAME_TEMPLATE, fxx)


def download_range(days, settings):
    return _generic_download_range(
        days, settings,
        product=PRODUCT,
        fxx=settings.get("fxx") or FXX,
        expected_message_count=EXPECTED_MESSAGE_COUNT,
        filename_template=FILENAME_TEMPLATE,
    )
