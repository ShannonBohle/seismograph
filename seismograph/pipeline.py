"""Orchestrates the agents for one run.

Per document:  Scout -> Extractor <-> Verifier <-> Judge (up to MAX_RETRIES send-backs)
               -> MemorySteward (H-MEM recall, dedupe, supersede) -> Corroborator (Zetaris cross-source)
               -> evidence store as pending_review. Nothing is published until a human approves it.
"""
from __future__ import annotations

import json
import uuid
from datetime import date, timedelta

from .agents import (Candidate, Corroborator, Draft, Extractor, Judge, MemorySteward, ModelCaller, Scout,
                     Verifier)
from .replay import SampleMissing
from .store import norm_company

MAX_RETRIES = 2


class Pipeline:
    def __init__(self, ctx):
        self.ctx = ctx
        log = ctx.log
        call = ModelCaller(ctx.nightshift, ctx.replay, log)
        self.scout = Scout(ctx.edgar, ctx.store, ctx.zetaris, log)
        self.extractor = Extractor(call, ctx.zetaris, ctx.settings.extractor_model)
        self.verifier = Verifier(log)
        self.judge = Judge(call, ctx.settings.judge_model)
        self.memory = MemorySteward(ctx.hmem, log)
        self.corroborator = Corroborator(ctx.zetaris, log)
        self.log = log

    def run(self, days: int = 60, forms: str = "8-K,6-K", max_docs: int = 10, end: str | None = None,
            ciks: str | None = None, queries: list[str] | None = None, source: str = "sec") -> dict:
        end_d = date.fromisoformat(end) if end else date.today()
        start = (end_d - timedelta(days=days)).isoformat()
        what = {"sec": f"SEC forms {forms}", "uk": "FCA NSM (UK announcements)"}.get(source, source.upper())
        self.log.log("Pipeline", "run_start", input_summary=f"window {start}..{end_d}, {what}, max {max_docs}",
                     target_agent="Scout")
        stats = {"documents": 0, "not_layoffs": 0, "drafted": 0, "judge_failed": 0, "duplicates": 0,
                 "pending_review": 0, "errors": 0}
        for c in self.scout.discover(start, end_d.isoformat(), forms, max_docs, ciks=ciks, queries=queries,
                                     source=source):
            if not self.scout.fetch_and_store(c, self.log.run_id):
                stats["errors"] += 1
                continue
            stats["documents"] += 1
            try:
                outcome = self.process(c)
            except SampleMissing as exc:  # sample mode only: this document's model answers were not recorded
                self.log.log("Pipeline", "not_in_sample", status="skipped", input_summary=c.doc_id,
                             output_summary=str(exc)[:200])
                stats["not_in_sample"] = stats.get("not_in_sample", 0) + 1
                continue
            except Exception as exc:  # keep going; the failure is in the log
                self.log.log("Pipeline", "document_failed", status="failure", input_summary=c.doc_id,
                             output_summary=f"{type(exc).__name__}: {exc}"[:300])
                stats["errors"] += 1
                continue
            stats[outcome] = stats.get(outcome, 0) + 1
        self.log.log("Pipeline", "run_end", output_summary=json.dumps(stats))
        return stats

    def process(self, c: Candidate) -> str:
        text = self.extractor.read(c)
        from .safety import scan
        safety_flags = scan(text)
        if safety_flags:
            self.log.log("SafetyGuard", "prompt_injection_scan", status="flagged", input_summary=c.doc_id,
                         output_summary="; ".join(safety_flags)[:300], target_agent="Editor (human review)")
        recalled = self.memory.recall(c.company)
        draft: Draft | None = None
        send_back: list[str] = []
        verifier_problems: list[str] = []
        verdict: dict = {}
        for attempt in range(MAX_RETRIES + 1):
            draft = self.extractor.draft(c, text, draft if attempt else None, send_back, retry=attempt)
            if not draft.data.get("is_workforce_reduction"):
                self.log.log("Extractor", "not_a_layoff", output_summary=f"{c.doc_id}: no job cuts announced",
                             model=draft.model)
                return "not_layoffs"
            verifier_problems = self.verifier.check(draft, text, retry=attempt)
            verdict = {}
            if verifier_problems:
                send_back = verifier_problems
                continue
            try:
                verdict = self.judge.review(draft, c, text, self.memory.describe(recalled), retry=attempt)
            except SampleMissing:
                raise
            except Exception as exc:  # Judge unreachable after provider retries: hold the record back
                verdict = {"verdict": "not_reached", "critique": f"Judge unavailable: {exc}"[:300], "model": None}
                self.log.log("Judge", "verdict", status="failure", output_summary=verdict["critique"],
                             retry_count=attempt, target_agent="Editor (human review)")
                break
            if verdict["verdict"] == "pass":
                self.log.log("Judge", "verdict", output_summary=f"pass (label {verdict.get('label')})",
                             model=verdict.get("model"), retry_count=attempt, target_agent="MemorySteward")
                break
            send_back = [p for p in [*(verdict.get("problems") or []), verdict.get("critique") or ""] if p]
            self.log.log("Judge", "verdict", status="rejected", output_summary="fail: " + "; ".join(send_back)[:300],
                         model=verdict.get("model"), retry_count=attempt,
                         target_agent="Extractor" if attempt < MAX_RETRIES else "Editor (human review)")
        d = draft.data
        passed = not verifier_problems and verdict.get("verdict") == "pass"
        record = {
            "record_id": f"rec_{uuid.uuid4().hex[:12]}", "run_id": self.log.run_id,
            "company": d.get("company") or c.company, "company_norm": norm_company(d.get("company") or c.company),
            "cik": c.cik, "accession": c.accession, "event_date": d.get("event_date") or c.file_date or None,
            "jobs_cut": d.get("jobs_cut") if isinstance(d.get("jobs_cut"), int) else None,
            "jobs_cut_text": d.get("jobs_cut_text"),
            "ai_attribution": d.get("ai_attribution") or "none", "ai_quote": d.get("ai_quote"),
            "doc_id": c.doc_id, "source_url": c.url, "doc_type": c.doc_type,
            "quote_verified": not verifier_problems,
            "judge_verdict": verdict.get("verdict") or "not_reached",
            "judge_critique": verdict.get("critique") or "; ".join(verifier_problems) or None,
            "extractor_model": draft.model, "judge_model": verdict.get("model"),
        }
        kind, existing = self.memory.dedupe(record, recalled)
        if kind == "duplicate":
            record.update(status="duplicate", warn_match="[]", gap_flags=json.dumps([f"duplicate of memory {existing}"]),
                          memory_id=existing, supersedes=None)
            self._save(record)
            self.log.log("MemorySteward", "dedupe", output_summary=f"{c.doc_id} duplicates memory {existing}; not queued")
            return "duplicates"
        warn, flags = self.corroborator.check(record)
        flags = safety_flags + flags
        if not passed:
            why = "failed the verbatim source checks" if verifier_problems else "the Judge did not pass it"
            flags.insert(0, f"Not publishable: {why} after {MAX_RETRIES} send-backs. Shown to the editor only for a label")
        mem_id = self.memory.remember(record, existing if kind == "supersedes" else None) if passed else None
        record.update(status="pending_review", warn_match=json.dumps(warn), gap_flags=json.dumps(flags),
                      memory_id=mem_id, supersedes=existing if kind == "supersedes" else None)
        self._save(record)
        self.log.log("Pipeline", "queued_for_review", output_summary=f"{record['record_id']} {record['company']} "
                     f"[{record['ai_attribution']}] judge={record['judge_verdict']}", target_agent="Editor (human review)")
        return "pending_review" if passed else "judge_failed"

    def _save(self, record: dict) -> None:
        row = {k: record.get(k) for k in (
            "record_id", "run_id", "company", "company_norm", "cik", "event_date", "jobs_cut", "jobs_cut_text",
            "ai_attribution", "ai_quote", "doc_id", "source_url", "doc_type", "quote_verified", "judge_verdict",
            "judge_critique", "extractor_model", "judge_model", "warn_match", "gap_flags", "memory_id", "supersedes",
            "status")}
        self.ctx.store.upsert("records", row, "record_id")
