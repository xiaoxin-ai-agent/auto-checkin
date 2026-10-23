"""配置加载与 ${ENV_VAR} 环境变量注入"""
from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any

import yaml

_ENV_PATTERN = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")


def _inject_env(value: Any) -> Any:
    """递归替换字符串中的 ${ENV_VAR} 为环境变量值，未命中则保留原样。"""
    if isinstance(value, str):
        def _repl(m: "re.Match[str]") -> str:
            return os.environ.get(m.group(1), m.group(0))
        return _ENV_PATTERN.sub(_repl, value)
    if isinstance(value, dict):
        return {k: _inject_env(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_inject_env(v) for v in value]
    return value


class Config:
    def __init__(self, data: dict, path: Path):
        self.data = _inject_env(data)
        self.path = path

    @classmethod
    def load(cls, path: str | Path) -> "Config":
        path = Path(path)
        if not path.exists():
            raise FileNotFoundError(f"配置文件不存在: {path}")
        with open(path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
        return cls(data, path)

    # ---------- 访问器 ----------
    @property
    def global_cfg(self) -> dict:
        return self.data.get("global", {})

    @property
    def tasks(self) -> list[dict]:
        return self.data.get("tasks", [])

    @property
    def notify_cfg(self) -> dict:
        return self.data.get("notify", {})

    @property
    def schedule_cfg(self) -> dict:
        return self.data.get("schedule", {})

    def enabled_tasks(self, only: list[str] | None = None) -> list[dict]:
        tasks = [t for t in self.tasks if t.get("enabled", True)]
        if only:
            wanted = set(only)
            tasks = [t for t in tasks if t.get("name") in wanted or t.get("platform") in wanted]
        return tasks
