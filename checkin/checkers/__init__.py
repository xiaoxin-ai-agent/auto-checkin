"""签到器注册与工厂"""
from __future__ import annotations

from .base import BaseChecker
from .http_checker import HttpChecker
from .browser_checker import BrowserChecker
from .cdp_checker import CdpChecker
from .desktop_checker import DesktopChecker

CHECKER_TYPES = {
    # 网页/接口模式：直接调用 HTTP 接口（接口可抓到时首选）
    "http": HttpChecker,
    # 无头浏览器模式：网页端有风控/复杂登录流程
    "browser": BrowserChecker,
    # 客户端模式：CDP 接入本机桌面客户端（Trae/CodeArts 等 Electron IDE）
    "cdp": CdpChecker,
    # 桌面自动化模式：非 Electron 客户端，直接操作窗口控件
    "desktop": DesktopChecker,
}


def create_checker(name: str, task_cfg: dict, global_cfg: dict | None = None) -> BaseChecker:
    checker_type = task_cfg.get("checker", "http")
    cls = CHECKER_TYPES.get(checker_type)
    if cls is None:
        raise ValueError(f"未知的签到器类型: {checker_type} (支持: {list(CHECKER_TYPES)})")
    return cls(name, task_cfg, global_cfg)


__all__ = [
    "BaseChecker", "HttpChecker", "BrowserChecker",
    "CdpChecker", "DesktopChecker", "create_checker", "CHECKER_TYPES",
]
