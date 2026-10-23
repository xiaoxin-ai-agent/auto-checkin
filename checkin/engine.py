"""签到引擎：任务编排、重试、随机延迟、通知、持久化"""
from __future__ import annotations

import random
import time
import logging
from typing import Optional

from .config import Config
from .models import CheckResult
from .notifier import Notifier
from .storage import Storage
from .checkers import create_checker

log = logging.getLogger("checkin.engine")


class Engine:
    def __init__(self, config: Config, storage: Optional[Storage] = None):
        self.config = config
        self.storage = storage or Storage()
        self.notifier = Notifier(config.notify_cfg)
        g = config.global_cfg
        self.jitter_min = g.get("jitter_min", 1)
        self.jitter_max = g.get("jitter_max", 5)
        self.retry = g.get("retry", 1)
        self.retry_backoff = g.get("retry_backoff", 10)

    # ------------------------------------------------------------------
    def run(self, only: Optional[list[str]] = None, notify: bool = True) -> list[CheckResult]:
        tasks = self.config.enabled_tasks(only)
        if not tasks:
            log.warning("没有匹配的启用任务")
            return []

        results: list[CheckResult] = []
        for idx, task_cfg in enumerate(tasks):
            name = task_cfg.get("name", f"task-{idx}")
            if idx > 0 and self.jitter_max > 0:
                delay = random.uniform(self.jitter_min, self.jitter_max)
                log.info("随机延迟 %.1f 秒后执行下一个任务...", delay)
                time.sleep(delay)

            checker = create_checker(name, task_cfg, self.config.global_cfg)
            result = self._run_with_retry(checker, task_cfg)
            results.append(result)
            log.info(result.summary())

        self.storage.save_all(results)
        if notify:
            self.notifier.send(results)
        return results

    # ------------------------------------------------------------------
    def _run_with_retry(self, checker, task_cfg: dict) -> CheckResult:
        max_attempts = task_cfg.get("settings", {}).get("retry", self.retry)
        max_attempts = max(1, int(max_attempts))
        result = checker.checkin()
        attempt = 1
        while not result.success and attempt < max_attempts:
            wait = self.retry_backoff * attempt
            log.warning("[%s/%s] 第 %d 次失败，%d 秒后重试...",
                        checker.platform, checker.name, attempt, wait)
            time.sleep(wait)
            result = checker.checkin()
            attempt += 1
        return result
