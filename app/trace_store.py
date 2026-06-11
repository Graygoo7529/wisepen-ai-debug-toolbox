import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.models import DebugEvent, TurnView


class TraceStore:
    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self.db_path)

    def _init_db(self) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS turns (
                    id TEXT PRIMARY KEY,
                    session_id TEXT NOT NULL,
                    query TEXT NOT NULL,
                    model_id TEXT,
                    status TEXT,
                    started_at TEXT,
                    finished_at TEXT,
                    error TEXT
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    turn_id TEXT NOT NULL,
                    seq INTEGER NOT NULL,
                    event_type TEXT,
                    payload_json TEXT,
                    raw_frame TEXT,
                    received_at TEXT
                )
                """
            )

    def create_turn(
        self,
        turn: TurnView,
        model_id: str,
    ) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO turns (
                    id, session_id, query, model_id,
                    status, started_at, finished_at, error
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    turn.turn_id,
                    turn.session_id,
                    turn.query,
                    model_id,
                    turn.status,
                    datetime.now(timezone.utc).isoformat(),
                    None,
                    None,
                ),
            )

    def save_event(self, turn_id: str, event: DebugEvent) -> None:
        payload_json = json.dumps(event.payload, ensure_ascii=False, default=str)
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO events (
                    turn_id, seq, event_type, payload_json, raw_frame, received_at
                )
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    turn_id,
                    event.seq,
                    event.event_type,
                    payload_json,
                    event.raw_frame,
                    event.received_at.isoformat(),
                ),
            )

    def finish_turn(self, turn: TurnView) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE turns
                SET status = ?, finished_at = ?, error = ?
                WHERE id = ?
                """,
                (
                    turn.status,
                    datetime.now(timezone.utc).isoformat(),
                    turn.error,
                    turn.turn_id,
                ),
            )

    def list_turns(self, limit: int = 200) -> list[dict[str, Any]]:
        with self._connect() as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                """
                SELECT id, session_id, query, model_id, status, started_at, finished_at, error
                FROM turns
                ORDER BY COALESCE(started_at, '') DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        return [dict(row) for row in rows]

    def get_turn(self, turn_id: str) -> dict[str, Any] | None:
        with self._connect() as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute(
                """
                SELECT id, session_id, query, model_id, status, started_at, finished_at, error
                FROM turns
                WHERE id = ?
                """,
                (turn_id,),
            ).fetchone()
        return dict(row) if row else None

    def list_events(self, turn_id: str) -> list[DebugEvent]:
        with self._connect() as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                """
                SELECT seq, event_type, payload_json, raw_frame, received_at
                FROM events
                WHERE turn_id = ?
                ORDER BY seq ASC
                """,
                (turn_id,),
            ).fetchall()

        events: list[DebugEvent] = []
        for row in rows:
            try:
                payload = json.loads(row["payload_json"])
            except json.JSONDecodeError:
                payload = row["payload_json"]
            events.append(
                DebugEvent(
                    seq=int(row["seq"]),
                    event_type=row["event_type"] or "unknown",
                    payload=payload,
                    raw_frame=row["raw_frame"] or "",
                    received_at=_parse_datetime(row["received_at"]),
                )
            )
        return events

    def delete_turn(self, turn_id: str) -> None:
        with self._connect() as conn:
            conn.execute("DELETE FROM events WHERE turn_id = ?", (turn_id,))
            conn.execute("DELETE FROM turns WHERE id = ?", (turn_id,))

    def clear_turns(self) -> None:
        with self._connect() as conn:
            conn.execute("DELETE FROM events")
            conn.execute("DELETE FROM turns")


def _parse_datetime(value: str | None) -> datetime:
    if not value:
        return datetime.now(timezone.utc)
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return datetime.now(timezone.utc)
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed
