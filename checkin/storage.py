"""签到结果持久化（SQLite）"""
from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterable, Optional

from .models import CheckResult

_SCHEMA = """
CREATE TABLE IF NOT EXISTS checkin_records (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    platform TEXT NOT NULL,
    task_name TEXT NOT NULL,
    success INTEGER NOT NULL,
    already_checked INTEGER DEFAULT 0,
    message TEXT,
    points REAL,
    extra TEXT,
    duration REAL,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_records_lookup
    ON checkin_records(platform, task_name, created_at);
"""


class Storage:
    def __init__(self, db_path: str | Path = "data/checkin.db"):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            conn.executescript(_SCHEMA)

    @contextmanager
    def _connect(self):
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    def save(self, result: CheckResult) -> None:
        with self._connect() as conn:
            conn.execute(
                """INSERT INTO checkin_records
                   (platform, task_name, success, already_checked, message, points, extra, duration, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    result.platform,
                    result.task_name,
                    int(result.success),
                    int(result.already_checked),
                    result.message,
                    result.points,
                    str(result.extra),
                    result.duration,
                    result.created_at,
                ),
            )

    def save_all(self, results: Iterable[CheckResult]) -> None:
        for r in results:
            self.save(r)

    def history(self, limit: int = 50) -> list[dict]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM checkin_records ORDER BY id DESC LIMIT ?", (limit,)
            ).fetchall()
        return [dict(r) for r in rows]

    def last_result(self, platform: str, task_name: str) -> Optional[dict]:
        with self._connect() as conn:
            row = conn.execute(
                """SELECT * FROM checkin_records
                   WHERE platform = ? AND task_name = ?
                   ORDER BY id DESC LIMIT 1""",
                (platform, task_name),
            ).fetchone()
        return dict(row) if row else None
