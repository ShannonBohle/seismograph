"""Zetaris client: the agent's data layer.

All evidence the agents reason over (SEC filings and exhibits, WARN notices, prior
records) is read through Zetaris SQL, which federates the PostgreSQL evidence store
registered as a Zetaris datasource. The request shape follows zetaris/getting-started
scripts/query_zetaris.py: POST /api/proxy/sql-editor/sqls/run-query on the UI origin
with a bearer token and X-Org-ID.
"""
from __future__ import annotations

import json
import re
import time
import uuid

import requests

from .logs import TraceLog
from .replay import Replay


def lit(value) -> str:
    """Render a SQL string literal safely (single quotes doubled)."""
    if value is None:
        return "NULL"
    return "'" + str(value).replace("\\", "\\\\").replace("'", "''") + "'"


class ZetarisError(RuntimeError):
    pass


class Zetaris:
    def __init__(self, settings, log: TraceLog, replay: Replay, local_store=None):
        self.s = settings
        self.log = log
        self.replay = replay
        self._token = settings.zetaris_api_key or None
        self.ds = settings.zetaris_db
        # SAMPLE_MODE only: queries run against the local SQLite copy of the evidence store instead of Zetaris.
        self.local_store = local_store if replay.sample_mode else None

    # ------------------------------------------------------------------ auth
    def _login(self) -> str:
        if self._token:
            return self._token
        if not (self.s.zetaris_username and self.s.zetaris_password):
            raise ZetarisError("Set ZETARIS_API_KEY, or ZETARIS_USERNAME and ZETARIS_PASSWORD.")
        r = requests.post(f"{self.s.zetaris_base_url}/api/auth/login",
                          json={"username": self.s.zetaris_username, "password": self.s.zetaris_password},
                          headers={"X-Request-ID": str(uuid.uuid4())}, timeout=30)
        if r.status_code != 200:
            raise ZetarisError(f"Zetaris login failed: HTTP {r.status_code}")
        body = r.json()
        token = body.get("idToken") or body.get("token") or body.get("accessToken") if isinstance(body, dict) else None
        if not token:
            m = re.search(r"[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}", json.dumps(body))
            token = m.group(0) if m else None
        if not token:
            raise ZetarisError("Zetaris login did not return a token.")
        self._token = token
        return token

    # ----------------------------------------------------------------- query
    def query(self, sql: str, *, agent: str, purpose: str, limit: int = 1000) -> list[dict]:
        request = {"sql": sql, "limit": limit}
        started = time.time()
        try:
            if self.local_store is not None:
                local_sql = sql.replace(f"{self.ds}.", "")
                raw = {"data": self.local_store.fetch(local_sql)}
                purpose = f"[sample mode: local SQLite, not Zetaris] {purpose}"
            else:
                if not re.fullmatch(r"[0-9]+", self.s.zetaris_org_id or ""):
                    raise ZetarisError("Set ZETARIS_ORG_ID to the numeric ID under Management -> Organisation.")
                body = dict(request)
                if self.s.zetaris_engine_id:
                    body["engineId"] = self.s.zetaris_engine_id
                r = requests.post(
                    f"{self.s.zetaris_base_url}/api/proxy/sql-editor/sqls/run-query",
                    json=body, timeout=120,
                    headers={"Authorization": f"Bearer {self._login()}", "X-Org-ID": self.s.zetaris_org_id,
                             "X-Request-ID": str(uuid.uuid4())},
                )
                if r.status_code != 200:
                    raise ZetarisError(f"Zetaris query failed: HTTP {r.status_code} {r.text[:300]}")
                raw = r.json()
            rows = self._rows(raw)
        except Exception as exc:
            self.log.log(agent, "zetaris_query", status="failure", input_summary=purpose,
                         output_summary=str(exc)[:300], sql=sql[:1500])
            raise
        self.log.log(agent, "zetaris_query", input_summary=purpose, output_summary=f"{len(rows)} rows",
                     sql=sql[:1500], elapsed_ms=int((time.time() - started) * 1000))
        return rows

    @staticmethod
    def _rows(raw) -> list[dict]:
        if isinstance(raw, dict) and "data" in raw and not isinstance(raw["data"], list) and isinstance(raw["data"], dict):
            raw = raw["data"]
        data = raw.get("data") if isinstance(raw, dict) else raw
        if data is None:
            return []
        headers = raw.get("headers") or raw.get("columns") or [] if isinstance(raw, dict) else []
        names = [(h.get("name") if isinstance(h, dict) else h) for h in headers]
        names = [str(n).lower() if n is not None else "" for n in names]
        rows = []
        for row in data:
            if isinstance(row, dict):
                rows.append({str(k).lower(): v for k, v in row.items()})
            elif isinstance(row, list) and names and len(row) == len(names):
                rows.append(dict(zip(names, row)))
            else:
                raise ZetarisError("Unexpected Zetaris row shape; expected objects or arrays with headers.")
        return rows

    # ---------------------------------------------------------- domain reads
    def t(self, table: str) -> str:
        return f"{self.ds}.{table}"
