from __future__ import annotations
import json
from datetime import datetime, timezone
from pathlib import Path
import sqlite3

from .contracts import V1

SCHEMA = [
    "CREATE TABLE IF NOT EXISTS runs (digest TEXT PRIMARY KEY, run_id TEXT NOT NULL, result TEXT NOT NULL)",
    """CREATE TABLE IF NOT EXISTS contracts (
           id INTEGER PRIMARY KEY CHECK (id = 1),
           current_version TEXT NOT NULL,
           updated_at TEXT NOT NULL)""",
    """CREATE TABLE IF NOT EXISTS contract_events (
           seq INTEGER PRIMARY KEY,
           from_version TEXT,
           to_version TEXT NOT NULL,
           at TEXT NOT NULL)""",
    """CREATE TABLE IF NOT EXISTS manifests (
           manifest_id TEXT PRIMARY KEY,
           seq INTEGER NOT NULL,
           contract_version TEXT NOT NULL,
           received_at TEXT NOT NULL,
           payload TEXT NOT NULL)""",
    """CREATE TABLE IF NOT EXISTS verifications (
           vid INTEGER PRIMARY KEY,
           manifest_id TEXT NOT NULL,
           row_no INTEGER NOT NULL,
           key TEXT NOT NULL,
           contract_version TEXT NOT NULL,
           status TEXT NOT NULL,
           evidence TEXT NOT NULL,
           reason TEXT NOT NULL,
           migration_state TEXT NOT NULL DEFAULT 'pending',
           migration_note TEXT,
           verified_at TEXT NOT NULL,
           UNIQUE (manifest_id, row_no))""",
    """CREATE TABLE IF NOT EXISTS migration (
           id INTEGER PRIMARY KEY CHECK (id = 1),
           cursor INTEGER NOT NULL DEFAULT 0,
           done INTEGER NOT NULL DEFAULT 0)""",
    """CREATE TABLE IF NOT EXISTS object_snapshots (
           manifest_id TEXT PRIMARY KEY,
           objects TEXT NOT NULL,
           updated_at TEXT NOT NULL)""",
]

VERIFICATION_COLUMNS = "vid, manifest_id, row_no, key, contract_version, status, evidence, reason, migration_state, migration_note, verified_at"


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def _manifest(row) -> dict:
    return {"manifest_id": row[0], "seq": row[1], "contract_version": row[2],
            "received_at": row[3], "payload": json.loads(row[4])}


def _verification(row) -> dict:
    return {"vid": row[0], "manifest_id": row[1], "row_no": row[2], "key": row[3],
            "contract_version": row[4], "status": row[5], "evidence": row[6], "reason": row[7],
            "migration_state": row[8], "migration_note": row[9], "verified_at": row[10]}


class Store:
    def __init__(self, path: str):
        self.path = path
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            for statement in SCHEMA:
                db.execute(statement)

    def connect(self):
        return sqlite3.connect(self.path)

    def _ensure_contract(self, db) -> None:
        db.execute("INSERT OR IGNORE INTO contracts (id, current_version, updated_at) VALUES (1, ?, ?)",
                   (V1, utcnow()))

    # ---- 批次运行（原有行为，保持不变） ----

    def record(self, digest: str, run_id: str, result: dict) -> dict:
        with self.connect() as db:
            db.execute("INSERT OR IGNORE INTO runs VALUES (?, ?, ?)",
                       (digest, run_id, json.dumps(result, ensure_ascii=False, sort_keys=True)))
            row = db.execute("SELECT run_id, result FROM runs WHERE digest=?", (digest,)).fetchone()
        return {"run_id": row[0], "result": json.loads(row[1])}

    def count(self) -> int:
        with self.connect() as db:
            return db.execute("SELECT COUNT(*) FROM runs").fetchone()[0]

    def all(self) -> list[dict]:
        with self.connect() as db:
            rows = db.execute("SELECT run_id, digest, result FROM runs ORDER BY run_id").fetchall()
        return [{"run_id": r[0], "digest": r[1], "result": json.loads(r[2])} for r in rows]

    # ---- 摘要契约 ----

    def current_contract(self) -> dict:
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            self._ensure_contract(db)
            row = db.execute("SELECT current_version, updated_at FROM contracts WHERE id=1").fetchone()
        return {"version": row[0], "updated_at": row[1]}

    def upgrade_contract(self, to_version: str) -> dict:
        """单向升级当前契约；并发升级只有一个生效，重复调用幂等。"""
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            self._ensure_contract(db)
            previous = db.execute("SELECT current_version FROM contracts WHERE id=1").fetchone()[0]
            upgraded = previous != to_version
            if upgraded:
                db.execute("UPDATE contracts SET current_version=?, updated_at=? WHERE id=1",
                           (to_version, utcnow()))
                db.execute("INSERT INTO contract_events (from_version, to_version, at) VALUES (?,?,?)",
                           (previous, to_version, utcnow()))
        return {"previous": previous, "contract_version": to_version, "upgraded": upgraded}

    def contract_events(self) -> list[dict]:
        with self.connect() as db:
            rows = db.execute("SELECT from_version, to_version, at FROM contract_events ORDER BY seq").fetchall()
        return [{"from_version": r[0], "to_version": r[1], "at": r[2]} for r in rows]

    # ---- 清单接收：绑定接收时的契约版本 ----

    def record_manifest(self, manifest_id: str, payload: dict) -> dict:
        """接收清单并绑定当前契约版本；同一内容重复接收沿用首次绑定。"""
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            self._ensure_contract(db)
            version = db.execute("SELECT current_version FROM contracts WHERE id=1").fetchone()[0]
            db.execute(
                """INSERT OR IGNORE INTO manifests (manifest_id, seq, contract_version, received_at, payload)
                   VALUES (?, (SELECT COALESCE(MAX(seq), 0) + 1 FROM manifests), ?, ?, ?)""",
                (manifest_id, version, utcnow(),
                 json.dumps(payload, ensure_ascii=False, sort_keys=True)))
            row = db.execute("SELECT manifest_id, seq, contract_version, received_at, payload FROM manifests WHERE manifest_id=?",
                             (manifest_id,)).fetchone()
        return _manifest(row)

    def get_manifest(self, manifest_id: str) -> dict | None:
        with self.connect() as db:
            row = db.execute("SELECT manifest_id, seq, contract_version, received_at, payload FROM manifests WHERE manifest_id=?",
                             (manifest_id,)).fetchone()
        return _manifest(row) if row else None

    def latest_manifest(self) -> dict | None:
        with self.connect() as db:
            row = db.execute("SELECT manifest_id, seq, contract_version, received_at, payload FROM manifests ORDER BY seq DESC LIMIT 1").fetchone()
        return _manifest(row) if row else None

    # ---- 核验记录：重试就地更新，不新增 ----

    def record_objects(self, manifest_id: str, objects: list[dict]) -> None:
        """留存本次核验采用的对象证据快照，供重试沿用。"""
        with self.connect() as db:
            db.execute(
                """INSERT INTO object_snapshots (manifest_id, objects, updated_at) VALUES (?,?,?)
                   ON CONFLICT (manifest_id) DO UPDATE SET objects=excluded.objects, updated_at=excluded.updated_at""",
                (manifest_id, json.dumps(objects, ensure_ascii=False, sort_keys=True), utcnow()))

    def get_objects(self, manifest_id: str) -> list[dict] | None:
        with self.connect() as db:
            row = db.execute("SELECT objects FROM object_snapshots WHERE manifest_id=?",
                             (manifest_id,)).fetchone()
        return json.loads(row[0]) if row else None

    def record_verifications(self, manifest_id: str, rows: list[dict]) -> None:
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            for row in rows:
                db.execute(
                    """INSERT INTO verifications (manifest_id, row_no, key, contract_version, status, evidence, reason, verified_at)
                       VALUES (?,?,?,?,?,?,?,?)
                       ON CONFLICT (manifest_id, row_no) DO UPDATE SET
                           key=excluded.key, contract_version=excluded.contract_version,
                           status=excluded.status, evidence=excluded.evidence,
                           reason=excluded.reason, verified_at=excluded.verified_at""",
                    (manifest_id, row["row_no"], row["key"], row["contract_version"],
                     row["status"], row["evidence"], row["reason"], utcnow()))

    def verifications_for(self, manifest_id: str) -> list[dict]:
        with self.connect() as db:
            rows = db.execute(
                f"SELECT {VERIFICATION_COLUMNS} FROM verifications WHERE manifest_id=? ORDER BY row_no",
                (manifest_id,)).fetchall()
        return [_verification(r) for r in rows]

    def verification_count(self, manifest_id: str | None = None) -> int:
        with self.connect() as db:
            if manifest_id is None:
                return db.execute("SELECT COUNT(*) FROM verifications").fetchone()[0]
            return db.execute("SELECT COUNT(*) FROM verifications WHERE manifest_id=?", (manifest_id,)).fetchone()[0]

    # ---- 历史迁移：游标推进，中断可恢复 ----

    def migration_status(self) -> dict:
        with self.connect() as db:
            db.execute("INSERT OR IGNORE INTO migration (id, cursor, done) VALUES (1, 0, 0)")
            cursor, done = db.execute("SELECT cursor, done FROM migration WHERE id=1").fetchone()
            counts = dict(db.execute("SELECT migration_state, COUNT(*) FROM verifications GROUP BY migration_state").fetchall())
        return {"cursor": cursor, "done": bool(done),
                "carried": counts.get("carried", 0), "refused": counts.get("refused", 0),
                "current": counts.get("current", 0), "pending": counts.get("pending", 0)}

    def fetch_verifications_after(self, cursor: int, limit: int) -> list[dict]:
        with self.connect() as db:
            rows = db.execute(
                f"SELECT {VERIFICATION_COLUMNS} FROM verifications WHERE vid>? ORDER BY vid LIMIT ?",
                (cursor, limit)).fetchall()
        return [_verification(r) for r in rows]

    def migrate_step(self, vid: int, state: str, note: str, done: bool) -> None:
        """单条迁移与游标推进同事务提交，崩溃后从最后位置恢复。"""
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            db.execute("UPDATE verifications SET migration_state=?, migration_note=? WHERE vid=?",
                       (state, note, vid))
            db.execute(
                """INSERT INTO migration (id, cursor, done) VALUES (1, ?, ?)
                   ON CONFLICT (id) DO UPDATE SET cursor=excluded.cursor, done=excluded.done""",
                (vid, int(done)))

    def set_migration_done(self, done: bool) -> None:
        with self.connect() as db:
            db.execute(
                """INSERT INTO migration (id, cursor, done) VALUES (1, 0, ?)
                   ON CONFLICT (id) DO UPDATE SET done=excluded.done""",
                (int(done),))
