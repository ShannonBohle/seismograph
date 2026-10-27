"""Writes to the evidence store. Reads for reasoning go through Zetaris (zetaris.py).

Live mode writes to PostgreSQL (the database Zetaris federates). SAMPLE_MODE writes to a
local SQLite file instead, so a judge can run the agent with no services installed.
"""
from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from pathlib import Path

from .config import ROOT

SCHEMA = (ROOT / "sql" / "01_evidence_store.sql").read_text(encoding="utf-8")
_SUFFIXES = r"\b(incorporated|inc|corporation|corp|company|co|holdings|group|plc|ltd|llc|lp|sa|ag|nv|the)\b"


def norm_company(name: str) -> str:
    n = re.sub(r"\(.*?\)", " ", (name or "").lower())
    n = re.sub(r"[^a-z0-9 ]", " ", n)
    n = re.sub(_SUFFIXES, " ", n)
    return re.sub(r"\s+", " ", n).strip()


def sha256(text: str) -> str:
    return hashlib.sha256((text or "").encode("utf-8")).hexdigest()


class Store:
    def __init__(self, dsn: str | None, sqlite_path: Path | None = None):
        if sqlite_path is not None:
            sqlite_path.parent.mkdir(parents=True, exist_ok=True)
            self.conn = sqlite3.connect(str(sqlite_path))
            self.kind = "sqlite"
        else:
            import psycopg  # imported lazily so sample mode needs no driver
            self.conn = psycopg.connect(dsn, autocommit=True)
            self.kind = "postgres"

    def _sql(self, sql: str) -> str:
        # only bare %s placeholders; LIKE patterns such as '%safety%' must stay as written
        return re.sub(r"%s(?![A-Za-z])", "?", sql) if self.kind == "sqlite" else sql

    def execute(self, sql: str, params=()):
        cur = self.conn.cursor()
        if self.kind == "postgres" and not params:
            cur.execute(sql)  # no parameters: psycopg must not read '%' in LIKE patterns as placeholders
        else:
            cur.execute(self._sql(sql), params)
        if self.kind == "sqlite":
            self.conn.commit()
        return cur

    def init(self) -> None:
        code = "\n".join(re.sub(r"--.*$", "", line) for line in SCHEMA.splitlines())
        if self.kind == "sqlite":  # SQLite spells view creation and booleans differently
            code = code.replace("CREATE OR REPLACE VIEW", "CREATE VIEW IF NOT EXISTS").replace("= TRUE", "= 1")
        for stmt in code.split(";"):
            if stmt.strip():
                self.execute(stmt.strip())

    def upsert(self, table: str, row: dict, key: str) -> None:
        cols = list(row)
        placeholders = ", ".join(["%s"] * len(cols))
        updates = ", ".join(f"{c} = EXCLUDED.{c}" for c in cols if c != key)
        sql = f"INSERT INTO {table} ({', '.join(cols)}) VALUES ({placeholders}) ON CONFLICT ({key}) DO UPDATE SET {updates}"
        self.execute(sql, [json.dumps(v) if isinstance(v, (list, dict)) else v for v in row.values()])

    def fetch(self, sql: str, params=()) -> list[dict]:
        cur = self.execute(sql, params)
        names = [d[0] for d in cur.description]
        return [dict(zip(names, r)) for r in cur.fetchall()]
