"""统一日志配置

在包级 logger("checkin") 上挂 handler，所有子 logger（checkin.engine、
checkin.cdp 等）通过 propagate 自动输出到控制台与日志文件。
"""
from __future__ import annotations

import logging
import sys
from pathlib import Path

_LOG_DIR = Path("logs")
_CONFIGURED = False


def _configure() -> None:
    """在 checkin 包级 logger 上注册 handler（仅一次）"""
    global _CONFIGURED
    if _CONFIGURED:
        return

    _LOG_DIR.mkdir(exist_ok=True)

    pkg = logging.getLogger("checkin")
    if not pkg.handlers:
        pkg.setLevel(logging.INFO)
        fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s")

        # pythonw 静默模式下没有控制台，仅写文件日志
        if sys.stdout is not None:
            console = logging.StreamHandler(sys.stdout)
            console.setFormatter(fmt)
            pkg.addHandler(console)

        file_handler = logging.FileHandler(_LOG_DIR / "checkin.log", encoding="utf-8")
        file_handler.setFormatter(fmt)
        pkg.addHandler(file_handler)

    _CONFIGURED = True


def get_logger(name: str = "checkin") -> logging.Logger:
    _configure()
    return logging.getLogger(name)
