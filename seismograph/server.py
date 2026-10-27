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


def run_samples(folder: Path) -> list[str]:
    """Run every samples/*.input.json and write the matching *.output.json (the real output of that run)."""
    done = []
    for f in sorted(folder.glob("*.input.json")):
        out = handle(json.loads(f.read_text(encoding="utf-8")))
        target = f.with_name(f.name.replace(".input.json", ".output.json"))
        target.write_text(json.dumps(out, indent=1, ensure_ascii=False, default=str), encoding="utf-8")
        done.append(f"{f.name} -> {target.name}: {'ok' if out.get('ok') else 'error'}")
    return done
