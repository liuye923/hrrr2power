"""Keep noisy third-party output (Herbie's own verbose download messages -
colored banners, curl commands, per-message dumps) out of the console and
in a log file instead, without touching hrrr2power's own concise progress
prints.
"""

import contextlib
from pathlib import Path


@contextlib.contextmanager
def redirect_stdout_to_file(log_file: Path):
    log_file.parent.mkdir(parents=True, exist_ok=True)
    with open(log_file, "a") as fh:
        with contextlib.redirect_stdout(fh):
            yield
