"""数据模型定义"""
from __future__ import annotations

import json
from dataclasses import dataclass, field, asdict
from datetime import datetime
from typing import Any, Optional


@dataclass
class CheckResult:
    """一次签到的结果"""

    platform: str
    task_name: str
    success: bool
    message: str = ""
    points: Optional[float] = None
    already_checked: bool = False
    extra: dict = field(default_factory=dict)
    duration: float = 0.0
    created_at: str = field(default_factory=lambda: datetime.now().isoformat(timespec="seconds"))

    def to_dict(self) -> dict:
        return asdict(self)

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False)

    def summary(self) -> str:
        status = "成功" if self.success else "失败"
        if self.already_checked:
            status = "已签到(重复)"
        pts = f", 积分: {self.points}" if self.points is not None else ""
        return f"[{self.platform}/{self.task_name}] {status}{pts} - {self.message}"
