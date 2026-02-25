from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta, timezone


class StateStore:
    def __init__(self, db_path: str) -> None:
        self.db_path = db_path

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def init_db(self) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS seen_items (
                    item_id TEXT PRIMARY KEY,
                    canonical_url TEXT NOT NULL,
                    seen_at TEXT NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS run_logs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    run_at TEXT NOT NULL,
                    status TEXT NOT NULL,
                    metrics_json TEXT NOT NULL,
                    error_message TEXT
                )
                """
            )

    def load_seen_item_ids(self, days: int = 7) -> set[str]:
        since = datetime.now(timezone.utc) - timedelta(days=days)
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT item_id FROM seen_items WHERE seen_at >= ?",
                (since.isoformat(),),
            ).fetchall()
        return {row["item_id"] for row in rows}

    def mark_seen(self, items: list[tuple[str, str]]) -> None:
        now = datetime.now(timezone.utc).isoformat()
        with self._connect() as conn:
            conn.executemany(
                """
                INSERT OR REPLACE INTO seen_items(item_id, canonical_url, seen_at)
                VALUES (?, ?, ?)
                """,
                [(item_id, canonical_url, now) for item_id, canonical_url in items],
            )

    def has_recent_successful_push_run(self, hours: int = 24) -> bool:
        since = datetime.now(timezone.utc) - timedelta(hours=hours)
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT metrics_json
                FROM run_logs
                WHERE status = 'success' AND run_at >= ?
                ORDER BY id DESC
                """,
                (since.isoformat(),),
            ).fetchall()
        for row in rows:
            try:
                metrics = json.loads(row["metrics_json"] or "{}")
            except json.JSONDecodeError:
                continue
            if metrics.get("push_enabled") and int(metrics.get("selected_count") or 0) > 0:
                return True
        return False

    def log_run(self, status: str, metrics: dict, error_message: str | None = None) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO run_logs(run_at, status, metrics_json, error_message)
                VALUES (?, ?, ?, ?)
                """,
                (
                    datetime.now(timezone.utc).isoformat(),
                    status,
                    json.dumps(metrics, ensure_ascii=False),
                    error_message,
                ),
            )
