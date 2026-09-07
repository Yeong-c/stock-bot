from __future__ import annotations

import logging
import sys
from logging.handlers import RotatingFileHandler

from .config import LOG_DIR


def setup_logging(level: int = logging.INFO) -> logging.Logger:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s", "%Y-%m-%d %H:%M:%S")
    root = logging.getLogger()
    root.setLevel(level)
    if root.handlers:
        return logging.getLogger("stockbot")
    fh = RotatingFileHandler(LOG_DIR / "bot.log", maxBytes=5_000_000, backupCount=5, encoding="utf-8")
    fh.setFormatter(fmt)
    root.addHandler(fh)
    sh = logging.StreamHandler(sys.stdout)
    sh.setFormatter(fmt)
    root.addHandler(sh)
    for noisy in ("httpx", "httpcore", "telegram", "apscheduler", "urllib3"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    return logging.getLogger("stockbot")
