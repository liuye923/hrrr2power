"""HRRR subh product constants, thin binding over the product-agnostic
inventory machinery in hrrr2power.inventory.

HRRR subh f01 packs 4 sub-hourly steps (15/30/45/60 min) back-to-back, so the
same ~49 variables repeat 4 times (196 messages total).
"""

import pandas as pd

from hrrr2power.inventory import fetch_inventory as _generic_fetch_inventory
from hrrr2power.inventory import unique_variables  # noqa: F401 - re-exported, product-agnostic

PRODUCT = "subh"


def fetch_inventory(date: str, init_hour: int = 0, fxx: int = 1) -> pd.DataFrame:
    return _generic_fetch_inventory(date, PRODUCT, init_hour=init_hour, fxx=fxx)
