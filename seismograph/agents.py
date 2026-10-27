"""The agents. Each has one role; the Pipeline (pipeline.py) moves work between them.

Scout          finds candidate SEC filings (EDGAR full-text search), stores them in the evidence store
Extractor      model-backed (Nightshift); reads a filing through Zetaris and drafts a claims-only record
Verifier       deterministic; checks the quote and number appear verbatim in the source
Judge          model-backed (Nightshift); independent second opinion, can send the record back
MemorySteward  Meterless H-MEM; recalls what is known about the company, detects duplicates/supersession,
               applies human feedback to memory confidence
Corroborator   cross-source checks through Zetaris (WARN notices, other documents in the same filing)
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta

from . import prompts
from .edgar import Edgar
from .hmem import HMEM
from .logs import TraceLog
from .nightshift import Nightshift, ProviderError, parse_json
from .replay import Replay
from .store import Store, norm_company, sha256
from .zetaris import Zetaris, lit

KEYWORDS = re.compile(
    r"artificial intelligence|\bA\.?I\.?\b|automation|automate|machine learning|generative|"
    r"workforce|headcount|reduction in force|layoff|lay off|laid off|positions|employees|"
    r"restructur|job cuts|eliminat|severance", re.I)


def normspace(s: str) -> str:
    s = (s or "").replace("“", '"').replace("”", '"').replace("‘", "'").replace("’", "'")
    s = s.replace("—", "-").replace("–", "-")
    return re.sub(r"\s+", " ", s).strip().lower()


BOILERPLATE = re.compile(r"forward[- ]looking statements?|cautionary (note|statement)|risk factors|safe harbor", re.I)


def in_boilerplate(text: str, quote: str) -> bool:
    """True when the quote sits in a paragraph of legal boilerplate (forward-looking statements, risk factors)."""
    target = normspace(quote)[:80]
    for p in re.split(r"\n\s*\n", text):
        if target and target in normspace(p):
            return bool(BOILERPLATE.search(p))
    return False


def excerpt(text: str, max_chars: int = 12000) -> str:
    """Keep the paragraphs that matter, in document order, within a size budget."""
    paras = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
    keep = [p for p in paras if KEYWORDS.search(p)]
    if not keep:
        keep = paras[:20]
    out, size = [], 0
    for p in keep:
        if size + len(p) > max_chars:
            break
        out.append(p)
        size += len(p) + 2
    return "\n\n".join(out)


@dataclass
class Candidate:
    accession: str
    cik: str
    company: str
    form: str
    file_date: str
    items: str
    doc_id: str
    url: str
    doc_type: str


@dataclass
class Draft:
    data: dict
    model: str = ""
    attempts: int = 0
    problems: list[str] = field(default_factory=list)


# ------------------------------------------------------------------- model calls
class ModelCaller:
    """Calls Nightshift (or replays a recorded call in SAMPLE_MODE) and logs which model answered."""

    def __init__(self, ns: Nightshift | None, replay: Replay, log: TraceLog):
        self.ns, self.replay, self.log = ns, replay, log

    def __call__(self, agent: str, model: str, system: str, user: str, *, target: str | None, retry: int,
                 purpose: str) -> tuple[dict, str]:
        request = {"model": model, "system": system, "user": user}
        if self.replay.sample_mode:
            rec = self.replay.load("nightshift", request)
            text, served, nemo = rec["text"], rec["served_model"], rec["is_nemotron"]
            data = parse_json(text)
        else:
            # Transient provider failures (HTTP 5xx, an answer that is not JSON) are retried twice
            # before the error reaches the pipeline. Each failed attempt is logged.
            last_exc: Exception | None = None
            for attempt in range(3):
                try:
                    c = self.ns.chat(model, system, user, agent=agent)
                    text, served, nemo = c.text, c.served_model, c.is_nemotron
                    data = parse_json(text)
                    self.replay.save("nightshift", request, {"text": text, "served_model": served, "is_nemotron": nemo})
                    last_exc = None
                    break
                except (ProviderError, ValueError, json.JSONDecodeError) as exc:
                    last_exc = exc
                    permanent = isinstance(exc, ProviderError) and ("unauthorized" in str(exc) or "REQUIRE_NEMOTRON" in str(exc))
                    self.log.log(agent, "model_call", status="failure", input_summary=purpose,
                                 output_summary=str(exc)[:300], model=model, retry_count=retry,
                                 target_agent=target, provider_attempt=attempt + 1,
                                 next_step="give up" if permanent or attempt == 2 else "retry provider call")
                    if permanent:
                        break
            if last_exc is not None:
                raise last_exc
        self.log.log(agent, "model_call", input_summary=purpose, output_summary=json.dumps(data)[:300],
                     model=served, retry_count=retry, target_agent=target, requested_model=model,
                     nemotron_served=nemo)
        return data, served


# --------------------------------------------------------------------------- Scout
class Scout:
    QUERIES = [
        '"artificial intelligence" "workforce reduction"',
        '"artificial intelligence" "reduction in force"',
        '"artificial intelligence" "headcount"  "restructuring"',
        '"automation" "workforce reduction"',
    ]

    def __init__(self, edgar: Edgar, store: Store, zetaris: Zetaris, log: TraceLog):
        self.edgar, self.store, self.z, self.log = edgar, store, zetaris, log

    def known_docs(self) -> set[str]:
        rows = self.z.query(f"SELECT doc_id FROM {self.z.t('records')}", agent="Scout",
                            purpose="documents already turned into records (skip them)")
        return {r["doc_id"] for r in rows}

    def discover(self, start: str, end: str, forms: str, max_docs: int, ciks: str | None = None,
                 queries: list[str] | None = None, source: str = "sec") -> list[Candidate]:
        known = self.known_docs()
        seen, out = set(), []
        if source == "uk":
            from . import uk
            out = uk.candidates(self.edgar.replay, self.log, start, end, known, queries)
        elif source != "sec":
            from . import foreign
            out = foreign.candidates(self.edgar.replay, self.log, source, start, end, known)
        for q in ([] if source != "sec" else (queries or self.QUERIES)):
            try:
                hits = self.edgar.search(q, forms, start, end, ciks=ciks) if ciks else self.edgar.search(q, forms, start, end)
            except Exception as exc:  # one failing query should not stop the run
                self.log.log("Scout", "sec_search", status="failure", input_summary=q, output_summary=str(exc)[:200])
                continue
            self.log.log("Scout", "sec_search", input_summary=f"{q} | {forms} | {start}..{end}" + (f" | ciks {ciks}" if ciks else ""),
                         output_summary=f"{len(hits)} matching documents")
            for h in hits:
                doc_id = f"{h['accession']}:{h['filename']}"
                if not h["filename"] or doc_id in seen or doc_id in known:
                    continue
                seen.add(doc_id)
                out.append(Candidate(h["accession"], h["cik"], h["company"], h["form"] or "", h["file_date"] or "",
                                     h["items"], doc_id, h["url"], h["file_type"] or h["form"] or ""))
        replay = getattr(self.edgar, "replay", None)
        if replay is not None and replay.sample_mode:  # replay only documents that a live run recorded
            recorded = [c for c in out if replay.has("sec_doc", {"url": c.url})]
            self.log.log("Scout", "sample_filter", output_summary=f"{len(recorded)} of {len(out)} candidates have "
                         "recorded documents in data/sample/")
            out = recorded
        out = out[:max_docs]
        self.log.log("Scout", "handoff", output_summary=f"{len(out)} new documents to fetch", target_agent="Extractor",
                     skipped_known=len(known))
        return out

    def fetch_and_store(self, c: Candidate, run_id: str) -> bool:
        try:
            if c.doc_id.startswith("NSM:"):
                from . import uk
                text = uk.fetch_text(self.edgar.replay, c.url)
            elif c.doc_id.split(":")[0].lower() in __import__("seismograph.foreign", fromlist=["ADAPTERS"]).ADAPTERS:
                from . import foreign
                text = foreign.fetch_text(self.edgar.replay, c.url)
            else:
                text = self.edgar.fetch_text(c.url)
        except Exception as exc:
            self.log.log("Scout", "fetch_document", status="failure", input_summary=c.url, output_summary=str(exc)[:200])
            return False
        self.store.upsert("filings", {"accession": c.accession, "cik": c.cik, "company": c.company, "form": c.form,
                                      "file_date": c.file_date or None, "items": c.items,
                                      "index_url": c.url.rsplit("/", 1)[0] + "/", "discovered_run": run_id}, "accession")
        self.store.upsert("documents", {"doc_id": c.doc_id, "accession": c.accession, "url": c.url,
                                        "doc_type": c.doc_type, "text": text, "sha256": sha256(text)}, "doc_id")
        self.log.log("Scout", "fetch_document", input_summary=c.url,
                     output_summary=f"{len(text)} chars stored as {c.doc_id}", sha256=sha256(text))
        return True


# ----------------------------------------------------------------------- Extractor
class Extractor:
    def __init__(self, call: ModelCaller, zetaris: Zetaris, model: str):
        self.call, self.z, self.model = call, zetaris, model

    def read(self, c: Candidate) -> str:
        rows = self.z.query(f"SELECT text FROM {self.z.t('documents')} WHERE doc_id = {lit(c.doc_id)}",
                            agent="Extractor", purpose=f"read {c.doc_id} through Zetaris")
        if not rows:
            raise LookupError(f"{c.doc_id} not visible through Zetaris yet")
        return rows[0]["text"]

    def draft(self, c: Candidate, text: str, previous: Draft | None, problems: list[str], retry: int) -> Draft:
        retry_block = ""
        if previous is not None:
            retry_block = prompts.RETRY_BLOCK.format(problems="\n".join(f"- {p}" for p in problems),
                                                     previous=json.dumps(previous.data, ensure_ascii=False))
        user = prompts.EXTRACTOR_USER.format(company=c.company, form=c.form, doc_type=c.doc_type,
                                             file_date=c.file_date, url=c.url, retry_block=retry_block,
                                             excerpt=excerpt(text))
        data, served = self.call("Extractor", self.model, prompts.EXTRACTOR_SYSTEM, user, target="Verifier",
                                 retry=retry, purpose=f"draft record from {c.doc_id}")
        return Draft(data=data, model=served, attempts=retry + 1)


# ------------------------------------------------------------------------ Verifier
class Verifier:
    def __init__(self, log: TraceLog):
        self.log = log

    def check(self, d: Draft, text: str, retry: int) -> list[str]:
        data, problems = d.data, []
        body = normspace(text)
        label = data.get("ai_attribution")
        if label not in ("explicit", "partial", "none"):
            problems.append(f"ai_attribution must be explicit, partial or none (got {label!r})")
        quote = data.get("ai_quote")
        if label in ("explicit", "partial"):
            if not quote:
                problems.append("an explicit or partial label needs the employer's verbatim quote in ai_quote")
            elif normspace(quote) not in body:
                problems.append("ai_quote does not appear verbatim in the source; copy it character for character")
            elif len(quote.split()) > 80:
                problems.append("ai_quote is too long; keep it to the 1-2 sentences that connect the cuts to AI")
        cq = data.get("cut_quote")
        if not cq:
            problems.append("cut_quote is required: copy the sentence that announces or reports the job cuts")
        elif normspace(cq) not in body:
            problems.append("cut_quote does not appear verbatim in the source; copy it character for character")
        else:
            for quote, name in ((cq, "cut_quote"), (quote, "ai_quote")):
                if quote and in_boilerplate(text, quote):
                    problems.append(f"{name} comes from forward-looking-statement or risk-factor boilerplate, which is "
                                    "not an announcement; if the document announces no cuts, set is_workforce_reduction to false")
        jt = data.get("jobs_cut_text")
        if jt and normspace(jt) not in body:
            problems.append("jobs_cut_text does not appear verbatim in the source")
        n = data.get("jobs_cut")
        if n is not None:
            if not isinstance(n, int) or n <= 0:
                problems.append("jobs_cut must be a positive integer or null")
            elif not jt or not re.search(r"\d", jt):
                problems.append("jobs_cut is set but jobs_cut_text does not contain the stated number")
            else:
                digits = {int(x.replace(",", "")) for x in re.findall(r"\d[\d,]*", jt)}
                if n not in digits:
                    problems.append(f"jobs_cut {n} does not match the number in jobs_cut_text ({jt!r})")
        ev = data.get("event_date")
        if ev:
            try:
                datetime.strptime(ev, "%Y-%m-%d")
            except ValueError:
                problems.append("event_date must be YYYY-MM-DD or null")
        self.log.log("Verifier", "verify_against_source", status="success" if not problems else "rejected",
                     input_summary=f"quote/number check, attempt {d.attempts}",
                     output_summary="verbatim checks passed" if not problems else "; ".join(problems),
                     target_agent="Judge" if not problems else "Extractor", retry_count=retry)
        return problems


# --------------------------------------------------------------------------- Judge
class Judge:
    def __init__(self, call: ModelCaller, model: str):
        self.call, self.model = call, model

    @staticmethod
    def passage(text: str, quote: str | None, width: int = 1800) -> str:
        if quote:
            i = normspace(text).find(normspace(quote)[:60])
            if i >= 0:  # map position in normalised text back approximately to raw text
                ratio = len(text) / max(1, len(normspace(text)))
                j = int(i * ratio)
                return text[max(0, j - width): j + len(quote) + width]
        return excerpt(text, 4000)

    def review(self, d: Draft, c: Candidate, text: str, memories: list[str], retry: int) -> dict:
        user = prompts.JUDGE_USER.format(record=json.dumps(d.data, ensure_ascii=False, indent=1), url=c.url,
                                         passage=self.passage(text, d.data.get("ai_quote")),
                                         memories="\n".join(f"- {m}" for m in memories) or "- (nothing recalled)")
        verdict, served = self.call("Judge", self.model, prompts.JUDGE_SYSTEM, user, target="Extractor",
                                    retry=retry, purpose=f"judge draft for {c.doc_id}")
        verdict["model"] = served
        v = str(verdict.get("verdict", "fail")).lower()
        verdict["verdict"] = "pass" if v == "pass" else "fail"
        return verdict


# ------------------------------------------------------------------- MemorySteward
class MemorySteward:
    """Meterless H-MEM in the product: recall, dedupe/supersede, and learning from human review."""

    def __init__(self, hmem: HMEM, log: TraceLog):
        self.h, self.log = hmem, log

    def recall(self, company: str) -> list[dict]:
        cn = norm_company(company)
        results = self.h.query(f"{company} job cuts AI attribution", entities=[cn], domain="layoffs",
                               threshold=0.15, top_n=5)
        results = [r for r in results if any(e["name"] == cn for e in r["memory"].entities)]
        self.log.log("MemorySteward", "hmem_recall", input_summary=company,
                     output_summary=f"{len(results)} memories about {cn!r}",
                     memories=[{"id": r["memory"].id, "score": r["score"], "superseded": r["suppressed"]} for r in results],
                     target_agent="Judge")
        return results

    @staticmethod
    def describe(results: list[dict]) -> list[str]:
        out = []
        for r in results:
            m = r["memory"]
            flag = " [superseded]" if m.supersededBy else ""
            out.append(f"{m.content} (source {m.source}; confidence {m.confidence:.2f}){flag}")
        return out

    def dedupe(self, record: dict, results: list[dict]) -> tuple[str, str | None]:
        """Return ("new" | "duplicate" | "supersedes", existing_memory_id)."""
        ev = record.get("event_date")
        for r in results:
            m = r["memory"]
            if m.supersededBy:
                continue
            old = m.data or {}
            if old.get("doc_id") == record["doc_id"]:
                return "duplicate", m.id
            same_window = False
            if ev and old.get("event_date"):
                try:
                    gap = abs((date.fromisoformat(ev) - date.fromisoformat(old["event_date"])).days)
                    same_window = gap <= 45
                except ValueError:
                    pass
            same_accession = old.get("accession") == record.get("accession")
            if same_accession or same_window:
                # Same event. A document that states a number supersedes one that did not; otherwise duplicate.
                if record.get("jobs_cut") and not old.get("jobs_cut"):
                    return "supersedes", m.id
                return "duplicate", m.id
        return "new", None

    def remember(self, record: dict, supersedes: str | None) -> str:
        num = record.get("jobs_cut_text") or "job cuts"
        quote = f' Employer quote: "{record["ai_quote"]}"' if record.get("ai_quote") else ""
        content = (f"{record['company']} announced {num} (event date {record.get('event_date') or 'not stated'}); "
                   f"employer's AI attribution: {record['ai_attribution']}.{quote}")
        month = (record.get("event_date") or "")[:7]
        m = self.h.add(content, source=record["source_url"], confidence=0.6, type="factual", layer="working",
                       tags=[t for t in (month, record["ai_attribution"], record.get("doc_type")) if t],
                       domain="layoffs", entities=[{"name": record["company_norm"], "type": "employer"}],
                       provenance={"origin": "tool", "learnedAt": self.h.clock(),
                                   "label": f"extracted by Extractor, checked by Verifier and Judge, run {record['run_id']}",
                                   "refs": {"sourceUrl": record["source_url"]}},
                       supersedes=supersedes,
                       data={k: record.get(k) for k in ("record_id", "doc_id", "accession", "event_date", "jobs_cut",
                                                        "ai_attribution")})
        self.log.log("MemorySteward", "hmem_create", output_summary=f"{m.id} for {record['company']}",
                     memory_id=m.id, supersedes=supersedes, ledger_entries=self.h.ledger_count())
        return m.id

    def learn_from_review(self, memory_id: str, approved: bool, note: str) -> None:
        m = self.h.feedback(memory_id, "helpful" if approved else "wrong", reason=note or "human review")
        if approved and m.layer != "long_term":
            prev = m.layer
            m.layer = "long_term"
            self.h._save(m)
            self.h._log(m.id, "promote", {"layer": prev}, {"layer": "long_term"}, {"reason": "approved by editor"})
        self.log.log("MemorySteward", "hmem_feedback", input_summary="human review",
                     output_summary=f"{memory_id}: {'helpful' if approved else 'wrong'} -> confidence {m.confidence:.2f}")


# -------------------------------------------------------------------- Corroborator
class Corroborator:
    def __init__(self, zetaris: Zetaris, log: TraceLog):
        self.z, self.log = zetaris, log

    def check(self, record: dict) -> tuple[list[dict], list[str]]:
        flags: list[str] = []
        cn = record["company_norm"]
        first = cn.split(" ")[0] if cn else ""
        warn: list[dict] = []
        if first:
            rows = self.z.query(
                f"SELECT state, company, notice_date, effective_date, jobs, location, source "
                f"FROM {self.z.t('warn_notices')} WHERE company_norm LIKE {lit(first + '%')}",
                agent="Corroborator", purpose=f"WARN notices for {record['company']}")
            ev = record.get("event_date")
            for r in rows:
                if not _same_company(cn, norm_company(r["company"])):
                    continue
                if ev and r.get("notice_date"):
                    try:
                        if abs((date.fromisoformat(str(r["notice_date"])[:10]) - date.fromisoformat(ev)).days) > 120:
                            continue
                    except ValueError:
                        pass
                warn.append({k: (str(v) if v is not None else None) for k, v in r.items()})
        states = self.z.query(f"SELECT DISTINCT state FROM {self.z.t('warn_notices')}", agent="Corroborator",
                              purpose="which states' WARN data is loaded")
        loaded = sorted(str(s["state"]) for s in states)
        if not loaded:
            flags.append("WARN notices not checked: no state WARN data is loaded yet")
        elif not warn:
            flags.append(f"no matching WARN notice within 120 days in the states loaded ({', '.join(loaded)})")
        if warn and record.get("jobs_cut"):
            total = sum(int(w["jobs"]) for w in warn if w.get("jobs") and str(w["jobs"]).isdigit())
            if total and total != record["jobs_cut"]:
                flags.append(f"WARN notices list {total} jobs in the loaded states; the filing states {record['jobs_cut']} "
                             "(WARN covers only some sites and states)")
        # Gap between documents in the same filing: AI language in one but not the other.
        docs = self.z.query(
            f"SELECT doc_id, doc_type, CASE WHEN lower(text) LIKE '%artificial intelligence%' OR lower(text) LIKE '% ai %' "
            f"OR lower(text) LIKE '%automation%' THEN 1 ELSE 0 END AS mentions_ai "
            f"FROM {self.z.t('documents')} WHERE accession = {lit(record['accession'])}",
            agent="Corroborator", purpose="compare AI language across documents in the same filing")
        if len(docs) > 1:
            with_ai = [d["doc_type"] for d in docs if int(d["mentions_ai"] or 0)]
            without = [d["doc_type"] for d in docs if not int(d["mentions_ai"] or 0)]
            if with_ai and without:
                flags.append(f"AI is mentioned in {', '.join(with_ai)} but not in {', '.join(without)} of the same filing")
        self.log.log("Corroborator", "cross_source_check", input_summary=record["company"],
                     output_summary=f"{len(warn)} WARN matches; flags: {'; '.join(flags) or 'none'}",
                     target_agent="Editor (human review)")
        return warn, flags


def _same_company(a: str, b: str) -> bool:
    if not a or not b:
        return False
    if a == b or a.startswith(b + " ") or b.startswith(a + " "):
        return True
    ta, tb = set(a.split()), set(b.split())
    return len(ta & tb) / max(1, min(len(ta), len(tb))) >= 0.8
