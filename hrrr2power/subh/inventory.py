"""Dump the HRRR subh (subhourly) fxx=1 grib inventory as a variable dictionary.

Follows the inventory approach from the Herbie docs:
https://herbie.readthedocs.io/en/stable/user_guide/tutorial/inventory.html

    H = Herbie(date, model="hrrr", product="subh", fxx=1)
    H.inventory()   # -> DataFrame with one row per GRIB message

HRRR subh f01 packs 4 sub-hourly steps (15/30/45/60 min) back-to-back, so the
same ~49 variables repeat 4 times (196 messages total).
"""

import pandas as pd
from herbie import Herbie


def fetch_inventory(date: str, init_hour: int = 0, fxx: int = 1) -> pd.DataFrame:
    """Fetch the full grib message inventory for one sample HRRR subh f01 file."""
    init_time = pd.Timestamp(date) + pd.Timedelta(hours=init_hour)
    H = Herbie(init_time, model="hrrr", product="subh", fxx=fxx)
    return H.inventory()


def unique_variables(inventory: pd.DataFrame) -> pd.DataFrame:
    """Deduplicate (variable, level) pairs and add a ready-to-use search pattern.

    The pattern column is ":VARIABLE:LEVEL:" - the same style used in
    default_download_subh.yaml's search_patterns, and matches all 4
    sub-hourly time blocks since it doesn't include forecast_time.
    """
    unique = inventory.drop_duplicates(subset=["variable", "level"], keep="first").copy()
    unique["pattern"] = ":" + unique["variable"] + ":" + unique["level"] + ":"
    return unique[["variable", "level", "pattern"]]
