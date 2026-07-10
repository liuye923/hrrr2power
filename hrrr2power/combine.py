"""Merge cfgrib message groups that cfgrib splits within one downloaded file.

HRRR subh f01 packs 4 sub-hourly steps (:15/:30/:45/:60) per file. cfgrib
splits these into two disjoint groups per variable (a 3-step group for
:15/:30/:45, and a 1-step group for :60, since :60 is encoded as step=1 hour
instead of minutes) - combine_named_arrays() stitches them back into one
continuous time axis. This quirk is specific to HRRR's subh product; other
products (e.g. prs, one message per variable per file) never hit the
multi-array branch below and just pass straight through the len(arrays)==1
case unchanged - the function is product-agnostic in mechanism, so every
product shares it rather than each carrying its own copy.
"""

import numpy as np
import pandas as pd
import xarray as xr


def recover_valid_time(da, reference_time):
    """Reconstruct valid_time for a message cfgrib/eccodes couldn't decode."""
    length = da.attrs.get("GRIB_lengthOfTimeRange")
    forecast_time = da.attrs.get("GRIB_forecastTime")
    unit = da.attrs.get("GRIB_indicatorOfUnitForTimeRange")
    if length is None or forecast_time is None or unit is None:
        return None
    if unit == 0:  # minute
        delta = pd.Timedelta(minutes=int(forecast_time) + int(length))
    elif unit == 1:  # hour
        delta = pd.Timedelta(hours=int(forecast_time) + int(length))
    else:
        return None
    return np.datetime64(pd.Timestamp(reference_time) + delta, "ns")


def combine_named_arrays(var_arrays: dict, reference_time) -> dict:
    """Merge cfgrib message groups that share a final variable name.

    var_arrays: dict of name -> list[DataArray], collected across every
    opened file/group for one (date, hour). Names with a single contributing
    array pass through untouched; names split across groups (the :15/:30/:45
    vs :60 case) get concatenated along a `valid_time` dimension.
    """
    combined = {}
    for name, arrays in var_arrays.items():
        if len(arrays) == 1:
            da = arrays[0]
            if "step" in da.dims:
                da = da.swap_dims({"step": "valid_time"})
            da = da.drop_vars("step", errors="ignore")
            combined[name] = da
            continue

        normalized = []
        for da in arrays:
            if "valid_time" not in da.coords:
                vtime = recover_valid_time(da, reference_time)
                if vtime is None:
                    continue
                da = da.assign_coords(valid_time=vtime)
            if "step" in da.dims:
                da = da.swap_dims({"step": "valid_time"})
            else:
                da = da.expand_dims(valid_time=[da["valid_time"].values])
            normalized.append(da.drop_vars("step", errors="ignore"))

        if not normalized:
            continue
        if len(normalized) == 1:
            combined[name] = normalized[0]
            continue

        da = xr.concat(normalized, dim="valid_time", coords="minimal", compat="override")
        da = da.sortby("valid_time")
        if da.indexes["valid_time"].has_duplicates:
            da = da.drop_duplicates("valid_time", keep="first")
        combined[name] = da

    return combined
