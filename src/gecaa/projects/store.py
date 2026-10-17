# SPDX-License-Identifier: Apache-2.0
"""Projects as JSON documents in SQLite (stdlib). One row per project, with a change log."""

from __future__ import annotations

import json
import os
import sqlite3
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path

_LOCK = threading.Lock()


def _db_path() -> Path:
    p = Path(os.environ.get("GECAA_PROJECTS_DB") or Path(__file__).resolve().parents[3] / "data" / "cache" / "projects.sqlite")
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


def _conn() -> sqlite3.Connection:
    c = sqlite3.connect(_db_path())
    c.execute("CREATE TABLE IF NOT EXISTS projects (id TEXT PRIMARY KEY, doc TEXT NOT NULL, updated TEXT NOT NULL)")
    return c


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def create(doc: dict) -> dict:
    doc = {**doc, "id": uuid.uuid4().hex[:12], "created": now(), "version": 1}
    doc.setdefault("log", []).append({"at": doc["created"], "text": "Projekt angelegt"})
    with _LOCK, _conn() as c:
        c.execute("INSERT INTO projects VALUES (?, ?, ?)", (doc["id"], json.dumps(doc, ensure_ascii=False), doc["created"]))
    return doc


def get(pid: str) -> dict | None:
    with _conn() as c:
        row = c.execute("SELECT doc FROM projects WHERE id = ?", (pid,)).fetchone()
    return json.loads(row[0]) if row else None


def list_all() -> list[dict]:
    with _conn() as c:
        rows = c.execute("SELECT doc FROM projects ORDER BY updated DESC LIMIT 200").fetchall()
    out = []
    for (d,) in rows:
        p = json.loads(d)
        row = {k: p.get(k) for k in ("id", "name", "applicant", "status", "updated", "created")}
        tasks = [t for ph in p.get("phases") or [] for t in ph.get("tasks") or []]
        row["share"] = round(sum(1 for t in tasks if t.get("done")) / len(tasks), 3) if tasks else 0.0
        out.append(row)
    return out


class Conflict(Exception):
    pass


def save(doc: dict, expected_version: int | None, note: str | None = None) -> dict:
    """Optimistic locking: refuses when someone else saved in between."""
    with _LOCK, _conn() as c:
        row = c.execute("SELECT doc FROM projects WHERE id = ?", (doc["id"],)).fetchone()
        if not row:
            raise KeyError(doc["id"])
        cur = json.loads(row[0])
        if expected_version is not None and cur.get("version") != expected_version:
            raise Conflict(cur.get("version"))
        doc["version"] = cur.get("version", 0) + 1
        doc["created"] = cur.get("created")
        doc["updated"] = now()
        log = cur.get("log", [])
        if note:
            log.append({"at": doc["updated"], "text": note})
        doc["log"] = log[-300:]
        c.execute("UPDATE projects SET doc = ?, updated = ? WHERE id = ?",
                  (json.dumps(doc, ensure_ascii=False), doc["updated"], doc["id"]))
    return doc


def delete(pid: str) -> bool:
    with _LOCK, _conn() as c:
        return c.execute("DELETE FROM projects WHERE id = ?", (pid,)).rowcount > 0
