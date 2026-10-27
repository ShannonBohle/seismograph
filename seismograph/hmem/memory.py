"""A Python implementation of a subset of the Meterless H-MEM engine spec.

Spec: https://github.com/Meterless/Meterless/tree/main/engines/hmem (Apache-2.0).

Implemented here, and checked against the engine's own conformance vectors in
tests/test_hmem_conformance.py:
  * the memory record contract (source and confidence mandatory, provenance on new writes)
  * the append-only trust ledger (every mutation is logged)
  * feedback deltas (+0.05 helpful, -0.03 not_helpful, -0.20 wrong + "review" tag)
  * eight-signal hybrid retrieval ranking with the superseded penalty
  * conflict scan and auto-resolve (loser marked supersededBy, nothing deleted)

Not implemented: the sleep cycle and dreaming. They are out of scope for this agent.

Storage is a local SQLite file (local-first), so memory survives between runs.
The semantic signal uses a deterministic hashed bag-of-words embedding rather than
a neural embedder; this is a documented limitation.
"""
from __future__ import annotations

import hashlib
import json
import math
import re
import sqlite3
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Callable, Iterable

LAYER_WEIGHT = {"long_term": 1.0, "working": 0.8, "short_term": 0.6}
FEEDBACK_DELTA = {"helpful": 0.05, "not_helpful": -0.03, "wrong": -0.20}
WEIGHTS = {  # canonical blend from docs/retrieval-ranking.md
    "semantic": 0.35, "keyword": 0.20, "tag": 0.10, "domain": 0.10,
    "entity": 0.15, "layer": 0.05, "recency": 0.05, "superseded": 0.20,
}
WEIGHT_PROFILE = "weights@hmem-spec-canonical"
DAY_MS = 86_400_000
STOPWORDS = set(
    "a an the and or of to in on for with by at from is are was were be been it its this that "
    "we our you your they their as do does did when what which who how".split()
)


def clamp01(x: float) -> float:
    return max(0.0, min(1.0, x))


def tokens(text: str) -> list[str]:
    return [t for t in re.findall(r"[a-z0-9]+", (text or "").lower()) if t not in STOPWORDS]


def embed(text: str, dim: int = 256) -> list[float]:
    """Deterministic hashed bag-of-words embedding (unit length)."""
    vec = [0.0] * dim
    for tok in tokens(text):
        h = int(hashlib.sha1(tok.encode()).hexdigest(), 16)
        vec[h % dim] += 1.0
    norm = math.sqrt(sum(v * v for v in vec)) or 1.0
    return [v / norm for v in vec]


def cosine(a: list[float], b: list[float]) -> float:
    if not a or not b:
        return 0.0
    return max(0.0, sum(x * y for x, y in zip(a, b)))


def jaccard(a: Iterable[str], b: Iterable[str]) -> float:
    sa, sb = set(a), set(b)
    if not sa or not sb:
        return 0.0
    return len(sa & sb) / len(sa | sb)


class RecordRejected(ValueError):
    """Raised when a memory record violates the H-MEM record contract."""


@dataclass
class Memory:
    id: str
    content: str
    source: str
    confidence: float
    type: str = "factual"
    layer: str = "working"
    timestamp: int = 0
    lastAccessed: int = 0
    accessCount: int = 0
    tags: list[str] = field(default_factory=list)
    domain: str | None = None
    namespace: str | None = None
    embedding: list[float] = field(default_factory=list)
    provenance: dict | None = None
    goalRunId: str | None = None
    entities: list[dict] = field(default_factory=list)
    relatedTo: list[str] = field(default_factory=list)
    supersedes: str | None = None
    supersededBy: str | None = None
    derivedFrom: list[str] = field(default_factory=list)
    data: dict = field(default_factory=dict)  # structured payload (e.g. the layoff record)


class HMEM:
    """H-MEM store, ledger, retrieval, feedback and conflicts over SQLite."""

    def __init__(self, path: str | Path = ":memory:", clock: Callable[[], int] | None = None,
                 actor: str = "agent:seismograph", on_event: Callable[[dict], None] | None = None):
        self.clock = clock or (lambda: int(time.time() * 1000))
        self.actor = actor
        self.on_event = on_event
        self.db = sqlite3.connect(str(path))
        self.db.execute("CREATE TABLE IF NOT EXISTS memories (id TEXT PRIMARY KEY, body TEXT NOT NULL)")
        self.db.execute(
            "CREATE TABLE IF NOT EXISTS ledger (seq INTEGER PRIMARY KEY AUTOINCREMENT, id TEXT, memory_id TEXT, "
            "action TEXT, actor TEXT, timestamp INTEGER, previous_state TEXT, new_state TEXT, details TEXT)"
        )
        self.db.commit()

    # ---------------------------------------------------------------- storage
    def _save(self, m: Memory) -> None:
        self.db.execute("INSERT OR REPLACE INTO memories (id, body) VALUES (?, ?)", (m.id, json.dumps(asdict(m))))
        self.db.commit()

    def get(self, memory_id: str) -> Memory | None:
        row = self.db.execute("SELECT body FROM memories WHERE id = ?", (memory_id,)).fetchone()
        return Memory(**json.loads(row[0])) if row else None

    def all(self) -> list[Memory]:
        return [Memory(**json.loads(r[0])) for r in self.db.execute("SELECT body FROM memories ORDER BY id")]

    # ----------------------------------------------------------------- ledger
    def _log(self, memory_id: str, action: str, previous: dict | None, new: dict | None, details: dict | None = None):
        entry = {
            "id": f"led_{uuid.uuid4().hex[:12]}", "memoryId": memory_id, "action": action, "actor": self.actor,
            "timestamp": self.clock(), "previousState": previous, "newState": new, "details": details or {},
        }
        self.db.execute(
            "INSERT INTO ledger (id, memory_id, action, actor, timestamp, previous_state, new_state, details) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (entry["id"], memory_id, action, self.actor, entry["timestamp"], json.dumps(previous),
             json.dumps(new), json.dumps(details or {})),
        )
        self.db.commit()
        if self.on_event:
            self.on_event(entry)
        return entry

    def history(self, memory_id: str) -> list[dict]:
        rows = self.db.execute(
            "SELECT id, action, actor, timestamp, previous_state, new_state, details FROM ledger "
            "WHERE memory_id = ? ORDER BY seq", (memory_id,)).fetchall()
        return [{"id": r[0], "memoryId": memory_id, "action": r[1], "actor": r[2], "timestamp": r[3],
                 "previousState": json.loads(r[4]), "newState": json.loads(r[5]), "details": json.loads(r[6])}
                for r in rows]

    def ledger_count(self) -> int:
        return self.db.execute("SELECT COUNT(*) FROM ledger").fetchone()[0]

    # -------------------------------------------------------------------- add
    def add(self, content: str, source: str, confidence: float = 0.7, *, type: str = "factual",
            layer: str = "working", tags: list[str] | None = None, domain: str | None = None,
            entities: list[dict] | None = None, provenance: dict | None = None, goal_run_id: str | None = None,
            supersedes: str | None = None, derived_from: list[str] | None = None, data: dict | None = None) -> Memory:
        if not source or not str(source).strip():
            raise RecordRejected("H-MEM record contract: every memory needs a source")
        if not content or not content.strip():
            raise RecordRejected("H-MEM record contract: memory content is empty")
        now = self.clock()
        m = Memory(
            id=f"mem_{uuid.uuid4().hex[:16]}", content=content.strip(), source=str(source),
            confidence=clamp01(float(confidence)), type=type, layer=layer, timestamp=now, lastAccessed=now,
            tags=list(tags or []), domain=domain, embedding=embed(content),
            provenance=provenance or {"origin": "tool", "learnedAt": now, "label": f"recorded from {source}"},
            goalRunId=goal_run_id, entities=(entities or [])[:10], supersedes=supersedes,
            derivedFrom=list(derived_from or []), data=data or {},
        )
        self._save(m)
        self._log(m.id, "create", None, {"confidence": m.confidence, "layer": m.layer}, {"source": source})
        if supersedes:
            self.mark_superseded(supersedes, m.id, reason="newer record supersedes")
        return m

    def mark_superseded(self, loser_id: str, winner_id: str, reason: str) -> None:
        loser = self.get(loser_id)
        if not loser:
            return
        prev = {"supersededBy": loser.supersededBy}
        loser.supersededBy = winner_id
        self._save(loser)
        self._log(loser_id, "conflict_resolved", prev, {"supersededBy": winner_id}, {"reason": reason})

    # --------------------------------------------------------------- feedback
    def feedback(self, memory_id: str, kind: str, reason: str = "") -> Memory:
        if kind not in FEEDBACK_DELTA:
            raise ValueError(f"unknown feedback kind {kind!r}")
        m = self.get(memory_id)
        if m is None:
            raise KeyError(memory_id)
        prev = {"confidence": m.confidence, "accessCount": m.accessCount}
        m.confidence = round(clamp01(m.confidence + FEEDBACK_DELTA[kind]), 6)
        if kind == "helpful":
            m.accessCount += 1
            m.lastAccessed = self.clock()
        if kind == "wrong" and "review" not in m.tags:
            m.tags.append("review")
        self._save(m)
        self._log(memory_id, "feedback", prev, {"confidence": m.confidence, "accessCount": m.accessCount},
                  {"kind": kind, "reason": reason})
        return m

    # -------------------------------------------------------------- retrieval
    def score(self, m: Memory, query: str, q_emb: list[float], q_tokens: list[str], *, tags=(), domain=None,
              entities=()) -> tuple[float, dict]:
        now = self.clock()
        signals = {
            "semantic": cosine(q_emb, m.embedding),
            "keyword": jaccard(q_tokens[:8], tokens(m.content)),
            "tag": jaccard(tags, m.tags) if tags else 0.0,
            "domain": 1.0 if domain and m.domain == domain else 0.0,
            "entity": jaccard([e.lower() for e in entities], [e["name"].lower() for e in m.entities]) if entities else 0.0,
            "layer": LAYER_WEIGHT.get(m.layer, 0.6),
            "recency": math.exp(-max(0, now - m.lastAccessed) / (14 * DAY_MS)),
            "superseded": 1.0 if m.supersededBy else 0.0,
        }
        raw = sum(WEIGHTS[k] * v for k, v in signals.items() if k != "superseded") - WEIGHTS["superseded"] * signals["superseded"]
        return clamp01(raw) * m.confidence, signals

    def query(self, query: str, *, top_n: int = 5, threshold: float = 0.35, tags=(), domain=None, entities=(),
              goal_run_id: str | None = None, include_unscoped: bool = True, log_read: bool = True) -> list[dict]:
        q_emb, q_tok = embed(query), tokens(query)
        results = []
        for m in self.all():
            if goal_run_id and m.goalRunId not in (goal_run_id, None if include_unscoped else goal_run_id):
                continue
            s, signals = self.score(m, query, q_emb, q_tok, tags=tags, domain=domain, entities=entities)
            if s >= threshold:
                results.append({"memory": m, "score": round(s, 6), "signals": signals,
                                "suppressed": bool(m.supersededBy)})
        results.sort(key=lambda r: r["score"], reverse=True)
        results = results[:top_n]
        if log_read:
            for r in results:
                self._log(r["memory"].id, "read", None, None, {"query": query[:120], "score": r["score"],
                                                                "weights": WEIGHT_PROFILE})
        return results

    # -------------------------------------------------------------- conflicts
    _NEG = re.compile(r"\b(never|not|no|don't|do not|disable|without)\b")
    _POS = re.compile(r"\b(always|enable|must|do)\b")

    def scan_conflicts(self, min_similarity: float = 0.6) -> list[dict]:
        """Find pairs of live memories that look like the same claim with opposing polarity."""
        live = [m for m in self.all() if not m.supersededBy]
        found = []
        for i, a in enumerate(live):
            for b in live[i + 1:]:
                sim = cosine(a.embedding, b.embedding)
                if sim < min_similarity:
                    continue
                pol_a = bool(self._NEG.search(a.content.lower())); pol_b = bool(self._NEG.search(b.content.lower()))
                if pol_a != pol_b:
                    found.append({"a": a.id, "b": b.id, "similarity": round(sim, 4), "kind": "polarity"})
        for c in found:
            self._log(c["a"], "conflict_detected", None, None, {"with": c["b"], "kind": c["kind"]})
        return found

    def auto_resolve(self, conflict: dict, margin: float = 0.2) -> str:
        a, b = self.get(conflict["a"]), self.get(conflict["b"])
        if not a or not b:
            return "queued"
        if abs(a.confidence - b.confidence) < margin:
            return "queued"  # too close to call: a human decides
        winner, loser = (a, b) if a.confidence > b.confidence else (b, a)
        self.mark_superseded(loser.id, winner.id, reason=f"auto-resolve by confidence ({winner.confidence} > {loser.confidence})")
        return "resolved"
