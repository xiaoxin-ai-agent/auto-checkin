"""浏览器签到器（Playwright，适合复杂登录流程/风控场景）"""
from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import Any, Optional

from ..models import CheckResult
from .base import BaseChecker

log = logging.getLogger("checkin.browser")


class BrowserChecker(BaseChecker):
    """
    使用 Playwright 无头浏览器执行签到。

    配置示例:
      settings:
        storage_state: "state/trae.json"   # 登录态快照，首次需用 main.py login 生成
        headless: true
        actions:
          - goto: "https://www.trae.com.cn/points"
          - click: "button:has-text('签到')"
          - wait: 1500
          - eval: "document.title"
          - screenshot: "logs/trae.png"
    """

    def __init__(self, name: str, task_cfg: dict, global_cfg: dict | None = None):
        super().__init__(name, task_cfg, global_cfg)
        settings = task_cfg.get("settings", {})
        self.actions: list[dict] = settings.get("actions", [])
        self.headless: bool = settings.get("headless", True)
        self.state_path: Optional[Path] = (
            Path(settings["storage_state"]) if settings.get("storage_state") else None
        )

    # ------------------------------------------------------------------
    def checkin(self) -> CheckResult:
        started = time.time()
        try:
            from playwright.sync_api import sync_playwright
        except ImportError:
            return CheckResult(
                platform=self.platform, task_name=self.name, success=False,
                message="未安装 playwright，请执行: pip install playwright && playwright install chromium",
                duration=round(time.time() - started, 2),
            )

        last_text = ""
        try:
            with sync_playwright() as p:
                browser = p.chromium.launch(headless=self.headless)
                context_kwargs: dict[str, Any] = {}
                if self.state_path and self.state_path.exists():
                    context_kwargs["storage_state"] = str(self.state_path)
                context = browser.new_context(
                    user_agent=self.user_agent, **context_kwargs
                )
                page = context.new_page()
                for action in self.actions:
                    last_text = self._run_action(page, action)
                # 保存最新登录态，供下次复用
                if self.state_path:
                    self.state_path.parent.mkdir(parents=True, exist_ok=True)
                    context.storage_state(path=str(self.state_path))
                browser.close()
            return CheckResult(
                platform=self.platform, task_name=self.name, success=True,
                message=f"浏览器流程执行完成: {last_text}"[:200],
                duration=round(time.time() - started, 2),
            )
        except Exception as e:
            log.exception("[%s/%s] 浏览器签到异常", self.platform, self.name)
            return CheckResult(
                platform=self.platform, task_name=self.name, success=False,
                message=f"浏览器执行异常: {e}",
                duration=round(time.time() - started, 2),
            )

    # ------------------------------------------------------------------
    def _run_action(self, page, action: dict) -> str:
        if "goto" in action:
            page.goto(action["goto"], wait_until="domcontentloaded")
        elif "click" in action:
            page.click(action["click"], timeout=10000)
        elif "fill" in action:
            page.fill(action["fill"][0], action["fill"][1])
        elif "wait" in action:
            page.wait_for_timeout(int(action["wait"]))
        elif "wait_for_selector" in action:
            page.wait_for_selector(action["wait_for_selector"], timeout=15000)
        elif "eval" in action:
            value = page.evaluate(action["eval"])
            return str(value)
        elif "screenshot" in action:
            out = Path(action["screenshot"])
            out.parent.mkdir(parents=True, exist_ok=True)
            page.screenshot(path=str(out), full_page=True)
        else:
            raise ValueError(f"未知的浏览器动作: {action}")
        return ""

    # ------------------------------------------------------------------
    @classmethod
    def interactive_login(cls, name: str, task_cfg: dict, global_cfg: dict | None = None) -> Path:
        """打开有头浏览器供用户手动登录，登录后保存 storage_state 快照。"""
        settings = task_cfg.get("settings", {})
        url = settings.get("login_url") or settings.get("url")
        state_path = Path(settings.get("storage_state", f"state/{name}.json"))

        from playwright.sync_api import sync_playwright

        with sync_playwright() as p:
            browser = p.chromium.launch(headless=False)
            context = browser.new_context()
            page = context.new_page()
            if url:
                page.goto(url, wait_until="domcontentloaded")
            print(f"\n请在打开的浏览器中完成 [{name}] 的登录，然后回到此处按回车保存登录态...")
            input()
            state_path.parent.mkdir(parents=True, exist_ok=True)
            context.storage_state(path=str(state_path))
            browser.close()
        print(f"登录态已保存: {state_path}")
        return state_path
