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


def resolve_download_cfg(config: dict) -> dict:
    """Build the local_cfg dict for resolve_download_settings out of a
    site-extraction config, folding in a top-level "fxx" override.

    fxx picks a product variant/lead-time (like the top-level "product" key),
    not a download mechanic - so extract-site configs set it at the top
    level rather than nested under "download:", which stays reserved for
    download-mechanics settings (workers/retries/retry_wait/overwrite/
    configure_file). A top-level fxx wins over a legacy download.fxx if both
    are somehow present; either way it must land in the returned dict so
    both the download step (hrrr2power.extract.ensure_downloaded) and any
    caller resolving fxx for its own purposes (e.g. extract-site picking
    which fxx's filenames to glob for) agree on the same value.
    """
    dl_cfg = dict(config.get("download", {}))
    fxx = config.get("fxx", dl_cfg.get("fxx"))
    if fxx is not None:
        dl_cfg["fxx"] = fxx
    return dl_cfg


def _normalize_pattern(entry: str | dict) -> tuple[str, int | None]:
    """A search_patterns entry is either a plain search string (the common
    case - one grib message, uses the product's own default message count)
    or a dict {pattern: ..., message_count: N} for a variable whose search
    string matches more than one message per file (e.g. a wildcard level
    regex like r":UGRD:\\d+ mb:" matching all 40 HRRR pressure levels at
    once, decoded by cfgrib as a single variable with an extra level
    dimension rather than 40 separate ones) - message_count overrides the
    product-wide default for just that variable, since is_valid_grib_file's
    integrity check needs to know exactly how many messages *this*
    variable's file should contain. Get the right number via
    hrrr2power.download.count_matching_messages rather than guessing.
    """
    if isinstance(entry, str):
        return entry, None
    return entry["pattern"], entry.get("message_count")


def resolve_download_settings(
    local_cfg: dict,
    variable_names: list[str] | None = None,
    default_config_file: Path | None = None,
    default_fxx: list[int] | None = None,
    default_local_archive_filename_template: str | None = None,
) -> dict:
    """Merge a local config's overrides on top of its configure_file base.

    local_cfg optionally has "configure_file" pointing to a base download
    yaml (default: default_config_file, a per-product base config passed by
    the caller - e.g. hrrr2power.subh.config's own default_download_subh.yaml),
    plus any of save_dir/init_hours/workers/retries/retry_wait/overwrite/
    fxx/variables/local_archive_dir/local_archive_filename_template to
    override. search_patterns always come from the base file, looked up by
    name - never redefined in local_cfg.

    variable_names, if given, overrides local_cfg's own "variables" key
    (used by the extract CLI, which derives the variable list from its
    extraction config rather than a "variables" list in the download block).

    fxx resolves from local_cfg, then the base file, then default_fxx (the
    calling product's own default, e.g. hrrr2power.subh.download.FXX) - a
    bare int in yaml (`fxx: 6`) is normalized to a 1-item list, matching the
    list[int] FastHerbie itself expects.

    local_archive_dir (default: unset - local-archive reads are opt-in)
    points at a directory of already-downloaded full (not byte-range
    subset) grib2 files, laid out <local_archive_dir>/<date>/<filename>.
    When set, the download step tries extracting each variable directly
    from the matching local file (via an idx-derived byte range - see
    hrrr2power.download.extract_from_local_archive) before falling back to
    a normal network download - see extract_from_local_archive for why this
    is strictly opt-in, best-effort, and safe to leave unset.
    local_archive_filename_template resolves like fxx (local_cfg, then
    base, then default_local_archive_filename_template, the calling
    product's own FILENAME_TEMPLATE) since a local archive typically
    follows the same naming convention as what this package would have
    downloaded anyway.
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
    fxx = local_cfg.get("fxx", base.get("fxx", default_fxx))
    if isinstance(fxx, int):
        fxx = [fxx]
    local_archive_dir = local_cfg.get("local_archive_dir", base.get("local_archive_dir"))
    local_archive_filename_template = local_cfg.get(
        "local_archive_filename_template",
        base.get("local_archive_filename_template", default_local_archive_filename_template),
    )

    return {
        "save_dir": Path(local_cfg.get("save_dir", base["save_dir"])),
        "init_hours": local_cfg.get("init_hours", base["init_hours"]),
        "fxx": fxx,
        "workers": local_cfg.get("workers", base["workers"]),
        "retries": local_cfg.get("retries", base["retries"]),
        "retry_wait": local_cfg.get("retry_wait", base["retry_wait"]),
        "overwrite": local_cfg.get("overwrite", base["overwrite"]),
        # None here means "derive from save_dir" - left unresolved because
        # save_dir may still be overridden by the caller (e.g. extract-site
        # forces it to match grib_dir) after this function returns.
        "log_file": Path(log_file) if log_file else None,
        "search_patterns": [
            (name, *_normalize_pattern(all_patterns[name])) for name in variable_names
        ],
        "local_archive_dir": Path(local_archive_dir) if local_archive_dir else None,
        "local_archive_filename_template": local_archive_filename_template,
    }
