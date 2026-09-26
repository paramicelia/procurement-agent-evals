"""SQLite-backed persistence: run checkpoints, the human-approval queue, and
the (only) writable orders table.

Why this file is the safety boundary, not tools.py: place_order() here is
the single code path that can insert into `orders`, and it always re-checks
for an approved approval record before doing so. A bug or a prompt-injected
instruction elsewhere in the codebase cannot skip that check by calling
something else — there is nothing else that writes to `orders`.
"""
from __future__ import annotations

import json
import sqlite3
import time
import uuid
from pathlib import Path

from .models import ExtractedQuote

SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    task_id TEXT PRIMARY KEY,
    raw_text TEXT NOT NULL,
    state TEXT NOT NULL,
    extracted_json TEXT,
    comparison_json TEXT,
    action TEXT,
    reason TEXT,
    followup_text TEXT,
    order_id TEXT,
    updated_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS approvals (
    id TEXT PRIMARY KEY,
    task_id TEXT NOT NULL,
    kind TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    created_at REAL NOT NULL,
    decided_at REAL,
    decided_by TEXT
);

CREATE TABLE IF NOT EXISTS orders (
    idempotency_key TEXT PRIMARY KEY,
    task_id TEXT NOT NULL,
    order_id TEXT NOT NULL,
    order_json TEXT NOT NULL,
    created_at REAL NOT NULL
);
"""

_RUN_FIELDS = [
    "task_id",
    "raw_text",
    "state",
    "extracted_json",
    "comparison_json",
    "action",
    "reason",
    "followup_text",
    "order_id",
]


class ApprovalRequiredError(RuntimeError):
    pass


class Store:
    def __init__(self, db_path: str):
        self.db_path = db_path
        if db_path != ":memory:":
            Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(db_path, timeout=30)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL;") if db_path != ":memory:" else None
        self._conn.executescript(SCHEMA)
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()

    # ---- runs / checkpoints ----------------------------------------------

    def upsert_run(self, task_id: str, **fields) -> None:
        row = self.get_run(task_id)
        if row is None:
            data = {k: None for k in _RUN_FIELDS}
            data["task_id"] = task_id
            data["raw_text"] = ""
            data["state"] = "init"
        else:
            data = {k: row[k] for k in _RUN_FIELDS}
        unknown = set(fields) - set(_RUN_FIELDS)
        if unknown:
            raise ValueError(f"unknown run field(s): {unknown}")
        data.update(fields)
        data["updated_at"] = time.time()
        cols = list(data.keys())
        placeholders = ",".join("?" for _ in cols)
        updates = ",".join(f"{c}=excluded.{c}" for c in cols if c != "task_id")
        self._conn.execute(
            f"INSERT INTO runs ({','.join(cols)}) VALUES ({placeholders}) "
            f"ON CONFLICT(task_id) DO UPDATE SET {updates}",
            [data[c] for c in cols],
        )
        self._conn.commit()

    def get_run(self, task_id: str) -> sqlite3.Row | None:
        return self._conn.execute("SELECT * FROM runs WHERE task_id = ?", (task_id,)).fetchone()

    def all_runs(self) -> list[sqlite3.Row]:
        return self._conn.execute("SELECT * FROM runs ORDER BY task_id").fetchall()

    # ---- approval queue ----------------------------------------------------

    def create_approval(self, task_id: str, kind: str, payload: dict) -> str:
        """Idempotent per (task_id, kind).

        run_task()'s AUTO_ACCEPT branch calls this and then checkpoints the
        run to STATE_AWAITING_APPROVAL in a *separate* commit (see
        orchestrator.py). If the process crashes between those two commits,
        the run row is still 'decided' on restart, so run_task() re-enters
        this branch and calls create_approval() a second time. Without this
        check that used to insert a second 'place_order' approval row with
        the same idempotency key; get_approval_for_task() only ever returns
        the newest one (ORDER BY created_at DESC), so if a human approved
        the older (now-orphaned) row, resume_after_approval() would see the
        newer row still pending and stall forever even though a human had
        genuinely approved the order. Returning the existing row instead of
        inserting a new one makes the whole (side effect, checkpoint) pair
        safe to retry from either side. See
        tests/test_invariants.py::test_crash_between_create_approval_and_checkpoint_does_not_duplicate.
        """
        existing = self._conn.execute(
            "SELECT id FROM approvals WHERE task_id = ? AND kind = ? ORDER BY created_at DESC LIMIT 1",
            (task_id, kind),
        ).fetchone()
        if existing is not None:
            return existing["id"]
        approval_id = str(uuid.uuid4())
        self._conn.execute(
            "INSERT INTO approvals (id, task_id, kind, payload_json, status, created_at) "
            "VALUES (?, ?, ?, ?, 'pending', ?)",
            (approval_id, task_id, kind, json.dumps(payload), time.time()),
        )
        self._conn.commit()
        return approval_id

    def decide_approval(self, approval_id: str, approve: bool, decided_by: str = "human-sim") -> None:
        self._conn.execute(
            "UPDATE approvals SET status = ?, decided_at = ?, decided_by = ? WHERE id = ?",
            ("approved" if approve else "rejected", time.time(), decided_by, approval_id),
        )
        self._conn.commit()

    def get_approval_for_task(self, task_id: str, kind: str) -> sqlite3.Row | None:
        return self._conn.execute(
            "SELECT * FROM approvals WHERE task_id = ? AND kind = ? ORDER BY created_at DESC LIMIT 1",
            (task_id, kind),
        ).fetchone()

    def pending_approvals(self) -> list[sqlite3.Row]:
        return self._conn.execute(
            "SELECT * FROM approvals WHERE status = 'pending' ORDER BY created_at"
        ).fetchall()

    # ---- orders: the only write, and it is idempotent ----------------------

    def place_order(self, *, task_id: str, extracted: ExtractedQuote, idempotency_key: str) -> str:
        approval = self.get_approval_for_task(task_id, "place_order")
        if approval is None or approval["status"] != "approved":
            status = approval["status"] if approval else "none"
            raise ApprovalRequiredError(f"task {task_id}: no approved place_order approval (status={status})")

        existing = self._conn.execute(
            "SELECT * FROM orders WHERE idempotency_key = ?", (idempotency_key,)
        ).fetchone()
        if existing is not None:
            return existing["order_id"]

        order_id = f"PO-{uuid.uuid4().hex[:10]}"
        try:
            self._conn.execute(
                "INSERT INTO orders (idempotency_key, task_id, order_id, order_json, created_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (idempotency_key, task_id, order_id, extracted.model_dump_json(), time.time()),
            )
            self._conn.commit()
        except sqlite3.IntegrityError:
            # Lost a race with a concurrent retry using the same key; return
            # the winner's order id instead of raising or double-inserting.
            existing = self._conn.execute(
                "SELECT * FROM orders WHERE idempotency_key = ?", (idempotency_key,)
            ).fetchone()
            return existing["order_id"]
        return order_id

    def count_orders_for_task(self, task_id: str) -> int:
        row = self._conn.execute("SELECT COUNT(*) AS c FROM orders WHERE task_id = ?", (task_id,)).fetchone()
        return row["c"]

    def all_orders(self) -> list[sqlite3.Row]:
        return self._conn.execute("SELECT * FROM orders ORDER BY created_at").fetchall()
