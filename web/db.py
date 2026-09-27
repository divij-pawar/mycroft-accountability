"""
C-03 — Persistent Store
SQLite backend for the accountability layer.

Design decisions:
- Append-only enforcement via BEFORE UPDATE / BEFORE DELETE triggers on `runs`.
  The clear_all() helper drops and recreates tables (admin/test use only).
- Ticker is stored as a generated column (extracted at insert time) so queries
  never have to parse JSON on the fly.
- 90-day TTL: purge_old_runs() deletes rows older than RETENTION_DAYS; called
  automatically on startup and on every write.
- reviewer_flags is a separate table; RunRecord rows are never mutated.
- gate_decisions (BG) is append-only too: the human decisions that clear a gated
  compare run. A run's gate state is computed at read time from its rows plus these
  decisions (validation/gate.py), so the run record itself never changes.
"""

from __future__ import annotations

import json
import re
import sqlite3
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any


def _extract_ticker(subject: str) -> str:
    """Mirror the _ticker() logic in server.py: first word, alphanumeric only, upper, ≤10 chars."""
    cleaned = re.sub(r"[^a-zA-Z0-9]", "", (subject.split() or ["CHAT"])[0])
    return (cleaned.upper()[:10]) or "CHAT"

DB_PATH        = Path(__file__).parent / "data" / "accountability.db"
RETENTION_DAYS = 90


# ── Schema ─────────────────────────────────────────────────────────────────────

_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS runs (
    run_id       TEXT PRIMARY KEY,
    ticker       TEXT NOT NULL,
    scope        TEXT NOT NULL,
    status       TEXT NOT NULL,
    initiated_at TEXT NOT NULL,
    completed_at TEXT,
    confidence   REAL,
    payload_json TEXT NOT NULL,
    created_at   TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
);

CREATE INDEX IF NOT EXISTS idx_runs_ticker       ON runs(ticker);
CREATE INDEX IF NOT EXISTS idx_runs_initiated_at ON runs(initiated_at);
CREATE INDEX IF NOT EXISTS idx_runs_created_at   ON runs(created_at);

-- Append-only enforcement
CREATE TRIGGER IF NOT EXISTS runs_no_update
BEFORE UPDATE ON runs
BEGIN
    SELECT RAISE(ABORT, 'runs table is append-only: updates are forbidden');
END;

CREATE TRIGGER IF NOT EXISTS runs_no_delete
BEFORE DELETE ON runs
BEGIN
    SELECT RAISE(ABORT, 'runs table is append-only: deletes are forbidden');
END;

CREATE TABLE IF NOT EXISTS sessions (
    session_id   TEXT PRIMARY KEY,
    run_id       TEXT NOT NULL,
    ticker       TEXT NOT NULL,
    session_json TEXT NOT NULL,
    created_at   TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    FOREIGN KEY (run_id) REFERENCES runs(run_id)
);

CREATE INDEX IF NOT EXISTS idx_sessions_ticker ON sessions(ticker);

CREATE TABLE IF NOT EXISTS reviewer_flags (
    flag_id       TEXT PRIMARY KEY,
    run_id        TEXT NOT NULL,
    flag_type     TEXT NOT NULL CHECK(flag_type IN ('Hallucinated','Incorrect','Other')),
    reviewer_note TEXT,
    flagged_at    TEXT NOT NULL,
    FOREIGN KEY (run_id) REFERENCES runs(run_id)
);

CREATE INDEX IF NOT EXISTS idx_flags_run_id ON reviewer_flags(run_id);

-- BG: a named human's decision on a gated compare run (validation/gate.py).
-- Append-only like runs: a later decision on the same figure supersedes an
-- earlier one, and both rows stay. The CHECKs repeat gate.py's minimums so a
-- write that skips the route's validation still can't store an empty decision.
CREATE TABLE IF NOT EXISTS gate_decisions (
    decision_id  TEXT PRIMARY KEY,
    run_id       TEXT NOT NULL,
    decided_by   TEXT NOT NULL CHECK(length(trim(decided_by)) >= 2),
    decision     TEXT NOT NULL CHECK(decision IN
                     ('accept_a','accept_b','both_wrong','not_a_conflict','override_value',
                      'confirmed_error','set_grade')),
    final_value  REAL,
    final_grade  TEXT,
    rationale    TEXT NOT NULL CHECK(length(trim(rationale)) >= 20),
    cited_items  TEXT NOT NULL,
    decided_at   TEXT NOT NULL,
    FOREIGN KEY (run_id) REFERENCES runs(run_id)
);

CREATE INDEX IF NOT EXISTS idx_decisions_run_id ON gate_decisions(run_id);

CREATE TRIGGER IF NOT EXISTS gate_decisions_no_update
BEFORE UPDATE ON gate_decisions
BEGIN
    SELECT RAISE(ABORT, 'gate_decisions is append-only: updates are forbidden');
END;

CREATE TRIGGER IF NOT EXISTS gate_decisions_no_delete
BEFORE DELETE ON gate_decisions
BEGIN
    SELECT RAISE(ABORT, 'gate_decisions is append-only: deletes are forbidden');
END;
"""


# ── Connection factory ─────────────────────────────────────────────────────────

def _connect() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_db() -> None:
    """Create tables and indexes if they don't exist. Safe to call on every startup."""
    with _connect() as conn:
        conn.executescript(_SCHEMA_SQL)
        _migrate_gate_decisions(conn)


def _migrate_gate_decisions(conn: sqlite3.Connection) -> None:
    """
    B3 (2026-09-25) added the decision 'confirmed_error'. A gate_decisions table
    created before that (BG, earlier the same day) carries a CHECK that refuses it,
    and SQLite can't alter a CHECK in place, so the table is rebuilt: renamed, the
    new one created from _SCHEMA_SQL, every row copied verbatim, the old one
    dropped. No decision is changed or lost; the append-only triggers are
    recreated with the new table. Runs once — a table that already accepts the new
    value is left alone.
    """
    row = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'gate_decisions'"
    ).fetchone()
    # B5 added 'set_grade' and the final_grade column; a table missing either is rebuilt.
    if row is None or ("set_grade" in row["sql"] and "final_grade" in row["sql"]):
        return
    before = conn.execute("SELECT COUNT(*) FROM gate_decisions").fetchone()[0]
    conn.executescript(
        "BEGIN;"
        "DROP TRIGGER IF EXISTS gate_decisions_no_update;"
        "DROP TRIGGER IF EXISTS gate_decisions_no_delete;"
        "DROP INDEX IF EXISTS idx_decisions_run_id;"
        "ALTER TABLE gate_decisions RENAME TO gate_decisions_pre_b3;"
        "COMMIT;"
    )
    conn.executescript(_SCHEMA_SQL)
    conn.execute(
        "INSERT INTO gate_decisions (decision_id, run_id, decided_by, decision, final_value,"
        " rationale, cited_items, decided_at)"
        " SELECT decision_id, run_id, decided_by, decision, final_value, rationale, cited_items,"
        " decided_at FROM gate_decisions_pre_b3 ORDER BY rowid"
    )  # final_grade is new, so NULL for every copied row
    after = conn.execute("SELECT COUNT(*) FROM gate_decisions").fetchone()[0]
    if after != before:
        raise RuntimeError(f"gate_decisions migration copied {after} of {before} rows; not dropping the original")
    conn.execute("DROP TABLE gate_decisions_pre_b3")
    conn.commit()


# ── TTL purge ──────────────────────────────────────────────────────────────────

def purge_old_runs(retention_days: int = RETENTION_DAYS) -> int:
    """
    Remove runs older than `retention_days`.  Must bypass the append-only trigger
    by using a separate connection with the trigger temporarily disabled.
    Returns the number of rows deleted.
    """
    cutoff = (
        datetime.now(timezone.utc) - timedelta(days=retention_days)
    ).strftime("%Y-%m-%dT%H:%M:%SZ")

    with _connect() as conn:
        # Disable the no-delete trigger for this connection only
        conn.execute("DROP TRIGGER IF EXISTS runs_no_delete_ttl_bypass")
        try:
            # Find run_ids that are expired
            rows = conn.execute(
                "SELECT run_id FROM runs WHERE created_at < ?", (cutoff,)
            ).fetchall()
            expired_ids = [r["run_id"] for r in rows]

            if not expired_ids:
                return 0

            placeholders = ",".join("?" * len(expired_ids))
            # Delete child rows first (FK constraint). gate_decisions is append-only
            # too, so its no-delete trigger is lifted for the purge and put back below.
            conn.execute("DROP TRIGGER IF EXISTS gate_decisions_no_delete")
            conn.execute(
                f"DELETE FROM gate_decisions WHERE run_id IN ({placeholders})",
                expired_ids,
            )
            conn.execute(
                f"DELETE FROM reviewer_flags WHERE run_id IN ({placeholders})",
                expired_ids,
            )
            conn.execute(
                f"DELETE FROM sessions WHERE run_id IN ({placeholders})",
                expired_ids,
            )
            # Temporarily drop the no-delete trigger so TTL purge is allowed
            conn.execute("DROP TRIGGER IF EXISTS runs_no_delete")
            conn.execute(
                f"DELETE FROM runs WHERE run_id IN ({placeholders})",
                expired_ids,
            )
            conn.commit()
            # Recreate both no-delete triggers (the schema is all IF NOT EXISTS)
            conn.executescript(_SCHEMA_SQL)
            return len(expired_ids)
        except Exception:
            conn.rollback()
            conn.executescript(_SCHEMA_SQL)  # never leave a no-delete trigger dropped
            raise


# ── Runs CRUD ──────────────────────────────────────────────────────────────────

def insert_run(payload: dict) -> None:
    """Append a run record. payload must contain run_id, scope, status, initiated_at."""
    with _connect() as conn:
        conn.execute(
            """
            INSERT INTO runs
                (run_id, ticker, scope, status, initiated_at, completed_at, confidence, payload_json)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                payload["run_id"],
                _extract_ticker(payload.get("subject", "")),
                payload.get("scope", "auditor"),
                "HALTED" if payload.get("halted") else "COMPLETE",
                payload.get("session", {}).get("initiated_at") if payload.get("session") else None,
                payload.get("session", {}).get("completed_at") if payload.get("session") else None,
                payload.get("confidence_score"),
                json.dumps(payload, default=str),
            ),
        )


def get_runs(
    ticker: str | None = None,
    from_dt: str | None = None,
    to_dt: str | None = None,
    limit: int = 50,
) -> list[dict]:
    """
    C-04 query surface: filter by ticker and/or date range.
    Dates are ISO-8601 strings (e.g. '2025-01-01' or '2025-01-01T00:00:00Z').
    """
    clauses: list[str] = []
    params:  list[Any] = []

    if ticker:
        clauses.append("ticker = ?")
        params.append(ticker.upper())
    if from_dt:
        clauses.append("initiated_at >= ?")
        params.append(from_dt)
    if to_dt:
        clauses.append("initiated_at <= ?")
        params.append(to_dt)

    where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
    params.append(limit)

    with _connect() as conn:
        rows = conn.execute(
            f"SELECT payload_json FROM runs {where} ORDER BY created_at DESC LIMIT ?",
            params,
        ).fetchall()

    return [json.loads(r["payload_json"]) for r in rows]


def get_run(run_id: str) -> dict | None:
    with _connect() as conn:
        row = conn.execute(
            "SELECT payload_json FROM runs WHERE run_id = ?", (run_id,)
        ).fetchone()
    return json.loads(row["payload_json"]) if row else None


def get_drift(ticker: str) -> list[dict]:
    """
    C-04 drift surface: confidence scores over time for a ticker.
    Returns lightweight records — no full payload.
    """
    with _connect() as conn:
        rows = conn.execute(
            """
            SELECT run_id, initiated_at, confidence, status
            FROM   runs
            WHERE  ticker = ?
            ORDER  BY initiated_at ASC
            """,
            (ticker.upper(),),
        ).fetchall()
    return [dict(r) for r in rows]


# ── Sessions CRUD ──────────────────────────────────────────────────────────────

def insert_session(run_id: str, ticker: str, session: dict) -> None:
    with _connect() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO sessions (session_id, run_id, ticker, session_json) VALUES (?, ?, ?, ?)",
            (run_id, run_id, ticker.upper(), json.dumps(session, default=str)),
        )


def get_sessions(limit: int = 50) -> list[dict]:
    with _connect() as conn:
        rows = conn.execute(
            "SELECT session_json FROM sessions ORDER BY created_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
    return [json.loads(r["session_json"]) for r in rows]


def get_session(session_id: str) -> dict | None:
    with _connect() as conn:
        row = conn.execute(
            "SELECT session_json FROM sessions WHERE session_id = ?", (session_id,)
        ).fetchone()
    return json.loads(row["session_json"]) if row else None


# ── Reviewer flags (UN-05) ─────────────────────────────────────────────────────

def insert_flag(run_id: str, flag_type: str, reviewer_note: str | None) -> dict:
    flag_id    = str(uuid.uuid4())
    flagged_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    with _connect() as conn:
        conn.execute(
            "INSERT INTO reviewer_flags (flag_id, run_id, flag_type, reviewer_note, flagged_at) VALUES (?,?,?,?,?)",
            (flag_id, run_id, flag_type, reviewer_note, flagged_at),
        )
    return {"flag_id": flag_id, "run_id": run_id, "flag_type": flag_type,
            "reviewer_note": reviewer_note, "flagged_at": flagged_at}


def get_flags(run_id: str) -> list[dict]:
    with _connect() as conn:
        rows = conn.execute(
            "SELECT flag_id, run_id, flag_type, reviewer_note, flagged_at "
            "FROM reviewer_flags WHERE run_id = ? ORDER BY flagged_at DESC",
            (run_id,),
        ).fetchall()
    return [dict(r) for r in rows]


# ── Gate decisions (BG) ────────────────────────────────────────────────────────

def insert_decision(run_id: str, fields: dict) -> dict:
    """Append one decision. `fields` must already have passed gate.validate_decision."""
    decision_id = str(uuid.uuid4())
    # Microseconds: two decisions in the same second must still order correctly.
    decided_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
    with _connect() as conn:
        conn.execute(
            "INSERT INTO gate_decisions (decision_id, run_id, decided_by, decision, final_value, final_grade,"
            " rationale, cited_items, decided_at) VALUES (?,?,?,?,?,?,?,?,?)",
            (decision_id, run_id, fields["decided_by"], fields["decision"], fields.get("final_value"),
             fields.get("final_grade"), fields["rationale"], json.dumps(fields["cited_items"]), decided_at),
        )
    return {"decision_id": decision_id, "run_id": run_id, "decided_at": decided_at, **fields}


def _decision_row(r: sqlite3.Row) -> dict:
    d = dict(r)
    d["cited_items"] = json.loads(d["cited_items"])
    return d


def get_decisions(run_id: str) -> list[dict]:
    """Oldest first, in insertion order — the order gate_state() replays them in."""
    with _connect() as conn:
        rows = conn.execute(
            "SELECT decision_id, run_id, decided_by, decision, final_value, final_grade, rationale, cited_items,"
            " decided_at FROM gate_decisions WHERE run_id = ? ORDER BY rowid ASC",
            (run_id,),
        ).fetchall()
    return [_decision_row(r) for r in rows]


def get_decisions_for(run_ids: list[str]) -> dict[str, list[dict]]:
    """get_decisions for many runs in one query (the History list)."""
    out: dict[str, list[dict]] = {rid: [] for rid in run_ids}
    if not run_ids:
        return out
    placeholders = ",".join("?" * len(run_ids))
    with _connect() as conn:
        rows = conn.execute(
            "SELECT decision_id, run_id, decided_by, decision, final_value, final_grade, rationale, cited_items,"
            f" decided_at FROM gate_decisions WHERE run_id IN ({placeholders}) ORDER BY rowid ASC",
            run_ids,
        ).fetchall()
    for r in rows:
        out[r["run_id"]].append(_decision_row(r))
    return out


# ── Admin / test helpers ───────────────────────────────────────────────────────

def clear_all() -> None:
    """
    Drop and recreate all tables.  Admin / test use only — bypasses append-only
    constraint by dropping the table entirely rather than deleting rows.
    """
    with _connect() as conn:
        conn.executescript(
            "DROP TABLE IF EXISTS gate_decisions;"
            "DROP TABLE IF EXISTS gate_decisions_pre_b3;"
            "DROP TABLE IF EXISTS reviewer_flags;"
            "DROP TABLE IF EXISTS sessions;"
            "DROP TABLE IF EXISTS runs;"
        )
        conn.executescript(_SCHEMA_SQL)


def migrate_from_json(runs: list[dict], sessions: dict) -> int:
    """
    One-shot migration: import legacy JSON store into SQLite.
    Skips runs that already exist (idempotent).
    Returns the number of rows inserted.
    """
    inserted = 0
    with _connect() as conn:
        for payload in runs:
            existing = conn.execute(
                "SELECT 1 FROM runs WHERE run_id = ?", (payload["run_id"],)
            ).fetchone()
            if existing:
                continue
            ticker = _extract_ticker(payload.get("subject", ""))
            conn.execute(
                """
                INSERT INTO runs
                    (run_id, ticker, scope, status, initiated_at, completed_at, confidence, payload_json)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    payload["run_id"],
                    ticker,
                    payload.get("scope", "auditor"),
                    "HALTED" if payload.get("halted") else "COMPLETE",
                    payload.get("session", {}).get("initiated_at") if payload.get("session") else None,
                    payload.get("session", {}).get("completed_at") if payload.get("session") else None,
                    payload.get("confidence_score"),
                    json.dumps(payload, default=str),
                ),
            )
            inserted += 1

        for run_id, session in sessions.items():
            existing = conn.execute(
                "SELECT 1 FROM sessions WHERE session_id = ?", (run_id,)
            ).fetchone()
            if existing:
                continue
            ticker = session.get("ticker", "CHAT")
            conn.execute(
                "INSERT INTO sessions (session_id, run_id, ticker, session_json) VALUES (?,?,?,?)",
                (run_id, run_id, ticker, json.dumps(session, default=str)),
            )

    return inserted
