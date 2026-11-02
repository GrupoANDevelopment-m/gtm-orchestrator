"""
Goal Engine (Layer 3) — define, prioritize, evaluate.

Goals are the loop's "intent". Each goal has:
- description (what we're trying to achieve)
- priority (0..1) — internal urgency weight
- urgency (deadline proximity)
- impact (expected value)
- status (active|paused|completed|abandoned)
- dependencies (other goal ids that must finish first)

Priority = base_weight * urgency_factor * impact_factor / (1 + cost_factor)
"""
from __future__ import annotations

import sqlite3
import time
import uuid
from pathlib import Path


DB_PATH = Path(__file__).resolve().parent.parent / "data" / "goals.sqlite3"


def _init():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS goals (
            id TEXT PRIMARY KEY,
            org_id TEXT NOT NULL DEFAULT 'default',
            session_id TEXT,
            description TEXT NOT NULL,
            priority REAL NOT NULL,
            urgency REAL DEFAULT 0.5,
            impact REAL DEFAULT 0.5,
            cost REAL DEFAULT 0.1,
            deadline REAL,
            status TEXT DEFAULT 'active',
            depends_on TEXT,
            created_at REAL NOT NULL,
            completed_at REAL
        );
        CREATE INDEX IF NOT EXISTS idx_g_status ON goals(org_id, status, priority DESC);
    """)
    conn.commit()
    conn.close()


_init()


def create_goal(description: str, *, priority: float = 0.5, urgency: float = 0.5,
                impact: float = 0.5, cost: float = 0.1, deadline: float | None = None,
                session_id: str | None = None, org_id: str = "default",
                depends_on: list[str] | None = None) -> dict:
    gid = f"goal-{uuid.uuid4().hex[:12]}"
    final_priority = priority * (0.5 + urgency) * (0.5 + impact) / (1 + cost)
    conn = sqlite3.connect(DB_PATH)
    try:
        conn.execute(
            """INSERT INTO goals (id, org_id, session_id, description, priority, urgency,
               impact, cost, deadline, status, depends_on, created_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
            (gid, org_id, session_id, description, final_priority, urgency, impact, cost,
             deadline, "active",
             ",".join(depends_on) if depends_on else None, time.time()),
        )
        conn.commit()
    finally:
        conn.close()
    return {"id": gid, "description": description, "priority": final_priority,
            "urgency": urgency, "impact": impact, "cost": cost, "status": "active"}


def top_goal(org_id: str = "default", *, session_id: str | None = None) -> dict | None:
    conn = sqlite3.connect(DB_PATH)
    try:
        sql = """SELECT id, description, priority, urgency, impact, status
                 FROM goals WHERE org_id=? AND status='active'"""
        params: list = [org_id]
        if session_id:
            sql += " AND (session_id=? OR session_id IS NULL)"
            params.append(session_id)
        sql += " ORDER BY priority DESC LIMIT 1"
        row = conn.execute(sql, params).fetchone()
        if not row:
            return None
        return {"id": row[0], "description": row[1], "priority": row[2],
                "urgency": row[3], "impact": row[4], "status": row[5]}
    finally:
        conn.close()


def list_goals(org_id: str = "default", *, status: str | None = None) -> list[dict]:
    conn = sqlite3.connect(DB_PATH)
    try:
        sql = "SELECT id, description, priority, status, created_at, completed_at FROM goals WHERE org_id=?"
        params: list = [org_id]
        if status:
            sql += " AND status=?"; params.append(status)
        sql += " ORDER BY priority DESC LIMIT 50"
        return [{"id": r[0], "description": r[1], "priority": r[2], "status": r[3],
                 "created_at": r[4], "completed_at": r[5]} for r in conn.execute(sql, params).fetchall()]
    finally:
        conn.close()


def complete_goal(goal_id: str) -> dict:
    conn = sqlite3.connect(DB_PATH)
    try:
        conn.execute("UPDATE goals SET status='completed', completed_at=? WHERE id=?",
                     (time.time(), goal_id))
        conn.commit()
    finally:
        conn.close()
    return {"id": goal_id, "status": "completed"}


def abandon_goal(goal_id: str) -> dict:
    conn = sqlite3.connect(DB_PATH)
    try:
        conn.execute("UPDATE goals SET status='abandoned' WHERE id=?", (goal_id,))
        conn.commit()
    finally:
        conn.close()
    return {"id": goal_id, "status": "abandoned"}


SOURCE = "Layer 3 GOAL ENGINE — define, prioritize (priority * urgency * impact / cost), rank, abandon"
