"""HTTP API: POST /run with a task, the same work the command line does.

  {"task": "discover", "source": "sec"|"uk"|"asx"|"bse"|"tdnet"|"cninfo"|"amf", "days": 30, "max_docs": 5}
  {"task": "ask", "question": "Which employers said AI shaped their cuts, and where?"}
  {"task": "check", "claim": "monday.com cut 20% of staff because of AI"}
  {"task": "publish"} | {"task": "countries"} | {"task": "evaluate"}

Runs bind to 0.0.0.0 so the API is reachable from outside Docker. Every response includes the run's trace path; errors
come back as JSON, never as a crash. With SAMPLE_MODE=true (the Docker default) it replays the recorded live run and
needs no keys, Zetaris or Nightshift.
"""
from __future__ import annotations

import json
import time
import traceback
from pathlib import Path

TASKS = ("discover", "ask", "check", "publish", "countries", "evaluate")


def handle(payload: dict) -> dict:
    from . import ask, compare
    from . import review as rv
    from .context import build
    from .pipeline import Pipeline
    task = (payload or {}).get("task")
    if task not in TASKS:
        return {"ok": False, "error": f"task must be one of {', '.join(TASKS)}"}
    started = time.time()
    ctx = build(echo=False)
    try:
        if ctx.settings.sample_mode and task in ("ask", "check", "countries", "evaluate"):
            seed_published(ctx)
        if task == "discover":
            src = payload.get("source", "sec")
            result = Pipeline(ctx).run(days=int(payload.get("days", 30)), forms=payload.get("forms", "8-K,6-K"),
                                       max_docs=min(int(payload.get("max_docs", 5)), 20), end=payload.get("end"),
                                       source=src)
        elif task == "ask":
            result = ask.ask(ctx, str(payload.get("question", ""))[:500])
        elif task == "check":
            claim = str(payload.get("claim", ""))[:500]
            result = ask.ask(ctx, "What did the employer named in this claim itself say about AI and its job cuts? "
                                  f"Return its records with the verbatim quote and source. Claim: {claim}")
            result["claim"] = claim
            result["how_to_read"] = ("The rows are the employer's own words from its filing. If no row is returned, "
                                     "the published record holds nothing from this employer that supports the claim.")
        elif task == "publish":
            result = rv.publish(ctx)
        elif task == "countries":
            result = compare.compare(ctx)
        else:
            result = rv.evaluate(ctx)
        return {"ok": True, "task": task, "result": result, "trace": str(ctx.log.path),
                "mode": "sample" if ctx.settings.sample_mode else "live", "seconds": round(time.time() - started, 1)}
    except Exception as exc:  # structured error, never a crash
        ctx.log.log("API", "run_failed", status="failure", input_summary=json.dumps(payload)[:200],
                    output_summary=f"{type(exc).__name__}: {exc}"[:300])
        return {"ok": False, "task": task, "error": f"{type(exc).__name__}: {exc}", "trace": str(ctx.log.path),
                "detail": traceback.format_exc(limit=2)[-600:]}


def app():
    from fastapi import FastAPI
    from fastapi.responses import JSONResponse
    api = FastAPI(title="Seismograph", description=__doc__)

    @api.get("/health")
    def health():
        return {"ok": True}

    @api.post("/run")
    def run(payload: dict):
        out = handle(payload)
        return JSONResponse(json.loads(json.dumps(out, default=str)), status_code=200 if out.get("ok") else 400)

    return api


def serve(host: str = "0.0.0.0", port: int = 8000) -> None:
    import uvicorn
    uvicorn.run(app(), host=host, port=port)


def seed_published(ctx) -> int:
    """Sample mode only: load the committed published record (out/records.json) into the local SQLite store, so
    ask and check answer from the same 12 records the public site shows. Nothing is invented: these rows are the
    published file, with the editor's approval already recorded in it."""
    from .store import norm_company
    if ctx.store.fetch("SELECT COUNT(*) AS n FROM records WHERE status = 'approved'")[0]["n"]:
        return 0
    path = ctx.settings.out_dir / "records.json"
    if not path.exists():
        return 0
    rows = json.loads(path.read_text(encoding="utf-8"))
    for r in rows:
        ctx.store.upsert("records", {
            "record_id": r["record_id"], "run_id": "published-record", "company": r["company"],
            "company_norm": norm_company(r["company"]), "cik": r.get("cik"), "event_date": r.get("event_date"),
            "jobs_cut": r.get("jobs_cut"), "jobs_cut_text": r.get("jobs_cut_text"),
            "ai_attribution": r["ai_attribution"], "ai_quote": r.get("ai_quote"), "doc_id": r["record_id"],
            "source_url": r["source_url"], "doc_type": r.get("doc_type"), "quote_verified": True,
            "judge_verdict": "pass", "status": "approved",
            "reviewer_note": "published record, loaded for sample mode"}, "record_id")
        if r.get("cik"):
            ctx.store.upsert("issuers", {"issuer_id": str(r["cik"]), "company": r["company"],
                                         "market": r.get("market") or "", "country": r.get("country"),
                                         "country_source": "published record"}, "issuer_id")
    ctx.log.log("API", "sample_seed", output_summary=f"loaded {len(rows)} published records from {path.name}")
    return len(rows)


def run_samples(folder: Path) -> list[str]:
    """Run every samples/*.input.json and write the matching *.output.json (the real output of that run)."""
    done = []
    for f in sorted(folder.glob("*.input.json")):
        out = handle(json.loads(f.read_text(encoding="utf-8")))
        target = f.with_name(f.name.replace(".input.json", ".output.json"))
        target.write_text(json.dumps(out, indent=1, ensure_ascii=False, default=str), encoding="utf-8")
        done.append(f"{f.name} -> {target.name}: {'ok' if out.get('ok') else 'error'}")
    return done
