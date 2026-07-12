"""HRRR nat product constants, thin bindings over the product-agnostic
extraction machinery in hrrr2power.extract.
"""

from pathlib import Path

from hrrr2power.extract import (
    CFGRIB_KWARGS,  # noqa: F401 - re-exported, shared default
    collect_variable_names,  # noqa: F401 - re-exported, product-agnostic
    concat_hourly,  # noqa: F401 - re-exported, product-agnostic
    concat_hourly_dask,  # noqa: F401 - re-exported, product-agnostic
    concat_hourly_region,  # noqa: F401 - re-exported, product-agnostic
    discover_groups as _generic_discover_groups,
    ensure_downloaded as _generic_ensure_downloaded,
    extract_region_data,  # noqa: F401 - re-exported, product-agnostic
    extract_site_data,  # noqa: F401 - re-exported, product-agnostic
    process_one,  # noqa: F401 - re-exported, product-agnostic
    process_one_region,  # noqa: F401 - re-exported, product-agnostic
)

from .config import resolve_download_settings
from .download import FXX, download_range

# Every hourly nat file is named hrrr.t{HH}z.wrfnatf{fxx:02d}.grib2 - fxx
# defaults to nat's own default (F00) but can be overridden to match
# whichever lead time was actually downloaded (see resolve_download_settings's
# "fxx" key) - extraction targets one fxx per run even though downloading
# can fetch several at once, to avoid ambiguity in which file represents a
# given (variable, hour).
FILENAME_GLOB_TEMPLATE = "hrrr.t*z.wrfnatf{fxx:02d}.grib2"


def discover_groups(grib_dir: Path, days, fxx: int = FXX[0]):
    return _generic_discover_groups(grib_dir, days, FILENAME_GLOB_TEMPLATE.format(fxx=fxx))


def ensure_downloaded(config: dict, workers: int | None = None) -> None:
    return _generic_ensure_downloaded(config, resolve_download_settings, download_range, workers=workers)
