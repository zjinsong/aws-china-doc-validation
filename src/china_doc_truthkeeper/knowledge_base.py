import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path


class KnowledgeBase:
    def __init__(self, database_path: Path):
        self.database_path = database_path
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def _connect(self):
        connection = sqlite3.connect(self.database_path)
        connection.row_factory = sqlite3.Row
        return connection

    def _initialize(self) -> None:
        conn = self._connect()
        try:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS feature_checks (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    service TEXT NOT NULL,
                    feature TEXT NOT NULL,
                    region TEXT NOT NULL,
                    status TEXT NOT NULL,
                    evidence TEXT NOT NULL,
                    checked_at TEXT NOT NULL,
                    UNIQUE(service, feature, region)
                );
                """
            )
            conn.commit()
        finally:
            conn.close()

    def save_check(self, service: str, feature: str, region: str, status: str, evidence: dict) -> dict:
        checked_at = datetime.now(timezone.utc).isoformat()
        conn = self._connect()
        try:
            conn.execute(
                """INSERT INTO feature_checks(service, feature, region, status, evidence, checked_at)
                   VALUES (?, ?, ?, ?, ?, ?)
                   ON CONFLICT(service, feature, region) DO UPDATE SET
                     status=excluded.status, evidence=excluded.evidence, checked_at=excluded.checked_at""",
                (service, feature, region, status, json.dumps(evidence, ensure_ascii=False), checked_at),
            )
            conn.commit()
        finally:
            conn.close()
        return self.find_check(service, feature, region) or {}

    def find_check(self, service: str, feature: str, region: str) -> dict | None:
        conn = self._connect()
        try:
            row = conn.execute(
                "SELECT service, feature, region, status, evidence, checked_at FROM feature_checks WHERE service=? AND feature=? AND region=?",
                (service, feature, region),
            ).fetchone()
        finally:
            conn.close()
        if not row:
            return None
        result = dict(row)
        result["evidence"] = json.loads(result["evidence"])
        return result

    def search(self, query: str, limit: int = 10) -> list[dict]:
        pattern = f"%{query.lower()}%"
        conn = self._connect()
        try:
            rows = conn.execute(
                """SELECT service, feature, region, status, evidence, checked_at FROM feature_checks
                   WHERE lower(service) LIKE ? OR lower(feature) LIKE ? ORDER BY checked_at DESC LIMIT ?""",
                (pattern, pattern, limit),
            ).fetchall()
        finally:
            conn.close()
        return [{**dict(row), "evidence": json.loads(row["evidence"])} for row in rows]
