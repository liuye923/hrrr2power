"""Generic yaml loading and date-range helpers, shared across hrrr2power products."""

from pathlib import Path

import pandas as pd
import yaml


def load_yaml(path: Path) -> dict:
    with open(path) as fh:
        return yaml.safe_load(fh)


def resolve_days(cfg: dict) -> list[pd.Timestamp]:
    """Resolve a date range from a config's start_date/end_date or days.

    cfg must have either both start_date and end_date (inclusive range), or
    days (an explicit list of dates, not a range).
    """
    if cfg.get("start_date") and cfg.get("end_date"):
        days = list(pd.date_range(cfg["start_date"], cfg["end_date"], freq="D"))
    elif cfg.get("days"):
        days = [pd.Timestamp(d) for d in cfg["days"]]
    else:
        raise SystemExit("Config must specify either start_date+end_date or days")
    return sorted(set(d.normalize() for d in days))
