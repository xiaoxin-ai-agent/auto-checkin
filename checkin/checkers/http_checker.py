"""通用 HTTP API 签到器（配置驱动，支持多步骤链）"""
from __future__ import annotations

import json
import logging
import re
import time
from typing import Any, Optional

import requests

from ..models import CheckResult
from .base import BaseChecker

log = logging.getLogger("checkin.http")

_VAR_PATTERN = re.compile(r"\$\{(\w+)\}")


class HttpChecker(BaseChecker):
    """
    通过 YAML 配置的步骤链完成签到，无需为每个平台写代码。

    配置示例见 config.example.yaml，关键能力：
      - steps:       多步请求，后一步可用 {var} 引用前一步 extract 出的变量
      - extract:     用 $.a.b.c 简易路径从 JSON 响应提取变量/积分
      - success:     status / json_contains / keywords 判定成功
      - already:     命中"已签到"类关键词时视为幂等成功
    """

    def __init__(self, name: str, task_cfg: dict, global_cfg: dict | None = None):
        super().__init__(name, task_cfg, global_cfg)
        settings = task_cfg.get("settings", {})
        self.steps: list[dict] = settings.get("steps", [])
        self.timeout: int = settings.get("timeout", 20)
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": self.user_agent})

    # ------------------------------------------------------------------
    def checkin(self) -> CheckResult:
        started = time.time()
        ctx: dict[str, Any] = {}
        last_resp_text = ""
        result_meta: dict[str, Any] = {}

        try:
            for step in self.steps:
                last_resp_text, step_meta = self._exec_step(step, ctx)
                result_meta.update({k: v for k, v in step_meta.items() if v is not None})

            success = bool(result_meta.get("success"))
            already = bool(result_meta.get("already"))
            message = result_meta.get("message") or ("签到成功" if success else "未能确认签到结果")
            points = result_meta.get("points")

            return CheckResult(
                platform=self.platform,
                task_name=self.name,
                success=success,
                already_checked=already,
                message=str(message)[:200],
                points=points,
                extra={"steps": len(self.steps)},
                duration=round(time.time() - started, 2),
            )
        except Exception as e:
            log.exception("[%s/%s] 签到执行异常", self.platform, self.name)
            return CheckResult(
                platform=self.platform,
                task_name=self.name,
                success=False,
                message=f"执行异常: {e}",
                duration=round(time.time() - started, 2),
            )

    # ------------------------------------------------------------------
    def _exec_step(self, step: dict, ctx: dict) -> tuple[str, dict]:
        method = step.get("method", "GET").upper()
        url = self._render(step.get("url", ""), ctx)
        headers = {k: self._render(str(v), ctx) for k, v in (step.get("headers") or {}).items()}
        params = {k: self._render(str(v), ctx) for k, v in (step.get("params") or {}).items()}
        cookies = {k: self._render(str(v), ctx) for k, v in (step.get("cookies") or {}).items()}

        body = None
        json_body = step.get("json")
        if json_body is not None:
            body = json.dumps(self._render_deep(json_body, ctx)).encode("utf-8")
            headers.setdefault("Content-Type", "application/json")
        elif step.get("data") is not None:
            body = self._render_deep(step["data"], ctx)

        log.info("[%s/%s] 步骤 %s -> %s %s", self.platform, self.name,
                 step.get("name", "?"), method, url)
        resp = self.session.request(
            method, url, headers=headers, params=params, cookies=cookies,
            data=body, timeout=self.timeout,
        )
        text = resp.text
        log.info("[%s/%s] 步骤 %s 响应: %s %s",
                 self.platform, self.name, step.get("name", "?"),
                 resp.status_code, text[:200])

        meta = self._judge(step, resp, text)
        # extract 变量供后续步骤使用
        for var, path in (step.get("extract") or {}).items():
            value = self._extract(text, path)
            if value is not None:
                ctx[var] = value
                if var == "points":
                    meta["points"] = _to_number(value)
        # 显式积分字段优先
        if step.get("extract_points"):
            value = self._extract(text, step["extract_points"])
            if value is not None:
                meta["points"] = _to_number(value)
        if step.get("extract_message"):
            value = self._extract(text, step["extract_message"])
            if value is not None:
                meta["message"] = value
        return text, meta

    # ------------------------------------------------------------------
    def _judge(self, step: dict, resp: requests.Response, text: str) -> dict:
        success_cfg = step.get("success", {})
        already_cfg = step.get("already", {})
        meta: dict[str, Any] = {"success": False, "already": False, "message": None}

        # 1) 幂等：命中"已签到"关键词 -> 视为成功
        already_keywords = already_cfg.get("keywords", [])
        if any(k in text for k in already_keywords):
            meta.update(success=True, already=True,
                        message=already_cfg.get("message", "今日已签到"))
            return meta

        # 2) 状态码判定
        expect_status = success_cfg.get("status", resp.status_code)
        if resp.status_code != expect_status:
            meta["message"] = f"HTTP {resp.status_code}"
            return meta

        # 3) JSON 包含判定: {"code": 0} 表示响应 JSON 中 code == 0
        for key, want in (success_cfg.get("json_contains") or {}).items():
            got = self._extract(text, f"$.{key}")
            if got != want:
                meta["message"] = f"字段 {key}={got!r} 不符合预期 {want!r}"
                return meta

        # 4) 文本关键词判定
        keywords = success_cfg.get("keywords", [])
        if keywords and not all(k in text for k in keywords):
            meta["message"] = "响应未包含成功关键词"
            return meta

        meta["success"] = True
        return meta

    # ------------------------------------------------------------------
    @staticmethod
    def _render(template: str, ctx: dict) -> Any:
        """将 {var} 占位符替换为 ctx 中的变量"""
        if not isinstance(template, str):
            return template
        result = template
        for key, value in ctx.items():
            result = result.replace("{" + key + "}", str(value))
        return result

    @classmethod
    def _render_deep(cls, obj: Any, ctx: dict) -> Any:
        if isinstance(obj, str):
            return cls._render(obj, ctx)
        if isinstance(obj, dict):
            return {k: cls._render_deep(v, ctx) for k, v in obj.items()}
        if isinstance(obj, list):
            return [cls._render_deep(v, ctx) for v in obj]
        return obj

    @staticmethod
    def _extract(text: str, path: str) -> Any:
        """从 JSON 响应中按 $.a.b.c / a.b.c 路径提取值"""
        try:
            data = json.loads(text)
        except (json.JSONDecodeError, TypeError):
            return None
        if not path:
            return None
        node: Any = data
        for part in path.lstrip("$").strip(".").split("."):
            if not part:
                continue
            if isinstance(node, dict) and part in node:
                node = node[part]
            elif isinstance(node, list) and part.isdigit():
                node = node[int(part)]
            else:
                return None
        return node


def _to_number(value: Any) -> Optional[float]:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
