"""HRRR-subh-specific config resolution: base download settings + variable selection."""

from pathlib import Path

from hrrr2power.config import load_yaml

DEFAULT_CONFIG_FILE = Path(__file__).resolve().parents[2] / "config" / "default_download_subh.yaml"


def resolve_download_settings(local_cfg: dict, variable_names: list[str] | None = None) -> dict:
    """Merge a local config's overrides on top of its configure_file base.

    local_cfg optionally has "configure_file" pointing to a base download
    yaml (default: config/default_download_subh.yaml), plus any of
    save_dir/init_hours/workers/retries/retry_wait/overwrite/variables to
    override. search_patterns always come from the base file, looked up by
    name - never redefined in local_cfg.

    variable_names, if given, overrides local_cfg's own "variables" key
    (used by the extract CLI, which derives the variable list from its
    extraction config rather than a "variables" list in the download block).
    """
    base_file = Path(local_cfg.get("configure_file", DEFAULT_CONFIG_FILE))
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
