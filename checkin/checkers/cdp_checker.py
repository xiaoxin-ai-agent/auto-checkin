"""客户端签到器：通过 CDP(Chrome DevTools Protocol) 连接本机已运行的桌面客户端
（Electron / VSCode 系 IDE，如 Trae、CodeArts），在客户端真实上下文中执行签到。

优势：
  - 复用客户端已登录的会话，无需处理登录/ Cookie 失效问题
  - 在页面上下文执行 fetch，自动携带 token / 绕过接口签名
  - 也可直接点击客户端内的签到按钮（webview / 页面）

使用前提：客户端需以调试端口启动，例如：
    Trae.exe --remote-debugging-port=9222
    codearts.exe --remote-debugging-port=9333
（可修改快捷方式目标，或在配置中用 launch_cmd 让工具自动拉起）
"""
from __future__ import annotations

import logging
import subprocess
import time
from pathlib import Path
from typing import Any, Optional

import requests

from ..models import CheckResult
from .base import BaseChecker

log = logging.getLogger("checkin.cdp")


class CdpChecker(BaseChecker):
    """连接本机客户端的 CDP 调试端口并执行签到动作"""

    def __init__(self, name: str, task_cfg: dict, global_cfg: dict | None = None):
        super().__init__(name, task_cfg, global_cfg)
        s = task_cfg.get("settings", {})
        self.host = s.get("debug_host", "127.0.0.1")
        self.port = s.get("debug_port", 9222)
        self.launch_cmd: Optional[str] = s.get("launch_cmd")
        self.ready_timeout = s.get("ready_timeout", 60)
        self.wait_after_launch = s.get("wait_after_launch", 8)
        self.target_match: Optional[str] = s.get("target_match")
        # 排除设置/辅助窗口：这些窗口与主窗口 URL 相同，容易误选
        self.target_exclude: list[str] = s.get(
            "target_exclude",
            ["windowKind=settings", "windowAppId=settings",
             "windowPreset=utility", "windowKind=sub"],
        )
        self.page_ready_timeout = s.get("page_ready_timeout", 15)
        self.actions: list[dict] = s.get("actions", [])
        self.kill_process: Optional[str] = s.get("kill_process")
        # 仅当客户端是本次由工具拉起时才关闭（避免误关用户正在使用的客户端）
        self.kill_after_launch: bool = s.get("kill_after_launch", False)
        self._launched_by_tool = False
        # 客户端在运行但没开调试端口时，是否重启它（手动启动的客户端无法附加）
        self.force_restart: bool = s.get("force_restart", False)
        self.force_kill: bool = s.get("force_kill", False)   # false=绝不强杀，避免丢未保存内容
        self.graceful_timeout: int = s.get("graceful_timeout", 12)
        self.process_name: Optional[str] = s.get("process_name", self.kill_process)

    # ------------------------------------------------------------------
    @property
    def endpoint(self) -> str:
        return f"http://{self.host}:{self.port}"

    def endpoint_ready(self) -> bool:
        try:
            r = requests.get(f"{self.endpoint}/json/version", timeout=3)
            return r.status_code == 200
        except Exception:
            return False

    def ensure_started(self) -> tuple[bool, str]:
        """确保客户端调试端口可用；未就绪且配置了 launch_cmd 时自动拉起。

        返回 (ok, message)
        """
        if self.endpoint_ready():
            return True, "调试端口已就绪"

        # 客户端在运行但未开调试端口（例如用户手动启动）-> 按需重启
        if self.force_restart and self.process_name and self._process_running():
            log.info("检测到 %s 正在运行但未开启调试端口，尝试重启以启用调试...", self.process_name)
            print(f"{self.process_name} 正在运行但未开启调试端口，正在重启以启用调试...")
            if not self._restart_process():
                return False, (
                    f"{self.process_name} 正在运行且未能正常退出（可能有未保存内容）。\n"
                    f"已跳过，避免丢失数据。请手动关闭该客户端后重试，"
                    f"或直接用它自身的签到入口完成签到。"
                )

        if not self.launch_cmd:
            return False, (
                f"CDP 端口 {self.port} 未就绪，且未配置 launch_cmd。\n"
                f"请先以调试模式启动客户端：\n"
                f'  "<客户端真实路径>.exe" --remote-debugging-port={self.port}\n'
                f"（客户端需完全退出后重新启动才生效；或在配置中填写 launch_cmd 自动拉起）"
            )

        # 客户端已在运行但没开调试端口（手动启动/自动更新后自行重启）
        # 此时再启动新实例无效（单实例），直接给出明确提示，避免空等
        if self.process_name and self._process_running():
            return False, (
                f"{self.process_name} 已在运行，但没有开启调试端口。\n"
                f"常见原因：手动启动未带参数，或客户端自动更新后自行重启。\n"
                f"处理：完全退出该客户端后重试（工具会用 "
                f"--remote-debugging-port={self.port} 自动拉起它）"
            )

        log.info("正在启动客户端: %s", self.launch_cmd)
        print(f"正在启动客户端并等待调试端口 {self.port}（最多 {self.ready_timeout} 秒）...")
        try:
            # 客户端日志不混入本工具输出
            subprocess.Popen(
                self.launch_cmd, shell=True,
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                stdin=subprocess.DEVNULL,
            )
            self._launched_by_tool = True
        except Exception as e:
            return False, f"启动客户端失败: {e}"

        deadline = time.time() + self.ready_timeout
        while time.time() < deadline and not self.endpoint_ready():
            time.sleep(1)
        if not self.endpoint_ready():
            return False, (
                f"客户端已启动，但调试端口 {self.port} 仍未就绪。请检查：\n"
                f"  1) launch_cmd 中的客户端路径是否正确（右键快捷方式→属性→目标）\n"
                f"  2) 启动参数是否带 --remote-debugging-port={self.port}\n"
                f"  3) 客户端是否已在运行（单实例模式下新参数不生效，需先完全退出）"
            )
        time.sleep(self.wait_after_launch)
        return True, "客户端已启动"

    def list_targets(self, auto_start: bool = True) -> list[dict]:
        """列出客户端当前所有可调试页面，用于确定 target_match"""
        if auto_start and not self.endpoint_ready():
            ok, msg = self.ensure_started()
            print(msg)
            if not ok:
                return []
        try:
            r = requests.get(f"{self.endpoint}/json/list", timeout=5)
            items = [t for t in r.json() if t.get("type") == "page"]
            if not items:
                print("已连接，但客户端当前没有可调试页面（请先打开签到页/积分页）")
            for i, t in enumerate(items):
                print(f"[{i}] title={t.get('title')!r}\n     url={t.get('url')!r}")
            return items
        except Exception as e:
            print(f"无法连接 CDP 端点 {self.endpoint}: {e}")
            return []

    # ------------------------------------------------------------------
    def checkin(self) -> CheckResult:
        started = time.time()
        try:
            from playwright.sync_api import sync_playwright
        except ImportError:
            return CheckResult(
                platform=self.platform, task_name=self.name, success=False,
                message="未安装 playwright，请执行: pip install playwright",
                duration=round(time.time() - started, 2),
            )

        # 1) 确保客户端已启动且调试端口可用
        ok, msg = self.ensure_started()
        if not ok:
            return CheckResult(
                platform=self.platform, task_name=self.name, success=False,
                message=msg,
                duration=round(time.time() - started, 2),
            )

        last_text = ""
        already = False
        try:
            with sync_playwright() as p:
                browser = p.chromium.connect_over_cdp(self.endpoint)
                context = browser.contexts[0]
                page = self._pick_page(context)
                log.info("已连接客户端页面: %s", page.url)
                for action in self.actions:
                    out = self._run_action(page, action)
                    if out.get("text"):
                        last_text = out["text"]
                    if out.get("already"):
                        already = True
                        break
                    if out.get("stop"):
                        break
                # 断开连接（不会关闭客户端）
                browser.close()

            if already:
                message = f"今日已签到（页面显示: {last_text}）"
            else:
                message = f"客户端签到流程执行完成: {last_text}" if last_text \
                    else "客户端签到流程执行完成"
            return CheckResult(
                platform=self.platform, task_name=self.name, success=True,
                already_checked=already,
                message=message[:200],
                extra={"endpoint": self.endpoint},
                duration=round(time.time() - started, 2),
            )
        except Exception as e:
            log.exception("[%s/%s] 客户端签到异常", self.platform, self.name)
            return CheckResult(
                platform=self.platform, task_name=self.name, success=False,
                message=f"客户端执行异常: {e}",
                duration=round(time.time() - started, 2),
            )
        finally:
            should_kill = self.kill_process and (
                not self.kill_after_launch or self._launched_by_tool
            )
            if should_kill:
                self._kill_process()

    # ------------------------------------------------------------------
    def _process_running(self) -> bool:
        """判断客户端进程是否在运行"""
        if not self.process_name:
            return False
        try:
            out = subprocess.run(
                f'tasklist /FI "IMAGENAME eq {self.process_name}" /NH',
                shell=True, capture_output=True, text=True, timeout=15,
            ).stdout or ""
            return self.process_name.lower() in out.lower()
        except Exception:
            return False

    def _kill_process(self) -> None:
        if not self.kill_process:
            return
        subprocess.run(
            f'taskkill /IM "{self.kill_process}" /F',
            shell=True, capture_output=True,
        )
        log.info("已关闭客户端进程: %s", self.kill_process)

    def _restart_process(self) -> bool:
        """优雅关闭客户端（不丢数据），必要时按配置强杀。返回是否已退出"""
        if not self.process_name:
            return False
        # 先优雅关闭（等同点窗口右上角关闭，IDE 会弹保存提示并阻塞退出）
        subprocess.run(
            f'taskkill /IM "{self.process_name}"',
            shell=True, capture_output=True,
        )
        deadline = time.time() + self.graceful_timeout
        while time.time() < deadline:
            if not self._process_running():
                log.info("%s 已正常退出", self.process_name)
                return True
            time.sleep(1)

        if self.force_kill:
            log.warning("%s 未响应关闭，执行强制结束", self.process_name)
            subprocess.run(
                f'taskkill /IM "{self.process_name}" /F',
                shell=True, capture_output=True,
            )
            time.sleep(2)
            return not self._process_running()

        log.warning("%s 未能在 %d 秒内退出（可能有未保存内容）", self.process_name, self.graceful_timeout)
        return False

    # ------------------------------------------------------------------
    def _page_has_anchor(self, page) -> bool:
        """判断页面是否包含签到流程的关键元素（用于在多个窗口中挑出主窗口）"""
        try:
            for action in self.actions:
                if "click_selector" in action:
                    if page.evaluate(_BOX_OF_SELECTOR_JS, action["click_selector"]):
                        return True
            for action in self.actions:
                for key in ("already_text", "click_text", "click_text_if_exists"):
                    if key in action:
                        texts = action[key]
                        texts = texts if isinstance(texts, list) else [texts]
                        if any(self._find_text_box(page, t, True) for t in texts):
                            return True
                if "click_near" in action:
                    cfg = action["click_near"]
                    if page.evaluate(_CLICK_NEAR_JS, [
                        cfg.get("text", ""), cfg.get("target", "button"),
                        cfg.get("match", "exact"),
                    ]):
                        return True
        except Exception:
            return False
        return False

    def _pick_page(self, context):
        """在多个窗口中选出真正的主窗口

        策略：
          1) 按 target_match 过滤
          2) 排除设置/辅助窗口（URL 含 target_exclude 任一关键字）
          3) 优先选择包含签到锚点元素的窗口（避免选到同名 URL 的其它窗口）
          4) 都找不到时退回首个候选并记录告警
        """
        pages = [p for p in context.pages if (p.url or "").startswith(("http", "file", "vscode-file"))]
        if not pages:
            raise RuntimeError("客户端没有可调试页面")

        candidates = pages
        if self.target_match:
            key = self.target_match
            matched = [p for p in pages
                       if key in (p.url or "") or key in (p.title() or "")]
            if matched:
                candidates = matched

        filtered = [p for p in candidates
                    if not any(pat in (p.url or "") for pat in self.target_exclude)]
        if not filtered:
            raise RuntimeError(
                "只找到设置/辅助窗口，没有可用的主窗口。可用窗口: "
                + ", ".join((p.title() or p.url)[:60] for p in candidates)
                + "；请关闭这些窗口后重试，或调整 target_exclude"
            )

        deadline = time.time() + self.page_ready_timeout
        while True:
            for pg in filtered:
                if self._page_has_anchor(pg):
                    log.info("选中窗口: %s", (pg.title() or pg.url)[:80])
                    return pg
            if time.time() >= deadline:
                break
            time.sleep(0.5)

        pg = filtered[0]
        log.warning("未能确认主窗口，退回首个候选窗口: %s", (pg.title() or pg.url)[:80])
        return pg

    # ------------------------------------------------------------------
    def _find_text_box(self, page, text: str, exact: bool = True):
        """在页面中查找可见的、文本匹配的元素，返回其中心坐标"""
        return page.evaluate(_FIND_TEXT_JS, [text, exact])

    def _run_action(self, page, action: dict) -> dict:
        """执行一个动作，返回 {text, already, stop}

        支持: eval / click / click_selector / click_text / click_text_if_exists /
              wait_text / expect_text / already_text / dump_text /
              wait / wait_for_selector / press / goto / screenshot
        """
        # ---------- JS ----------
        if "eval" in action:
            value = page.evaluate(action["eval"])
            log.info("eval 返回: %s", str(value)[:150])
            return {"text": str(value)}

        if "dump_text" in action:
            text = page.inner_text("body")
            log.info("页面文本片段: %s", text[:300].replace("\n", " | "))
            return {"text": text[:60]}

        # ---------- 点击 ----------
        if "click_selector" in action:
            box = page.evaluate(_BOX_OF_SELECTOR_JS, action["click_selector"])
            if not box:
                raise RuntimeError(f"未找到选择器元素: {action['click_selector']}")
            page.mouse.click(box["x"], box["y"])
            log.info("已点击选择器 %s (%.0f,%.0f)",
                     action["click_selector"], box["x"], box["y"])

        elif "click_text" in action:
            texts = action["click_text"]
            texts = texts if isinstance(texts, list) else [texts]
            exact = bool(action.get("exact", True))
            for t in texts:
                box = self._find_text_box(page, t, exact)
                if box:
                    page.mouse.click(box["x"], box["y"])
                    log.info("已点击文本 %r (%.0f,%.0f)", box.get("text", t), box["x"], box["y"])
                    return {"text": box.get("text", t)}
            raise RuntimeError(f"未找到可点击文本: {texts}")

        elif "click_text_if_exists" in action:
            texts = action["click_text_if_exists"]
            texts = texts if isinstance(texts, list) else [texts]
            exact = bool(action.get("exact", True))
            for t in texts:
                box = self._find_text_box(page, t, exact)
                if box:
                    page.mouse.click(box["x"], box["y"])
                    log.info("已点击文本 %r", box.get("text", t))
                    return {"text": box.get("text", t)}
            log.info("未找到文本 %s，跳过点击", texts)
            return {}

        elif "click_near" in action:
            # 按"行标题"定位同一条目内的按钮，适合列表型活动（按钮文案会变的场景）
            cfg = action["click_near"]
            box = page.evaluate(
                _CLICK_NEAR_JS,
                [cfg.get("text", ""), cfg.get("target", "button"), cfg.get("match", "exact")],
            )
            if not box:
                raise RuntimeError(f"未在 '{cfg.get('text')}' 所在条目中找到可点击元素")
            page.mouse.click(box["x"], box["y"])
            log.info("已在 '%s' 条目中点击 %r", cfg.get("text"), box.get("text"))
            return {"text": box.get("text", "")}

        # ---------- 状态判定 ----------
        elif "already_text" in action:
            texts = action["already_text"]
            texts = texts if isinstance(texts, list) else [texts]
            exact = bool(action.get("exact", True))
            for t in texts:
                if self._find_text_box(page, t, exact):
                    log.info("检测到已签到状态: %r", t)
                    return {"text": t, "already": True, "stop": True}
            return {}

        elif "expect_text" in action:
            texts = action["expect_text"]
            texts = texts if isinstance(texts, list) else [texts]
            exact = bool(action.get("exact", True))
            timeout = int(action.get("timeout", 8)) * 1000   # 轮询等待，避免渲染延迟误判
            waited = 0
            while True:
                for t in texts:
                    box = self._find_text_box(page, t, exact)
                    if box:
                        return {"text": box.get("text", t)}
                if waited >= timeout:
                    break
                page.wait_for_timeout(500)
                waited += 500
            raise RuntimeError(f"未检测到预期文本: {texts}")

        elif "wait_text" in action:
            texts = action["wait_text"]
            texts = texts if isinstance(texts, list) else [texts]
            timeout = int(action.get("timeout", 15)) * 1000
            waited = 0
            while waited < timeout:
                for t in texts:
                    box = self._find_text_box(page, t, True)
                    if box:
                        log.info("已等到文本 %r", t)
                        return {"text": box.get("text", t)}
                page.wait_for_timeout(500)
                waited += 500
            raise RuntimeError(f"等待文本超时: {texts}")

        # ---------- 其他 ----------
        elif "click" in action:
            page.click(action["click"], timeout=15000)

        elif "wait_for_selector" in action:
            page.wait_for_selector(action["wait_for_selector"], timeout=20000)

        elif "wait" in action:
            page.wait_for_timeout(int(action["wait"]))

        elif "press" in action:
            page.keyboard.press(action["press"])

        elif "goto" in action:
            page.goto(action["goto"], wait_until="domcontentloaded")

        elif "screenshot" in action:
            # 截图仅作留证，失败不应影响签到结果判定
            try:
                out = Path(action["screenshot"])
                out.parent.mkdir(parents=True, exist_ok=True)
                page.screenshot(path=str(out), full_page=True, timeout=8000)
            except Exception as e:
                log.warning("截图失败（不影响签到）: %s", e)

        else:
            raise ValueError(f"未知的客户端动作: {action}")
        return {}


_FIND_TEXT_JS = """(args) => {
  const [text, exact] = args;
  const matches = [];
  const collect = (root) => {
    let nodes;
    try { nodes = root.querySelectorAll('*'); } catch (e) { return; }
    for (const el of nodes) {
      try {
        if (el.shadowRoot) collect(el.shadowRoot);   // 穿透 shadow DOM
        const r = el.getBoundingClientRect();
        if (!r.width || !r.height) continue;
        const st = window.getComputedStyle(el);
        if (st.visibility === 'hidden' || st.display === 'none' || st.opacity === '0') continue;
        const t = (el.innerText || '').trim();
        if (!t || t.length > 60) continue;
        const hit = exact ? t === text : t.includes(text);
        if (!hit) continue;
        // 跳过"含同样文本子元素"的父节点，取最深的那个
        let childSame = false;
        for (const c of el.children) {
          const ct = (c.innerText || '').trim();
          if (exact ? ct === t : ct.includes(text)) { childSame = true; break; }
        }
        if (childSame) continue;
        matches.push(el);
      } catch (e) {}
    }
  };
  collect(document);
  if (!matches.length) return null;
  const el = matches[0];
  el.scrollIntoView({block: 'center', inline: 'center'});
  const rr = el.getBoundingClientRect();
  return {x: rr.x + rr.width / 2, y: rr.y + rr.height / 2,
          text: (el.innerText || '').trim().slice(0, 40)};
}"""

_CLICK_NEAR_JS = """(args) => {
  const [text, targetSel, matchMode] = args;
  const contains = matchMode === 'contains';
  const matches = [];
  const collect = (root) => {
    let nodes;
    try { nodes = root.querySelectorAll('*'); } catch (e) { return; }
    for (const el of nodes) {
      try {
        if (el.shadowRoot) collect(el.shadowRoot);
        const r = el.getBoundingClientRect();
        if (!r.width || !r.height) continue;
        const st = window.getComputedStyle(el);
        if (st.visibility === 'hidden' || st.display === 'none') continue;
        const t = (el.innerText || '').trim();
        if (!t || t.length > 40) continue;
        if (contains ? t.includes(text) : t === text) matches.push(el);
      } catch (e) {}
    }
  };
  collect(document);
  if (!matches.length) return null;
  let node = matches[0];
  for (let i = 0; i < 6 && node; i++) {
    if (node.querySelector) {
      const btn = node.querySelector(targetSel);
      if (btn) {
        const r = btn.getBoundingClientRect();
        const st = window.getComputedStyle(btn);
        if (r.width && r.height && st.visibility !== 'hidden' && st.display !== 'none') {
          return {x: r.x + r.width / 2, y: r.y + r.height / 2,
                  text: (btn.innerText || '').trim().slice(0, 30)};
        }
      }
    }
    node = node.parentElement;
  }
  return null;
}"""

_BOX_OF_SELECTOR_JS = """(sel) => {
  const deepQuery = (root) => {
    let el = null;
    try { el = root.querySelector(sel); } catch (e) { return null; }
    if (el) return el;
    for (const node of root.querySelectorAll('*')) {
      if (node.shadowRoot) {
        const found = deepQuery(node.shadowRoot);
        if (found) return found;
      }
    }
    return null;
  };
  const el = deepQuery(document);
  if (!el) return null;
  el.scrollIntoView({block: 'center', inline: 'center'});
  const r = el.getBoundingClientRect();
  if (!r.width || !r.height) return null;
  return {x: r.x + r.width / 2, y: r.y + r.height / 2};
}"""
