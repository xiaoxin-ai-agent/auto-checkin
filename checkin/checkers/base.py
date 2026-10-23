"""签到器抽象基类"""
from __future__ import annotations

from abc import ABC, abstractmethod

from ..models import CheckResult


class BaseChecker(ABC):
    """所有平台签到器均实现 checkin() 返回 CheckResult"""

    def __init__(self, name: str, task_cfg: dict, global_cfg: dict | None = None):
        self.name = name
        self.task_cfg = task_cfg
        self.global_cfg = global_cfg or {}
        self.platform = task_cfg.get("platform", name)
        self.user_agent = self.global_cfg.get(
            "user_agent",
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
        )

    @abstractmethod
    def checkin(self) -> CheckResult:
        """执行签到并返回结果"""
        raise NotImplementedError

    def __repr__(self) -> str:  # pragma: no cover
        return f"<{self.__class__.__name__} {self.platform}/{self.name}>"
