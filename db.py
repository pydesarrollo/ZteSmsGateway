"""
Almacenamiento SQLite: routers configurados y el historial de SMS enviados.

Cada función abre y cierra su propia conexión (sqlite3 no es seguro para
compartir una conexión entre threads, y FastAPI corre los endpoints `def`
normales en un threadpool).
"""
import json
import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
DB_PATH = BASE_DIR / "sms_gateway.db"
ROUTERS_JSON = BASE_DIR / "routers.json"


@contextmanager
def _conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db():
    with _conn() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS routers (
                id TEXT PRIMARY KEY,
                ip TEXT NOT NULL,
                password TEXT NOT NULL,
                numero TEXT,
                created_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                router_id TEXT NOT NULL,
                phone TEXT NOT NULL,
                message TEXT NOT NULL,
                status TEXT NOT NULL CHECK (status IN ('sent', 'failed')),
                error TEXT,
                created_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_messages_router ON messages(router_id)"
        )
    _seed_from_routers_json_once()


def _seed_from_routers_json_once():
    """Migración de una sola vez: si la tabla routers está vacía y existe un
    routers.json (formato de la versión anterior de este proyecto), importa
    esas entradas usando las mismas variables de entorno de password que
    usaba api.py antes de moverse a SQLite."""
    if list_routers():
        return
    if not ROUTERS_JSON.exists():
        return

    default_password = os.environ.get("ZTE_ROUTER_PASSWORD", "admin")
    with open(ROUTERS_JSON, "r", encoding="utf-8") as f:
        entries = json.load(f)

    for entry in entries:
        router_id = entry["id"]
        password_env = f"ZTE_PASSWORD_{router_id.upper()}"
        password = os.environ.get(password_env, default_password)
        upsert_router(router_id, entry["ip"], password, entry.get("numero"))


def list_routers() -> list[dict]:
    with _conn() as conn:
        rows = conn.execute(
            "SELECT id, ip, numero, created_at FROM routers ORDER BY id"
        ).fetchall()
        return [dict(r) for r in rows]


def get_router(router_id: str) -> dict | None:
    with _conn() as conn:
        row = conn.execute(
            "SELECT id, ip, password, numero, created_at FROM routers WHERE id = ?",
            (router_id,),
        ).fetchone()
        return dict(row) if row else None


def upsert_router(router_id: str, ip: str, password: str | None, numero: str | None = None) -> dict:
    """password=None mantiene la password actual (solo valido si el router ya existe)."""
    with _conn() as conn:
        existing = conn.execute(
            "SELECT password FROM routers WHERE id = ?", (router_id,)
        ).fetchone()
        if password is None:
            if not existing:
                raise ValueError("password es requerida para crear un router nuevo")
            password = existing["password"]
        if existing:
            conn.execute(
                "UPDATE routers SET ip = ?, password = ?, numero = ? WHERE id = ?",
                (ip, password, numero, router_id),
            )
        else:
            conn.execute(
                "INSERT INTO routers (id, ip, password, numero, created_at) VALUES (?, ?, ?, ?, ?)",
                (router_id, ip, password, numero, datetime.now(timezone.utc).isoformat()),
            )
    return get_router(router_id)


def delete_router(router_id: str) -> bool:
    with _conn() as conn:
        cur = conn.execute("DELETE FROM routers WHERE id = ?", (router_id,))
        return cur.rowcount > 0


def log_message(router_id: str, phone: str, message: str, status: str, error: str | None = None):
    with _conn() as conn:
        conn.execute(
            """
            INSERT INTO messages (router_id, phone, message, status, error, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (router_id, phone, message, status, error, datetime.now(timezone.utc).isoformat()),
        )


def prune_messages(router_id: str, keep: int):
    """Deja solo los últimos `keep` mensajes de ese router; borra los más antiguos."""
    if keep <= 0:
        return
    with _conn() as conn:
        conn.execute(
            """
            DELETE FROM messages
            WHERE router_id = ? AND id NOT IN (
                SELECT id FROM messages WHERE router_id = ? ORDER BY id DESC LIMIT ?
            )
            """,
            (router_id, router_id, keep),
        )


def list_messages(router_id: str | None = None, limit: int = 20, offset: int = 0) -> list[dict]:
    with _conn() as conn:
        if router_id:
            rows = conn.execute(
                "SELECT * FROM messages WHERE router_id = ? ORDER BY id DESC LIMIT ? OFFSET ?",
                (router_id, limit, offset),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM messages ORDER BY id DESC LIMIT ? OFFSET ?", (limit, offset)
            ).fetchall()
        return [dict(r) for r in rows]


def count_messages(router_id: str | None = None) -> int:
    with _conn() as conn:
        if router_id:
            row = conn.execute(
                "SELECT COUNT(*) AS c FROM messages WHERE router_id = ?", (router_id,)
            ).fetchone()
        else:
            row = conn.execute("SELECT COUNT(*) AS c FROM messages").fetchone()
        return row["c"]


def stats() -> list[dict]:
    """Totales por router: enviados, fallidos y total, incluyendo routers sin mensajes."""
    with _conn() as conn:
        rows = conn.execute(
            """
            SELECT
                r.id AS router_id,
                r.numero AS numero,
                COALESCE(SUM(CASE WHEN m.status = 'sent' THEN 1 ELSE 0 END), 0) AS sent,
                COALESCE(SUM(CASE WHEN m.status = 'failed' THEN 1 ELSE 0 END), 0) AS failed
            FROM routers r
            LEFT JOIN messages m ON m.router_id = r.id
            GROUP BY r.id
            ORDER BY r.id
            """
        ).fetchall()
        return [dict(r) for r in rows]
