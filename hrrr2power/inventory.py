"""Dump a HRRR product's grib inventory as a variable dictionary, shared by
every product (hrrr2power.subh, hrrr2power.prs, ...).

Follows the inventory approach from the Herbie docs:
https://herbie.readthedocs.io/en/stable/user_guide/tutorial/inventory.html

    H = Herbie(date, model="hrrr", product=product, fxx=fxx)
    H.inventory()   # -> DataFrame with one row per GRIB message
"""

import pandas as pd
from herbie import Herbie


def fetch_inventory(date: str, product: str, init_hour: int = 0, fxx: int = 1) -> pd.DataFrame:
    """Fetch the full grib message inventory for one sample HRRR file."""
    init_time = pd.Timestamp(date) + pd.Timedelta(hours=init_hour)
    H = Herbie(init_time, model="hrrr", product=product, fxx=fxx)
    return H.inventory()


def unique_variables(inventory: pd.DataFrame) -> pd.DataFrame:
    """Deduplicate (variable, level) pairs and add a ready-to-use search pattern.

    The pattern column is ":VARIABLE:LEVEL:" - the same style used in
    default_download_subh.yaml's search_patterns, and matches all sub-hourly
    time blocks (for products that have them) since it doesn't include
    forecast_time.
    """
    unique = inventory.drop_duplicates(subset=["variable", "level"], keep="first").copy()
    unique["pattern"] = ":" + unique["variable"] + ":" + unique["level"] + ":"
    return unique[["variable", "level", "pattern"]]
