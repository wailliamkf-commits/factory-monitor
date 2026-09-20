"""Durable event metadata. Evidence file deletion is intentionally outside this module."""

from __future__ import annotations

import json
import sqlite3
import threading
from pathlib import Path
from typing import Any


_COLUMNS = {
    "camera_id", "kind", "triggered_at", "status", "reason", "layout_version", "analysis_status", "analysis",
    "recording_status", "evidence_path", "preview_path", "gaps", "review_label", "completed_at", "latency_ms",
}
_JSON_COLUMNS = {"analysis", "gaps"}


class EventStore:
    def __init__(self, db_path: str | Path) -> None:
        self.path = Path(db_path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._connection = sqlite3.connect(self.path, check_same_thread=False)
        self._connection.row_factory = sqlite3.Row
        with self._connection:
            self._connection.execute("PRAGMA journal_mode=WAL")
            self._connection.execute("PRAGMA synchronous=FULL")
            self._connection.execute("""
                CREATE TABLE IF NOT EXISTS events (
                    id TEXT PRIMARY KEY, camera_id TEXT NOT NULL, kind TEXT NOT NULL,
                    triggered_at REAL NOT NULL, status TEXT NOT NULL, reason TEXT NOT NULL,
                    layout_version INTEGER NOT NULL, analysis_status TEXT, analysis TEXT,
                    recording_status TEXT, evidence_path TEXT, preview_path TEXT, gaps TEXT,
                    review_label TEXT, completed_at REAL, latency_ms REAL, payload TEXT NOT NULL
                )
            """)
            self._connection.execute("CREATE INDEX IF NOT EXISTS events_completed ON events(completed_at, triggered_at)")

    def recover_interrupted_recordings(self) -> list[str]:
        """Mark abandoned recordings at exclusive runtime-writer startup only.

        Opening a store for GUI/listing/manual review must be non-mutating, so
        callers explicitly invoke this before any new recording worker starts.
        """
        recovered: list[str] = []
        with self._lock, self._connection:
            rows = self._connection.execute("SELECT id, gaps FROM events WHERE status = 'recording' OR recording_status = 'recording'").fetchall()
            for row in rows:
                try:
                    gaps = json.loads(row["gaps"]) if row["gaps"] else []
                except json.JSONDecodeError:
                    gaps = ["unreadable prior gap metadata"]
                if not isinstance(gaps, list):
                    gaps = [gaps]
                gaps.append("crash recovery: recording interrupted")
                self._connection.execute("UPDATE events SET status = ?, recording_status = ?, gaps = ? WHERE id = ?", ("incomplete", "incomplete", json.dumps(gaps), row["id"]))
                recovered.append(row["id"])
        return recovered

    @staticmethod
    def _encode(column: str, value: Any) -> Any:
        return json.dumps(value, ensure_ascii=False) if column in _JSON_COLUMNS and value is not None else value

    @staticmethod
    def _decode(row: sqlite3.Row) -> dict:
        event = json.loads(row["payload"])
        for column in _COLUMNS:
            value = row[column]
            if column in _JSON_COLUMNS and value is not None:
                try:
                    value = json.loads(value)
                except json.JSONDecodeError:
                    value = ["unreadable stored value"] if column == "gaps" else {"error": "unreadable stored value"}
            if value is not None:
                event[column] = value
        event["id"] = row["id"]
        return event

    @staticmethod
    def _validate_event(event: dict) -> None:
        needed = {"id", "camera_id", "kind", "triggered_at", "status", "reason", "layout_version"}
        missing = needed.difference(event)
        if missing:
            raise ValueError(f"event missing required fields: {', '.join(sorted(missing))}")
        if not all(isinstance(event[key], str) and event[key] for key in ("id", "camera_id", "kind", "status", "reason")):
            raise ValueError("event identity fields must be non-empty strings")
        if event["kind"] not in {"material_candidate", "station_absence"} or event["status"] not in {"candidate", "recording", "complete", "incomplete", "error"}:
            raise ValueError("event kind or status is unsupported")
        if isinstance(event["triggered_at"], bool) or not isinstance(event["triggered_at"], (int, float)) or not isinstance(event["layout_version"], int):
            raise ValueError("event timestamps/layout_version are invalid")

    def create_event(self, event: dict) -> None:
        self._validate_event(event)
        values = {column: event.get(column) for column in _COLUMNS}
        payload = json.dumps(event, ensure_ascii=False)
        columns = ["id", *_COLUMNS, "payload"]
        placeholders = ", ".join("?" for _ in columns)
        encoded = [event["id"], *(self._encode(column, values[column]) for column in _COLUMNS), payload]
        with self._lock, self._connection:
            try:
                self._connection.execute(f"INSERT INTO events ({', '.join(columns)}) VALUES ({placeholders})", encoded)
            except sqlite3.IntegrityError as exc:
                raise ValueError(f"event already exists: {event['id']}") from exc

    def update_event(self, event_id: str, **fields: Any) -> None:
        if not isinstance(event_id, str) or not event_id:
            raise ValueError("event_id must be a non-empty string")
        if not fields:
            return
        forbidden = set(fields).difference(_COLUMNS)
        if forbidden:
            raise ValueError(f"unsupported event fields: {', '.join(sorted(forbidden))}")
        if "status" in fields and fields["status"] not in {"candidate", "recording", "complete", "incomplete", "error"}:
            raise ValueError("unsupported event status")
        assignments = ", ".join(f"{column} = ?" for column in fields)
        with self._lock, self._connection:
            row = self._connection.execute("SELECT payload FROM events WHERE id = ?", (event_id,)).fetchone()
            if row is None:
                raise KeyError(event_id)
            payload = json.loads(row["payload"])
            payload.update(fields)
            parameters = [self._encode(column, value) for column, value in fields.items()]
            parameters.extend([json.dumps(payload, ensure_ascii=False), event_id])
            changed = self._connection.execute(f"UPDATE events SET {assignments}, payload = ? WHERE id = ?", parameters)
            if changed.rowcount != 1:
                raise KeyError(event_id)

    def get_event(self, event_id: str) -> dict | None:
        with self._lock:
            row = self._connection.execute("SELECT * FROM events WHERE id = ?", (event_id,)).fetchone()
        return self._decode(row) if row else None

    def list_events(self, limit: int = 100) -> list[dict]:
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 10_000:
            raise ValueError("limit must be an integer between 1 and 10000")
        with self._lock:
            rows = self._connection.execute("SELECT * FROM events ORDER BY triggered_at DESC, id DESC LIMIT ?", (limit,)).fetchall()
        return [self._decode(row) for row in rows]

    def prune_completed(self, retain: int = 20) -> list[dict]:
        if isinstance(retain, bool) or not isinstance(retain, int) or retain < 0:
            raise ValueError("retain must be a non-negative integer")
        with self._lock, self._connection:
            rows = self._connection.execute("""
                SELECT * FROM events
                WHERE completed_at IS NOT NULL
                  AND (analysis_status IS NULL OR analysis_status <> 'pending')
                ORDER BY completed_at DESC, id DESC
            """).fetchall()
            removable = rows[retain:]
            for row in removable:
                self._connection.execute("""
                    DELETE FROM events
                    WHERE id = ? AND completed_at IS NOT NULL
                      AND (analysis_status IS NULL OR analysis_status <> 'pending')
                """, (row["id"],))
        return [self._decode(row) for row in removable]

    def close(self) -> None:
        with self._lock:
            self._connection.close()
