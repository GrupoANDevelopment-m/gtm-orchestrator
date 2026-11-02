"""
Cognitive memory — Layer 17 + auxiliary layers.

Three stores:
1. Episodic memory: concrete events (every API call, tool use, decision)
2. Semantic memory: facts about the world (leads, accounts, ICP profiles, deals)
3. Lessons memory: extracted rules from failures (capability-gap -> lesson)

Real persistence via SQLite. Designed for org-scoped multi-tenancy.
"""
from __future__ import annotations

import json
import sqlite3
import time
import uuid
from pathlib import Path
from typing import Any, Optional


DB_PATH = Path(__file__).resolve().parent.parent / "data" / "cognitive.sqlite3"


def _init():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS episodic (
            id TEXT PRIMARY KEY,
            org_id TEXT NOT NULL DEFAULT 'default',
            kind TEXT NOT NULL,           -- perception | hypothesis | evidence | action | outcome
            source TEXT,                  -- which layer produced it
            payload_json TEXT NOT NULL,
            session_id TEXT,
            created_at REAL NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_ep_org_time ON episodic(org_id, created_at DESC);
        CREATE INDEX IF NOT EXISTS idx_ep_session ON episodic(session_id);
        CREATE INDEX IF NOT EXISTS idx_ep_kind ON episodic(kind);

        CREATE TABLE IF NOT EXISTS semantic (
            id TEXT PRIMARY KEY,
            org_id TEXT NOT NULL DEFAULT 'default',
            entity TEXT NOT NULL,          -- 'lead', 'deal', 'icp', 'account'
            entity_id TEXT NOT NULL,
            facts_json TEXT NOT NULL,
            confidence REAL DEFAULT 1.0,
            source TEXT,
            created_at REAL NOT NULL,
            updated_at REAL NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_se_org_entity ON semantic(org_id, entity, entity_id);
        CREATE UNIQUE INDEX IF NOT EXISTS idx_se_unique ON semantic(org_id, entity, entity_id, source);

        CREATE TABLE IF NOT EXISTS lessons (
            id TEXT PRIMARY KEY,
            org_id TEXT NOT NULL DEFAULT 'default',
            kind TEXT NOT NULL,           -- capability_gap | strategy_fix | knowledge_synthesis
            trigger TEXT NOT NULL,        -- what caused the lesson
            lesson TEXT NOT NULL,
            times_observed INTEGER DEFAULT 1,
            last_observed REAL NOT NULL,
            first_observed REAL NOT NULL,
            confidence REAL DEFAULT 0.7,
            promoted_skill TEXT            -- name of skill created from this lesson
        );
        CREATE INDEX IF NOT EXISTS idx_le_kind ON lessons(org_id, kind);

        CREATE TABLE IF NOT EXISTS capabilities (
            id TEXT PRIMARY KEY,
            org_id TEXT NOT NULL DEFAULT 'default',
            name TEXT NOT NULL,            -- 'prospect_city', 'qualify_inbound', etc
            version INTEGER DEFAULT 1,
            description TEXT,
            origin TEXT,                   -- 'bundled', 'created', 'learned'
            invocations INTEGER DEFAULT 0,
            success_count INTEGER DEFAULT 0,
            failure_count INTEGER DEFAULT 0,
            last_invoked REAL,
            avg_duration_ms REAL,
            enabled INTEGER DEFAULT 1,
            created_at REAL NOT NULL
        );
        CREATE UNIQUE INDEX IF NOT EXISTS idx_cap_unique ON capabilities(org_id, name);
    """)
    conn.commit()
    conn.close()


_init()


# === EPISODIC ===

def episode(kind: str, source: str, payload: dict, *, org_id: str = "default",
            session_id: str | None = None) -> dict:
    eid = f"ep-{uuid.uuid4().hex[:12]}"
    conn = sqlite3.connect(DB_PATH)
    try:
        conn.execute(
            "INSERT INTO episodic (id, org_id, kind, source, payload_json, session_id, created_at) VALUES (?,?,?,?,?,?,?)",
            (eid, org_id, kind, source, json.dumps(payload, default=str), session_id, time.time()),
        )
        conn.commit()
    finally:
        conn.close()
    return {"id": eid, "kind": kind, "source": source, "payload": payload}


def recent_episodes(org_id: str = "default", *, limit: int = 50, kind: str | None = None,
                    session_id: str | None = None) -> list[dict]:
    conn = sqlite3.connect(DB_PATH)
    try:
        sql = "SELECT id, kind, source, payload_json, session_id, created_at FROM episodic WHERE org_id=?"
        params: list = [org_id]
        if kind:
            sql += " AND kind=?"; params.append(kind)
        if session_id:
            sql += " AND session_id=?"; params.append(session_id)
        sql += " ORDER BY created_at DESC LIMIT ?"
        params.append(limit)
        rows = conn.execute(sql, params).fetchall()
        out = []
        for r in rows:
            try:
                out.append({"id": r[0], "kind": r[1], "source": r[2],
                           "payload": json.loads(r[3]), "session_id": r[4], "created_at": r[5]})
            except Exception:
                continue
        return out
    finally:
        conn.close()


# === SEMANTIC ===

def remember(entity: str, entity_id: str, facts: dict, *, org_id: str = "default",
             source: str = "system", confidence: float = 1.0) -> dict:
    """Upsert semantic fact. Latest write wins per (org, entity, entity_id, source)."""
    now = time.time()
    conn = sqlite3.connect(DB_PATH)
    try:
        # Use INSERT OR REPLACE via composite key
        sid = f"sem-{uuid.uuid4().hex[:12]}"
        conn.execute(
            """INSERT INTO semantic (id, org_id, entity, entity_id, facts_json, confidence,
               source, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?)
               ON CONFLICT(org_id, entity, entity_id, source) DO UPDATE SET
                 facts_json=excluded.facts_json,
                 confidence=excluded.confidence,
                 updated_at=excluded.updated_at""",
            (sid, org_id, entity, entity_id, json.dumps(facts, default=str),
             confidence, source, now, now),
        )
        conn.commit()
    finally:
        conn.close()
    return {"entity": entity, "entity_id": entity_id, "facts": facts, "source": source}


def recall(entity: str, entity_id: str, *, org_id: str = "default") -> dict:
    """Aggregate all facts about an entity from all sources."""
    conn = sqlite3.connect(DB_PATH)
    try:
        rows = conn.execute(
            "SELECT source, facts_json, confidence, updated_at FROM semantic WHERE org_id=? AND entity=? AND entity_id=?",
            (org_id, entity, entity_id),
        ).fetchall()
        merged: dict = {}
        for source, facts_json, confidence, updated_at in rows:
            try:
                facts = json.loads(facts_json)
            except Exception:
                continue
            for k, v in facts.items():
                merged.setdefault(k, []).append({"value": v, "source": source,
                                                  "confidence": confidence, "updated_at": updated_at})
        return {"entity": entity, "entity_id": entity_id, "facts": merged,
                "source_count": len(rows)}
    finally:
        conn.close()


# === LESSONS (capability gap -> lesson) ===

LESSONS_PATTERN_THRESHOLD = 3  # promote when same lesson seen N times


def observe_lesson(trigger: str, lesson: str, *, org_id: str = "default",
                   kind: str = "capability_gap") -> dict:
    """Record a lesson. Increments count if already known, else creates new."""
    conn = sqlite3.connect(DB_PATH)
    try:
        existing = conn.execute(
            "SELECT id, times_observed FROM lessons WHERE org_id=? AND trigger=? AND lesson=?",
            (org_id, trigger, lesson),
        ).fetchone()
        if existing:
            conn.execute(
                "UPDATE lessons SET times_observed = times_observed + 1, last_observed = ? WHERE id=?",
                (time.time(), existing[0]),
            )
            new_count = existing[1] + 1
            lid = existing[0]
        else:
            lid = f"les-{uuid.uuid4().hex[:12]}"
            conn.execute(
                """INSERT INTO lessons (id, org_id, kind, trigger, lesson, times_observed,
                   last_observed, first_observed) VALUES (?,?,?,?,?,?,?,?)""",
                (lid, org_id, kind, trigger, lesson, 1, time.time(), time.time()),
            )
            new_count = 1
        conn.commit()
        return {"id": lid, "trigger": trigger, "lesson": lesson,
                "times_observed": new_count, "promote": new_count >= LESSONS_PATTERN_THRESHOLD}
    finally:
        conn.close()


def list_lessons(org_id: str = "default", *, limit: int = 50, kind: str | None = None) -> list[dict]:
    conn = sqlite3.connect(DB_PATH)
    try:
        sql = "SELECT id, kind, trigger, lesson, times_observed, last_observed, first_observed, promoted_skill FROM lessons WHERE org_id=?"
        params: list = [org_id]
        if kind:
            sql += " AND kind=?"; params.append(kind)
        sql += " ORDER BY times_observed DESC, last_observed DESC LIMIT ?"
        params.append(limit)
        rows = conn.execute(sql, params).fetchall()
        return [{"id": r[0], "kind": r[1], "trigger": r[2], "lesson": r[3],
                 "times_observed": r[4], "last_observed": r[5], "first_observed": r[6],
                 "promoted_skill": r[7]} for r in rows]
    finally:
        conn.close()


def mark_promoted(lesson_id: str, skill_name: str) -> None:
    conn = sqlite3.connect(DB_PATH)
    try:
        conn.execute("UPDATE lessons SET promoted_skill=? WHERE id=?", (skill_name, lesson_id))
        conn.commit()
    finally:
        conn.close()


# === CAPABILITY INDEX ===

def register_capability(name: str, *, description: str = "", origin: str = "bundled",
                         org_id: str = "default") -> dict:
    conn = sqlite3.connect(DB_PATH)
    try:
        conn.execute(
            """INSERT INTO capabilities (id, org_id, name, description, origin, created_at)
               VALUES (?,?,?,?,?,?)
               ON CONFLICT(org_id, name) DO UPDATE SET description=excluded.description""",
            (f"cap-{uuid.uuid4().hex[:12]}", org_id, name, description, origin, time.time()),
        )
        conn.commit()
    finally:
        conn.close()
    return {"name": name, "origin": origin, "description": description}


def record_capability_invocation(name: str, *, success: bool, duration_ms: int,
                                  org_id: str = "default") -> None:
    conn = sqlite3.connect(DB_PATH)
    try:
        # Update aggregate stats
        conn.execute(
            """UPDATE capabilities SET invocations = invocations + 1,
               success_count = success_count + ?,
               failure_count = failure_count + ?,
               last_invoked = ?,
               avg_duration_ms = (avg_duration_ms * invocations + ?) / (invocations + 1)
               WHERE org_id=? AND name=?""",
            (1 if success else 0, 0 if success else 1, time.time(), duration_ms, org_id, name),
        )
        conn.commit()
    finally:
        conn.close()


def list_capabilities(org_id: str = "default") -> list[dict]:
    conn = sqlite3.connect(DB_PATH)
    try:
        rows = conn.execute(
            "SELECT name, version, description, origin, invocations, success_count, failure_count, last_invoked, avg_duration_ms, enabled FROM capabilities WHERE org_id=? ORDER BY invocations DESC",
            (org_id,),
        ).fetchall()
        return [{"name": r[0], "version": r[1], "description": r[2], "origin": r[3],
                 "invocations": r[4], "success_count": r[5], "failure_count": r[6],
                 "success_rate": (r[5] / r[4]) if r[4] else 0,
                 "last_invoked": r[7], "avg_duration_ms": r[8] or 0,
                 "enabled": bool(r[9])} for r in rows]
    finally:
        conn.close()
