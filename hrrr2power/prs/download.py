"""HRRR prs product constants, thin bindings over the product-agnostic
download machinery in hrrr2power.download.

Restricted to fxx=0 (the analysis field) - each variable's file has exactly
one message, unlike subh's 4 packed sub-hourly steps, so there's no
message-group stitching needed (hrrr2power.combine.combine_named_arrays
already no-ops correctly on a single-message input).
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

PRODUCT = "prs"

# Analysis field only.
FXX = [0]

# One message per variable per hour (an analysis field, not subh's packed
# sub-hourly steps) - see hrrr2power.download.is_valid_grib_file's docstring
# for what requiring the exact count catches.
EXPECTED_MESSAGE_COUNT = 1

FILENAME_TEMPLATE = "hrrr.t{hour:02d}z.wrfprsf00.grib2"


def is_valid_grib_file(path: Path) -> bool:
    return _generic_is_valid_grib_file(path, expected_message_count=EXPECTED_MESSAGE_COUNT)


def expected_output_path(init_time, search_name: str, save_dir: Path) -> Path:
    return _generic_expected_output_path(init_time, search_name, save_dir, FILENAME_TEMPLATE)


def download_range(days, settings):
    return _generic_download_range(
        days, settings,
        product=PRODUCT,
        fxx=FXX,
        expected_message_count=EXPECTED_MESSAGE_COUNT,
        filename_template=FILENAME_TEMPLATE,
    )
