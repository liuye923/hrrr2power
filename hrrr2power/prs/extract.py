"""HRRR prs product constants, thin bindings over the product-agnostic
extraction machinery in hrrr2power.extract.
"""

from pathlib import Path

from hrrr2power.extract import (
    CFGRIB_KWARGS,  # noqa: F401 - re-exported, shared default
    collect_variable_names,  # noqa: F401 - re-exported, product-agnostic
    concat_hourly,  # noqa: F401 - re-exported, product-agnostic
    concat_hourly_dask,  # noqa: F401 - re-exported, product-agnostic
    discover_groups as _generic_discover_groups,
    ensure_downloaded as _generic_ensure_downloaded,
    extract_site_data,  # noqa: F401 - re-exported, product-agnostic
    process_one,  # noqa: F401 - re-exported, product-agnostic
)

from .config import resolve_download_settings
from .download import download_range

# Every hourly prs (F00 analysis) file is named hrrr.t{HH}z.wrfprsf00.grib2.
FILENAME_GLOB = "hrrr.t*z.wrfprsf00.grib2"


def discover_groups(grib_dir: Path, days):
    return _generic_discover_groups(grib_dir, days, FILENAME_GLOB)


def ensure_downloaded(config: dict, workers: int | None = None) -> None:
    return _generic_ensure_downloaded(config, resolve_download_settings, download_range, workers=workers)
