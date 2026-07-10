"""HRRR prs product config: thin binding over the generic
resolve_download_settings, pinned to prs's own base config file.
"""

from pathlib import Path

from hrrr2power.config import resolve_download_settings as _generic_resolve_download_settings

DEFAULT_CONFIG_FILE = Path(__file__).resolve().parents[2] / "config" / "default_download_prs.yaml"


def resolve_download_settings(local_cfg: dict, variable_names: list[str] | None = None) -> dict:
    return _generic_resolve_download_settings(
        local_cfg, variable_names, default_config_file=DEFAULT_CONFIG_FILE
    )
