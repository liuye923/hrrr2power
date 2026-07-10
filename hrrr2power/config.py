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


def resolve_download_settings(
    local_cfg: dict, variable_names: list[str] | None = None, default_config_file: Path | None = None
) -> dict:
    """Merge a local config's overrides on top of its configure_file base.

    local_cfg optionally has "configure_file" pointing to a base download
    yaml (default: default_config_file, a per-product base config passed by
    the caller - e.g. hrrr2power.subh.config's own default_download_subh.yaml),
    plus any of save_dir/init_hours/workers/retries/retry_wait/overwrite/
    variables to override. search_patterns always come from the base file,
    looked up by name - never redefined in local_cfg.

    variable_names, if given, overrides local_cfg's own "variables" key
    (used by the extract CLI, which derives the variable list from its
    extraction config rather than a "variables" list in the download block).
    """
    base_file = Path(local_cfg.get("configure_file", default_config_file))
    base = load_yaml(base_file)
    all_patterns = base["search_patterns"]

    if variable_names is None:
        variable_names = local_cfg.get("variables", list(all_patterns.keys()))

    missing = [name for name in variable_names if name not in all_patterns]
    if missing:
        raise SystemExit(
            f"No search_patterns entry for variable(s) {missing} in {base_file} - add them there."
        )

    log_file = local_cfg.get("log_file", base.get("log_file"))

    return {
        "save_dir": Path(local_cfg.get("save_dir", base["save_dir"])),
        "init_hours": local_cfg.get("init_hours", base["init_hours"]),
        "workers": local_cfg.get("workers", base["workers"]),
        "retries": local_cfg.get("retries", base["retries"]),
        "retry_wait": local_cfg.get("retry_wait", base["retry_wait"]),
        "overwrite": local_cfg.get("overwrite", base["overwrite"]),
        # None here means "derive from save_dir" - left unresolved because
        # save_dir may still be overridden by the caller (e.g. extract-site
        # forces it to match grib_dir) after this function returns.
        "log_file": Path(log_file) if log_file else None,
        "search_patterns": [(name, all_patterns[name]) for name in variable_names],
    }
