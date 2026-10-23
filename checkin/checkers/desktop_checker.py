"""桌面 UI 自动化签到器（Windows）：直接控制客户端窗口控件/图像点击

适用于无法通过 CDP 接入的客户端（非 Electron，如 JetBrains 系插件宿主、
Qt/原生客户端），或签到按钮不在 webview 中而是原生控件的场景。

动作类型（settings.steps）：
  - {type: click_text,  value: "签到"}      # 按控件名称/文本点击（uiautomation）
  - {type: click_name,  value: "signin"}    # 按控件 AutomationId/Name 点击
  - {type: image,       value: "imgs/btn.png"}  # 图像匹配点击（pyautogui，兜底）
  - {type: wait,        value: 2}           # 等待秒数
  - {type: hotkey,      value: "ctrl+s"}    # 快捷键
  - {type: dump,        value: true}        # 打印控件树，用于定位控件名
"""
from __future__ import annotations

import logging
import subprocess
import time
from typing import Optional

from ..models import CheckResult
from .base import BaseChecker

log = logging.getLogger("checkin.desktop")


class DesktopChecker(BaseChecker):
    """Windows 桌面 GUI 自动化签到"""

    def __init__(self, name: str, task_cfg: dict, global_cfg: dict | None = None):
        super().__init__(name, task_cfg, global_cfg)
        s = task_cfg.get("settings", {})
        self.window_title: Optional[str] = s.get("window_title")
        self.steps: list[dict] = s.get("steps", [])
        self.launch_cmd: Optional[str] = s.get("launch_cmd")
        self.wait_after_launch = s.get("wait_after_launch", 15)
        self.window_timeout = s.get("window_timeout", 20)
        self.kill_process: Optional[str] = s.get("kill_process")

    # ------------------------------------------------------------------
    def checkin(self) -> CheckResult:
        started = time.time()
        try:
            import uiautomation as auto
        except ImportError:
            return CheckResult(
                platform=self.platform, task_name=self.name, success=False,
                message="未安装 uiautomation，请执行: pip install uiautomation",
                duration=round(time.time() - started, 2),
            )

        try:
            window = self._get_window(auto)
            window.SetActive()
            window.SetFocus()
            time.sleep(0.5)

            for step in self.steps:
                self._run_step(auto, window, step)

            return CheckResult(
                platform=self.platform, task_name=self.name, success=True,
                message="桌面自动化流程执行完成",
                duration=round(time.time() - started, 2),
            )
        except Exception as e:
            log.exception("[%s/%s] 桌面自动化异常", self.platform, self.name)
            return CheckResult(
                platform=self.platform, task_name=self.name, success=False,
                message=f"桌面自动化异常: {e}",
                duration=round(time.time() - started, 2),
            )
        finally:
            if self.kill_process:
                subprocess.run(
                    f'taskkill /IM "{self.kill_process}" /F',
                    shell=True, capture_output=True,
                )

    # ------------------------------------------------------------------
    def _get_window(self, auto):
        window = auto.WindowControl(
            searchDepth=1, RegexName=self.window_title or ".*"
        )
        if not window.Exists(self.window_timeout):
            if not self.launch_cmd:
                raise RuntimeError(
                    f"未找到窗口 '{self.window_title}'，且未配置 launch_cmd"
                )
            log.info("启动客户端: %s", self.launch_cmd)
            subprocess.Popen(self.launch_cmd, shell=True)
            time.sleep(self.wait_after_launch)
            window = auto.WindowControl(searchDepth=1, RegexName=self.window_title or ".*")
            if not window.Exists(self.window_timeout):
                raise RuntimeError(f"启动后仍未找到窗口 '{self.window_title}'")
        return window

    def _run_step(self, auto, window, step: dict) -> None:
        stype = step.get("type")
        value = step.get("value")

        if stype in ("click_text", "click_name"):
            control = window.Control(RegexName=value) if stype == "click_text" \
                else window.Control(Name=value)
            # 逐级向下查找可点击控件
            target = control
            for _ in range(5):
                if not target.Exists(2):
                    break
                if target.GetClickablePoint():
                    break
                target = target.GetFirstChildControl()
            if not target.Exists(2):
                raise RuntimeError(f"未找到控件: {value}")
            target.Click(simulateMove=False)
            log.info("已点击控件: %s", value)

        elif stype == "image":
            import pyautogui
            pos = pyautogui.locateOnScreen(str(value), confidence=0.85)
            if not pos:
                raise RuntimeError(f"屏幕上未匹配到图像: {value}")
            pyautogui.click(pyautogui.center(pos))
            log.info("已点击图像位置: %s", pos)

        elif stype == "wait":
            time.sleep(float(value))

        elif stype == "hotkey":
            import pyautogui
            pyautogui.hotkey(*str(value).split("+"))

        elif stype == "dump":
            names = []

            def _walk(ctrl, depth=0):
                if depth > 3:
                    return
                for child in ctrl.GetChildren():
                    names.append(f"{'  ' * depth}{child.ControlTypeName}: {child.Name!r}")
                    _walk(child, depth + 1)

            _walk(window)
            log.info("控件树:\n%s", "\n".join(names[:80]))

        else:
            raise ValueError(f"未知的桌面动作类型: {stype}")
