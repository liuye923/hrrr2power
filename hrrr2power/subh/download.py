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

# Default forecast hour: subh only carries the 15-min sub-hourly steps at
# fxx=1 (fxx=0 is a single-timestep analysis despite the "subh" name).
# Overridable per-run via a run/site config's "fxx" key (see
# hrrr2power.config.resolve_download_settings) - subh genuinely only has
# meaningful sub-hourly data at fxx=1 though, so there's little reason to.
FXX = [1]

# Every variable's subh file packs exactly 4 sub-hourly messages
# (:15/:30/:45/:60), regardless of which single fxx is requested - see
# hrrr2power.download.is_valid_grib_file's docstring for what this catches.
EXPECTED_MESSAGE_COUNT = 4

FILENAME_TEMPLATE = "hrrr.t{hour:02d}z.wrfsubhf{fxx:02d}.grib2"


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
