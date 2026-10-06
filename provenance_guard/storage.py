"""SQLite persistence for submissions and structured audit events."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from typing import Any


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def connect(path: str) -> sqlite3.Connection:
    connection = sqlite3.connect(path)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA journal_mode=WAL")
    return connection


def init_db(path: str) -> None:
    with connect(path) as db:
        db.executescript(
            """
            CREATE TABLE IF NOT EXISTS contents (
                content_id TEXT PRIMARY KEY,
                creator_id TEXT NOT NULL,
                text TEXT NOT NULL,
                status TEXT NOT NULL,
                attribution TEXT NOT NULL,
                confidence REAL NOT NULL,
                label TEXT NOT NULL,
                signals_json TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS audit_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                event_type TEXT NOT NULL,
                content_id TEXT NOT NULL,
                creator_id TEXT NOT NULL,
                timestamp TEXT NOT NULL,
                attribution TEXT NOT NULL,
                confidence REAL NOT NULL,
                status TEXT NOT NULL,
                signals_json TEXT NOT NULL,
                appeal_reason TEXT
            );
            """
        )


def insert_content(path: str, record: dict[str, Any]) -> None:
    with connect(path) as db:
        db.execute(
            """INSERT INTO contents
            (content_id, creator_id, text, status, attribution, confidence, label, signals_json, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                record["content_id"], record["creator_id"], record["text"], record["status"],
                record["attribution"], record["confidence"], record["label"],
                json.dumps(record["signals"]), record["created_at"],
            ),
        )
        _insert_audit(db, record)


def _insert_audit(db: sqlite3.Connection, record: dict[str, Any]) -> None:
    db.execute(
        """INSERT INTO audit_log
        (event_type, content_id, creator_id, timestamp, attribution, confidence, status, signals_json, appeal_reason)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            record.get("event_type", "classification"), record["content_id"], record["creator_id"],
            record.get("timestamp", record.get("created_at", utc_now())), record["attribution"],
            record["confidence"], record["status"], json.dumps(record.get("signals", {})),
            record.get("appeal_reason"),
        ),
    )


def get_content(path: str, content_id: str) -> dict[str, Any] | None:
    with connect(path) as db:
        row = db.execute("SELECT * FROM contents WHERE content_id = ?", (content_id,)).fetchone()
    if row is None:
        return None
    result = dict(row)
    result["signals"] = json.loads(result.pop("signals_json"))
    return result


def update_status_and_add_appeal(path: str, content_id: str, creator_id: str, reason: str) -> dict[str, Any] | None:
    timestamp = utc_now()
    with connect(path) as db:
        row = db.execute("SELECT * FROM contents WHERE content_id = ?", (content_id,)).fetchone()
        if row is None:
            return None
        db.execute("UPDATE contents SET status = ? WHERE content_id = ?", ("under_review", content_id))
        record = dict(row)
        record["status"] = "under_review"
        record["signals"] = json.loads(record.pop("signals_json"))
        record["event_type"] = "appeal"
        record["timestamp"] = timestamp
        record["appeal_reason"] = reason
        _insert_audit(db, record)
    record["appeal_reason"] = reason
    return record


def list_audit(path: str, limit: int = 100) -> list[dict[str, Any]]:
    with connect(path) as db:
        rows = db.execute(
            "SELECT * FROM audit_log ORDER BY id ASC LIMIT ?", (limit,)
        ).fetchall()
    entries = []
    for row in rows:
        item = dict(row)
        item["signals"] = json.loads(item.pop("signals_json"))
        entries.append(item)
    return entries

