"""HRRR prs product constants, thin binding over the product-agnostic
inventory machinery in hrrr2power.inventory.
"""

import pandas as pd

from hrrr2power.inventory import fetch_inventory as _generic_fetch_inventory
from hrrr2power.inventory import unique_variables  # noqa: F401 - re-exported, product-agnostic

PRODUCT = "prs"


def fetch_inventory(date: str, init_hour: int = 0, fxx: int = 0) -> pd.DataFrame:
    return _generic_fetch_inventory(date, PRODUCT, init_hour=init_hour, fxx=fxx)
