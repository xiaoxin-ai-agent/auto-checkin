#!/usr/bin/env python3
"""自动签到工具入口

用法:
    python main.py run                  # 立即执行所有启用的签到任务
    python main.py run --task trae      # 只执行指定任务（支持 platform 或 name）
    python main.py run --no-notify      # 不发送通知
    python main.py daemon               # 常驻进程，按 schedule 配置定时执行
    python main.py login --task trae    # 浏览器模式：有头登录并保存登录态
    python main.py cdp-targets --task trae  # 探测客户端可调试页面（确定 target_match）
    python main.py list                 # 列出所有任务
    python main.py history [--limit 20] # 查看签到历史
"""
from __future__ import annotations

import argparse
import logging
import os
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE_DIR))

# 计划任务用 pythonw.exe 静默运行时没有控制台，stdout/stderr 为 None
# 这里兜底到 devnull，避免 print() 抛异常导致任务失败
if sys.stdout is None:
    sys.stdout = open(os.devnull, "w", encoding="utf-8")
if sys.stderr is None:
    sys.stderr = open(os.devnull, "w", encoding="utf-8")

# 固定以项目目录为工作目录，保证 config/logs/data 相对路径始终正确
os.chdir(BASE_DIR)

from checkin.config import Config          # noqa: E402
from checkin.engine import Engine          # noqa: E402
from checkin.logger import get_logger      # noqa: E402
from checkin.storage import Storage        # noqa: E402

log = get_logger("checkin.main")


def _load_config(path: str) -> Config:
    cfg_path = Path(path)
    if not cfg_path.exists():
        example = BASE_DIR / "config.example.yaml"
        print(f"配置文件 {path} 不存在。请复制 {example.name} 为 config.yaml 后修改。")
        sys.exit(1)
    return Config.load(cfg_path)


def cmd_run(args) -> None:
    config = _load_config(args.config)
    engine = Engine(config)
    results = engine.run(only=args.task, notify=not args.no_notify)
    failed = [r for r in results if not r.success]
    print("\n========== 签到结果 ==========")
    for r in results:
        print(r.summary())
    print(f"==============================\n共 {len(results)} 项，成功 {len(results) - len(failed)} 项")
    sys.exit(1 if failed else 0)


def cmd_daemon(args) -> None:
    from apscheduler.schedulers.blocking import BlockingScheduler

    config = _load_config(args.config)
    engine = Engine(config)
    schedule = config.schedule_cfg
    cron = schedule.get("cron", "30 8 * * *")  # 默认每天 08:30

    print(f"定时模式已启动，cron: {cron}（如 '30 8 * * *' 表示每天 08:30）")
    scheduler = BlockingScheduler(timezone=schedule.get("timezone", "Asia/Shanghai"))
    scheduler.add_job(engine.run, "cron",
                      hour=cron.split()[1], minute=cron.split()[0],
                      day=cron.split()[2], month=cron.split()[3],
                      day_of_week=cron.split()[4])
    try:
        engine.run()  # 启动时先跑一次
        scheduler.start()
    except (KeyboardInterrupt, SystemExit):
        print("已退出定时模式")


def cmd_login(args) -> None:
    from checkin.checkers.browser_checker import BrowserChecker

    config = _load_config(args.config)
    tasks = config.tasks  # 登录是配置前置步骤，不要求 enabled
    if args.task:
        tasks = [t for t in tasks
                 if t.get("name") in args.task or t.get("platform") in args.task]
    if not tasks:
        names = [t.get("name") for t in config.tasks]
        print(f"未找到匹配任务，可用任务: {names}")
        sys.exit(1)
    for task_cfg in tasks:
        name = task_cfg.get("name")
        BrowserChecker.interactive_login(name, task_cfg, config.global_cfg)


def cmd_cdp_targets(args) -> None:
    """列出客户端 CDP 可调试页面，帮助确定 target_match 配置
    注意：不要求任务 enabled（首次配置时可用），也允许尚未启用 cdp 的任务先探测。
    """
    from checkin.checkers.cdp_checker import CdpChecker

    config = _load_config(args.config)
    tasks = config.tasks
    if args.task:
        wanted = set(args.task)
        tasks = [t for t in tasks
                 if t.get("name") in wanted or t.get("platform") in wanted]
        if not tasks:
            names = [t.get("name") for t in config.tasks]
            print(f"未找到匹配任务 {args.task}\n可用任务: {names}")
            sys.exit(1)

    # 只有 cdp 类型才有调试端口；非 cdp 任务给出明确提示
    cdp_tasks = [t for t in tasks if t.get("checker") == "cdp"]
    if not cdp_tasks:
        info = [(t.get("name"), t.get("checker", "http")) for t in tasks]
        print(f"以下任务不是 cdp 类型: {info}")
        print("请将对应任务的 checker 改为 cdp 后再试（或检查任务名是否写对）")
        sys.exit(1)

    for task_cfg in cdp_tasks:
        checker = CdpChecker(task_cfg.get("name"), task_cfg, config.global_cfg)
        print(f"\n=== {task_cfg.get('name')}  ({checker.endpoint}) ===")
        checker.list_targets()


def cmd_list(args) -> None:
    config = _load_config(args.config)
    tasks = config.tasks
    if not tasks:
        print("配置中没有任务")
        return
    print(f"{'任务名':<20} {'平台':<15} {'类型':<10} 启用")
    print("-" * 60)
    for t in tasks:
        print(f"{t.get('name', ''):<20} {t.get('platform', ''):<15} "
              f"{t.get('checker', 'http'):<10} {'是' if t.get('enabled', True) else '否'}")


def cmd_history(args) -> None:
    storage = Storage()
    records = storage.history(args.limit)
    if not records:
        print("暂无签到记录")
        return
    for r in records:
        status = "成功" if r["success"] else ("已签到" if r["already_checked"] else "失败")
        pts = f" 积分+{r['points']}" if r["points"] is not None else ""
        print(f"{r['created_at']}  [{status}] {r['platform']}/{r['task_name']}{pts}  {r['message'] or ''}")


def main() -> None:
    parser = argparse.ArgumentParser(description="多平台自动签到工具")
    parser.add_argument("--config", default="config.yaml", help="配置文件路径")
    sub = parser.add_subparsers(dest="command", required=True)

    p_run = sub.add_parser("run", help="立即执行签到")
    p_run.add_argument("--task", action="append", help="只执行指定任务（可多次）")
    p_run.add_argument("--no-notify", action="store_true", help="不发送通知")
    p_run.set_defaults(func=cmd_run)

    p_daemon = sub.add_parser("daemon", help="定时模式常驻运行")
    p_daemon.set_defaults(func=cmd_daemon)

    p_login = sub.add_parser("login", help="浏览器交互式登录，保存登录态")
    p_login.add_argument("--task", action="append", required=True)
    p_login.set_defaults(func=cmd_login)

    p_cdp = sub.add_parser("cdp-targets", help="探测客户端可调试页面(CDP)")
    p_cdp.add_argument("--task", action="append", help="指定任务（可多次）")
    p_cdp.set_defaults(func=cmd_cdp_targets)

    p_list = sub.add_parser("list", help="列出所有任务")
    p_list.set_defaults(func=cmd_list)

    p_hist = sub.add_parser("history", help="查看签到历史")
    p_hist.add_argument("--limit", type=int, default=20)
    p_hist.set_defaults(func=cmd_history)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
