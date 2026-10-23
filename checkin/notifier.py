"""多渠道通知：Server酱 / Bark / 钉钉 / 飞书 / 企业微信 / 自定义 Webhook"""
from __future__ import annotations

import logging
from typing import Optional

import requests

from .models import CheckResult

log = logging.getLogger("checkin.notifier")


class Notifier:
    def __init__(self, cfg: dict):
        self.cfg = cfg or {}
        self.enabled = bool(self.cfg.get("enabled", False))

    # ------------------------------------------------------------------
    def send(self, results: list[CheckResult], title: Optional[str] = None) -> bool:
        if not self.enabled or not results:
            return False
        if self.cfg.get("only_on_failure", False) and all(r.success for r in results):
            return False

        title = title or self._build_title(results)
        content = self._build_content(results)
        ok = False
        for channel in ("serverchan", "bark", "dingtalk", "feishu", "wecom", "webhook"):
            handler = getattr(self, f"_send_{channel}", None)
            if handler and self.cfg.get(channel, {}).get("enabled"):
                try:
                    if handler(self.cfg[channel], title, content):
                        ok = True
                except Exception as e:  # 单个渠道失败不影响其他渠道
                    log.warning("通知渠道 %s 发送失败: %s", channel, e)
        return ok

    # ------------------------------------------------------------------
    @staticmethod
    def _build_title(results: list[CheckResult]) -> str:
        ok = sum(1 for r in results if r.success)
        return f"自动签到完成: {ok}/{len(results)} 成功"

    @staticmethod
    def _build_content(results: list[CheckResult]) -> str:
        lines = [f"自动签到报告 ({results[0].created_at})", "-" * 28]
        for r in results:
            mark = "OK" if r.success else ("DUP" if r.already_checked else "FAIL")
            pts = f" 积分+{r.points}" if r.points is not None else ""
            lines.append(f"[{mark}] {r.platform}/{r.task_name}{pts} {r.message}")
        return "\n".join(lines)

    # ------------------------------------------------------------------
    @staticmethod
    def _send_serverchan(c: dict, title: str, content: str) -> bool:
        key = c.get("sendkey", "")
        if not key:
            return False
        url = f"https://sctapi.ftqq.com/{key}.send"
        r = requests.post(url, data={"title": title, "desp": content}, timeout=15)
        return r.status_code == 200

    @staticmethod
    def _send_bark(c: dict, title: str, content: str) -> bool:
        base = c.get("url", "").rstrip("/")
        if not base:
            return False
        r = requests.post(
            f"{base}/{title}/{content}",
            data={"group": c.get("group", "checkin")},
            timeout=15,
        )
        return r.status_code == 200

    @staticmethod
    def _send_dingtalk(c: dict, title: str, content: str) -> bool:
        token = c.get("access_token", "")
        if not token:
            return False
        url = f"https://oapi.dingtalk.com/robot/send?access_token={token}"
        body = {
            "msgtype": "markdown",
            "markdown": {"title": title, "text": f"### {title}\n\n```\n{content}\n```"},
        }
        r = requests.post(url, json=body, timeout=15)
        return r.status_code == 200

    @staticmethod
    def _send_feishu(c: dict, title: str, content: str) -> bool:
        url = c.get("webhook", "")
        if not url:
            return False
        body = {
            "msg_type": "text",
            "content": {"text": f"{title}\n\n{content}"},
        }
        r = requests.post(url, json=body, timeout=15)
        return r.status_code == 200

    @staticmethod
    def _send_wecom(c: dict, title: str, content: str) -> bool:
        key = c.get("key", "")
        if not key:
            return False
        url = f"https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key={key}"
        body = {
            "msgtype": "text",
            "text": {"content": f"{title}\n\n{content}"},
        }
        r = requests.post(url, json=body, timeout=15)
        return r.status_code == 200

    @staticmethod
    def _send_webhook(c: dict, title: str, content: str) -> bool:
        url = c.get("url", "")
        if not url:
            return False
        r = requests.post(
            url,
            json={"title": title, "content": content},
            headers=c.get("headers", {}),
            timeout=15,
        )
        return r.status_code == 200
