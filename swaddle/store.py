"""SQLite event store for one household: babies, caregivers and care events."""

from __future__ import annotations

import json
import os
import sqlite3
import threading
import time
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable
from zoneinfo import ZoneInfo

EVENT_KINDS = ("feed", "diaper", "sleep", "temperature", "medicine", "note")

SCHEMA = """
CREATE TABLE IF NOT EXISTS baby (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    birth_date TEXT NOT NULL,
    tz TEXT NOT NULL,
    pronoun TEXT NOT NULL DEFAULT 'she'
);
CREATE TABLE IF NOT EXISTS event (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    baby_id INTEGER NOT NULL REFERENCES baby(id),
    kind TEXT NOT NULL,
    start_ts REAL NOT NULL,
    end_ts REAL,
    data TEXT NOT NULL DEFAULT '{}',
    caregiver TEXT,
    deleted INTEGER NOT NULL DEFAULT 0,
    created_ts REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS event_baby_kind_start ON event(baby_id, kind, start_ts);
"""


class Clock:
    """Wall clock with an optional demo anchor.

    SWADDLE_DEMO_TIME="2026-10-22T03:12" (local to the baby's timezone) makes the
    server believe it is that moment when it starts, then lets time run normally.
    This is how the demo video is shot at "3:12 AM" without waiting for 3 AM.
    """

    def __init__(self, demo_time: str | None = None, tz: str = "UTC", frozen: bool = False):
        self._offset = 0.0
        self._frozen_at: float | None = None
        if demo_time:
            anchor = datetime.fromisoformat(demo_time)
            if anchor.tzinfo is None:
                anchor = anchor.replace(tzinfo=ZoneInfo(tz))
            self._offset = anchor.timestamp() - time.time()
            if frozen:
                self._frozen_at = anchor.timestamp()

    def now(self) -> float:
        if self._frozen_at is not None:
            return self._frozen_at + self._offset_frozen
        return time.time() + self._offset

    _offset_frozen = 0.0

    def shift(self, seconds: float) -> None:
        if self._frozen_at is not None:
            self._offset_frozen += seconds
        else:
            self._offset += seconds


@dataclass
class Baby:
    id: int
    name: str
    birth_date: date
    tz: str
    pronoun: str = "she"

    @property
    def zone(self) -> ZoneInfo:
        return ZoneInfo(self.tz)

    def age_weeks(self, now_ts: float) -> float:
        born = datetime.combine(self.birth_date, datetime.min.time(), tzinfo=self.zone)
        return (now_ts - born.timestamp()) / (7 * 86400)

    def age_days(self, now_ts: float) -> float:
        return self.age_weeks(now_ts) * 7

    @property
    def obj(self) -> str:
        return {"she": "her", "he": "him", "they": "them"}.get(self.pronoun, "them")

    @property
    def poss(self) -> str:
        return {"she": "her", "he": "his", "they": "their"}.get(self.pronoun, "their")


@dataclass
class Event:
    id: int
    baby_id: int
    kind: str
    start_ts: float
    end_ts: float | None
    data: dict[str, Any] = field(default_factory=dict)
    caregiver: str | None = None

    @property
    def duration_min(self) -> float | None:
        if self.end_ts is None:
            return None
        return (self.end_ts - self.start_ts) / 60


class Store:
    def __init__(self, path: str | os.PathLike[str] = ":memory:", clock: Clock | None = None):
        self.path = str(path)
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(self.path, check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        self._lock = threading.RLock()
        with self._lock:
            self._db.executescript(SCHEMA)
        self.clock = clock or Clock()

    # ---- babies -------------------------------------------------------
    def add_baby(self, name: str, birth_date: date, tz: str, pronoun: str = "she") -> Baby:
        with self._lock, self._db:
            cur = self._db.execute(
                "INSERT INTO baby(name, birth_date, tz, pronoun) VALUES (?,?,?,?)",
                (name, birth_date.isoformat(), tz, pronoun),
            )
        return Baby(int(cur.lastrowid), name, birth_date, tz, pronoun)

    def babies(self) -> list[Baby]:
        with self._lock:
            rows = self._db.execute("SELECT * FROM baby ORDER BY id").fetchall()
        return [Baby(r["id"], r["name"], date.fromisoformat(r["birth_date"]), r["tz"], r["pronoun"]) for r in rows]

    def baby(self, name: str | None = None) -> Baby:
        babies = self.babies()
        if not babies:
            raise LookupError("No baby profile yet. Add one with setup_baby.")
        if name:
            for b in babies:
                if b.name.lower() == name.strip().lower():
                    return b
            raise LookupError(f"I don't have a baby named {name}. I know: {', '.join(b.name for b in babies)}.")
        return babies[0]

    # ---- events -------------------------------------------------------
    def add_event(
        self,
        baby: Baby,
        kind: str,
        start_ts: float,
        end_ts: float | None = None,
        data: dict[str, Any] | None = None,
        caregiver: str | None = None,
    ) -> Event:
        if kind not in EVENT_KINDS:
            raise ValueError(f"unknown event kind {kind}")
        payload = json.dumps(data or {})
        with self._lock, self._db:
            cur = self._db.execute(
                "INSERT INTO event(baby_id, kind, start_ts, end_ts, data, caregiver, created_ts) VALUES (?,?,?,?,?,?,?)",
                (baby.id, kind, start_ts, end_ts, payload, caregiver, self.clock.now()),
            )
        return Event(int(cur.lastrowid), baby.id, kind, start_ts, end_ts, data or {}, caregiver)

    def update_event(self, event: Event) -> None:
        with self._lock, self._db:
            self._db.execute(
                "UPDATE event SET start_ts=?, end_ts=?, data=?, caregiver=? WHERE id=?",
                (event.start_ts, event.end_ts, json.dumps(event.data), event.caregiver, event.id),
            )

    def delete_event(self, event_id: int) -> None:
        with self._lock, self._db:
            self._db.execute("UPDATE event SET deleted=1 WHERE id=?", (event_id,))

    def _row(self, r: sqlite3.Row) -> Event:
        return Event(r["id"], r["baby_id"], r["kind"], r["start_ts"], r["end_ts"], json.loads(r["data"]), r["caregiver"])

    def events(
        self,
        baby: Baby,
        kinds: Iterable[str] | None = None,
        since_ts: float | None = None,
        until_ts: float | None = None,
    ) -> list[Event]:
        q = "SELECT * FROM event WHERE baby_id=? AND deleted=0"
        args: list[Any] = [baby.id]
        if kinds:
            kinds = list(kinds)
            q += f" AND kind IN ({','.join('?' * len(kinds))})"
            args += kinds
        if since_ts is not None:
            q += " AND COALESCE(end_ts, start_ts) >= ?"
            args.append(since_ts)
        if until_ts is not None:
            q += " AND start_ts <= ?"
            args.append(until_ts)
        q += " ORDER BY start_ts"
        with self._lock:
            return [self._row(r) for r in self._db.execute(q, args).fetchall()]

    def last(self, baby: Baby, kind: str) -> Event | None:
        with self._lock:
            r = self._db.execute(
                "SELECT * FROM event WHERE baby_id=? AND kind=? AND deleted=0 ORDER BY start_ts DESC LIMIT 1",
                (baby.id, kind),
            ).fetchone()
        return self._row(r) if r else None

    def last_created(self, baby: Baby) -> Event | None:
        with self._lock:
            r = self._db.execute(
                "SELECT * FROM event WHERE baby_id=? AND deleted=0 ORDER BY created_ts DESC, id DESC LIMIT 1",
                (baby.id,),
            ).fetchone()
        return self._row(r) if r else None

    def open_sleep(self, baby: Baby) -> Event | None:
        with self._lock:
            r = self._db.execute(
                "SELECT * FROM event WHERE baby_id=? AND kind='sleep' AND end_ts IS NULL AND deleted=0 ORDER BY start_ts DESC LIMIT 1",
                (baby.id,),
            ).fetchone()
        return self._row(r) if r else None

    def clear(self) -> None:
        with self._lock, self._db:
            self._db.execute("DELETE FROM event")
            self._db.execute("DELETE FROM baby")


def utc(ts: float) -> datetime:
    return datetime.fromtimestamp(ts, tz=timezone.utc)


def local(ts: float, baby: Baby) -> datetime:
    return datetime.fromtimestamp(ts, tz=baby.zone)


def hours_ago(clock: Clock, h: float) -> float:
    return clock.now() - h * 3600


__all__ = ["Baby", "Clock", "Event", "Store", "EVENT_KINDS", "local", "utc", "hours_ago", "timedelta"]
