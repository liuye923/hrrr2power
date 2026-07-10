"""HRRR subh (subhourly, fxx=1) product constants, thin bindings over the
product-agnostic download machinery in hrrr2power.download.

HRRR subh f01 packs all 4 sub-hourly steps (:15/:30/:45/:60) into a single
set of messages per variable, so one download call retrieves the whole
day-hour's worth of that variable's sub-hourly steps.
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

PRODUCT = "subh"

# HRRR subh only has fxx=1 for the 15-min sub-hourly steps.
# All four 15-min valid times (+15, +30, +45, +60 min) are packed in fxx=1.
FXX = [1]

# Every variable's subh f01 file packs exactly 4 sub-hourly messages
# (:15/:30/:45/:60) - see hrrr2power.download.is_valid_grib_file's docstring
# for what this catches.
EXPECTED_MESSAGE_COUNT = 4

FILENAME_TEMPLATE = "hrrr.t{hour:02d}z.wrfsubhf01.grib2"


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
