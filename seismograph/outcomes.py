"""Outcomes agent, employer playbook and quarterly scorecards.

Outcomes: for each editor-approved record, the agent searches the same company's LATER SEC
filings and records what the company itself reports about the results (savings, charges,
problems, reversals), quoted verbatim and checked against the source.

Playbook: peer cases for employers considering AI-linked restructuring: what each company
said, what it later reported (including failures), and a confidence rating for each case.

Scorecard: a quarter's summary for investors, analysts and compliance teams.

None of these pages claims that AI caused any job cut or any result.
"""
from __future__ import annotations

import json
import uuid
from datetime import date, datetime, timedelta, timezone
from html import escape

from . import prompts
from .agents import Candidate, ModelCaller, excerpt, in_boilerplate, normspace
from .replay import SampleMissing
from .zetaris import lit

OUTCOME_QUERIES = [
    '"restructuring" "cost savings"',
    '"workforce reduction" "savings"',
    '"reduction in force" "charges"',
]
ACHIEVED = {"savings_achieved", "charges", "problem", "reversal", "other"}
KINDS = ACHIEVED | {"savings_expected"}


def approved_records(ctx) -> list[dict]:
    z = ctx.zetaris
    return z.query(
        f"SELECT record_id, cik, company, event_date, jobs_cut_text, ai_attribution, ai_quote, source_url, memory_id, "
        f"supersedes FROM {z.t('records')} WHERE status = 'approved' AND judge_verdict = 'pass'",
        agent="Outcomes", purpose="approved records to follow up")


def follow_up(ctx, max_docs_per_company: int = 3) -> dict:
    """Find later filings for each approved record's company and extract reported outcomes."""
    from .agents import Scout
    log, edgar, store, z = ctx.log, ctx.edgar, ctx.store, ctx.zetaris
    call = ModelCaller(ctx.nightshift, ctx.replay, log)
    scout = Scout(edgar, store, z, log)
    model = ctx.settings.extractor_model
    stats = {"companies": 0, "documents": 0, "outcomes": 0, "rejected_quotes": 0, "not_in_sample": 0}
    by_company: dict[str, list[dict]] = {}
    for r in approved_records(ctx):
        if r.get("cik") and str(r["cik"]).isdigit():  # SEC filers only; UK records have LEI:<lei>
            by_company.setdefault(str(r["cik"]), []).append(r)
    end = date.today().isoformat()
    for cik, recs in by_company.items():
        rec = sorted(recs, key=lambda x: str(x.get("event_date") or ""))[0]  # earliest announcement
        start = str(rec.get("event_date") or "")[:10] or (date.today() - timedelta(days=365)).isoformat()
        stats["companies"] += 1
        seen, cands = set(), []
        for q in OUTCOME_QUERIES:
            try:
                hits = edgar.search(q, "8-K,10-Q,10-K,6-K", start, end, ciks=f"{int(cik):010d}")
            except Exception as exc:
                log.log("Outcomes", "sec_search", status="failure", input_summary=f"{q} {cik}", output_summary=str(exc)[:200])
                continue
            for h in hits:
                doc_id = f"{h['accession']}:{h['filename']}"
                if not h["filename"] or doc_id in seen or doc_id == "":
                    continue
                if h["url"] in {x["source_url"] for x in recs}:
                    continue  # the announcement itself is not a follow-up
                seen.add(doc_id)
                cands.append(Candidate(h["accession"], h["cik"], h["company"], h["form"] or "", h["file_date"] or "",
                                       h["items"], doc_id, h["url"], h["file_type"] or h["form"] or ""))
        replay = getattr(edgar, "replay", None)
        if replay is not None and replay.sample_mode:
            cands = [c for c in cands if replay.has("sec_doc", {"url": c.url})]
        cands.sort(key=lambda c: c.file_date)
        log.log("Outcomes", "follow_up_search", input_summary=f"{rec['company']} since {start}",
                output_summary=f"{len(cands)} later filings; reading up to {max_docs_per_company}")
        for c in cands[:max_docs_per_company]:
            if not scout.fetch_and_store(c, log.run_id):
                continue
            stats["documents"] += 1
            text = z.query(f"SELECT text FROM {z.t('documents')} WHERE doc_id = {lit(c.doc_id)}",
                           agent="Outcomes", purpose=f"read later filing {c.doc_id} through Zetaris")[0]["text"]
            user = prompts.OUTCOMES_USER.format(
                event_date=rec.get("event_date"), company=rec["company"], label=rec["ai_attribution"],
                cut_text=rec.get("jobs_cut_text") or "", quote=rec.get("ai_quote") or "(no AI quote)", form=c.form,
                doc_type=c.doc_type, file_date=c.file_date, url=c.url, excerpt=excerpt(text))
            try:
                data, served = call("Outcomes", model, prompts.OUTCOMES_SYSTEM, user, target="Verifier", retry=0,
                                    purpose=f"outcomes in {c.doc_id}")
            except SampleMissing as exc:
                stats["not_in_sample"] += 1
                log.log("Outcomes", "not_in_sample", status="skipped", input_summary=c.doc_id, output_summary=str(exc)[:150])
                continue
            except Exception as exc:
                log.log("Outcomes", "model_failed", status="failure", input_summary=c.doc_id, output_summary=str(exc)[:200])
                continue
            if not data.get("relates_to_program"):
                log.log("Outcomes", "not_related", output_summary=f"{c.doc_id}: does not report on the program", model=served)
                continue
            body = normspace(text)
            kept = 0
            for o in (data.get("outcomes") or [])[:5]:
                quote, kind = (o.get("quote") or "").strip(), o.get("kind")
                ok = bool(quote) and kind in KINDS and normspace(quote) in body and not in_boilerplate(text, quote)
                if not ok:
                    stats["rejected_quotes"] += 1
                    log.log("Verifier", "verify_outcome", status="rejected", input_summary=c.doc_id,
                            output_summary=f"dropped {kind!r}: not verbatim, boilerplate or unknown kind")
                    continue
                store.upsert("outcomes", {
                    "outcome_id": f"out_{uuid.uuid4().hex[:12]}", "record_id": rec["record_id"], "cik": cik,
                    "company": rec["company"], "doc_id": c.doc_id, "source_url": c.url, "file_date": c.file_date or None,
                    "kind": kind, "quote": quote, "amount_text": o.get("amount_text"), "verified": True,
                    "model": served, "run_id": log.run_id}, "outcome_id")
                kept += 1
            stats["outcomes"] += kept
            log.log("Verifier", "verify_outcome", input_summary=c.doc_id, output_summary=f"{kept} verified outcome quotes kept",
                    target_agent="Playbook")
    log.log("Outcomes", "run_end", output_summary=json.dumps(stats))
    return stats


# --------------------------------------------------------------------- playbook
def _confidence(outs: list[dict], mem_conf: float | None) -> tuple[str, str]:
    reported = [o for o in outs if o["kind"] in ACHIEVED]
    if reported:
        return "High", "editor-approved announcement and results the company later reported, quoted from its filings"
    if outs:
        return "Medium", "editor-approved announcement; later filings so far give only expected results"
    return "Low", "editor-approved announcement; no later reported results found yet"


def playbook(ctx) -> dict:
    z = ctx.zetaris
    recs = approved_records(ctx)
    outs = z.query(f"SELECT record_id, company, kind, quote, amount_text, source_url, file_date FROM {z.t('outcomes')} "
                   f"WHERE verified = true", agent="Playbook", purpose="verified outcomes for approved records")
    superseded = {r.get("supersedes") for r in recs if r.get("supersedes")}
    cases = {}
    for r in recs:
        if r.get("memory_id") and r["memory_id"] in superseded:
            continue  # a later record for the same event carries the case
        key = str(r.get("cik") or r["company"])
        c = cases.setdefault(key, {"company": r["company"], "records": [], "outcomes": []})
        c["records"].append(r)
    for o in outs:
        for c in cases.values():
            if any(o["record_id"] == r["record_id"] for r in c["records"]) or o["company"] == c["company"]:
                c["outcomes"].append(o)
                break
    result = []
    for c in cases.values():
        mem = None
        for r in c["records"]:
            if r.get("memory_id"):
                m = ctx.hmem.get(r["memory_id"])
                mem = m.confidence if m else mem
        level, why = _confidence(c["outcomes"], mem)
        result.append({**c, "confidence": level, "confidence_reason": why, "memory_confidence": mem})
    order = {"High": 0, "Medium": 1, "Low": 2}
    result.sort(key=lambda c: (order[c["confidence"]], c["company"]))
    out = ctx.settings.out_dir / ("sample" if ctx.settings.sample_mode else "")
    out.mkdir(parents=True, exist_ok=True)
    (out / "playbook.json").write_text(json.dumps(result, indent=1, default=str), encoding="utf-8")
    (out / "playbook.html").write_text(_page("Employer playbook: AI-linked restructurings and what companies later reported",
                                             _playbook_body(result), ctx), encoding="utf-8")
    ctx.log.log("Playbook", "publish", output_summary=f"{len(result)} peer cases -> {out}/playbook.html")
    return {"cases": len(result), "by_confidence": {k: sum(1 for c in result if c["confidence"] == k) for k in order}}


def _playbook_body(cases: list[dict]) -> str:
    names = {"savings_achieved": "Savings reported", "savings_expected": "Savings expected", "charges": "Costs reported",
             "problem": "Problems reported", "reversal": "Reversal", "other": "Other result"}
    parts = ["""<div class=note><b>How to read this.</b> Each case is a company that announced job cuts and talked about AI,
taken from its own SEC filings and approved by an editor. "Later" quotes come from the same company's subsequent filings.
These are the companies' own claims: not advice, not an audit, and not evidence that AI caused any result.
Confidence: <b>High</b> = later reported results found; <b>Medium</b> = only expected results so far; <b>Low</b> = nothing reported yet.</div>"""]
    for c in cases:
        said = "".join(
            f"<p><b>{escape(r['ai_attribution'])}</b> · {escape(str(r.get('event_date') or ''))} · "
            f"{escape(r.get('jobs_cut_text') or 'number not stated')}"
            + (f"<br><q>{escape(r['ai_quote'])}</q>" if r.get("ai_quote") else "")
            + f" <a href=\"{escape(r['source_url'])}\">source</a></p>" for r in c["records"])
        later = "".join(
            f"<li><b>{names.get(o['kind'], o['kind'])}</b> ({escape(str(o.get('file_date') or ''))}): <q>{escape(o['quote'])}</q>"
            f" <a href=\"{escape(o['source_url'])}\">source</a></li>" for o in c["outcomes"]) or "<li class=muted>Nothing reported yet in the filings searched.</li>"
        problems = [o for o in c["outcomes"] if o["kind"] in ("problem", "reversal")]
        parts.append(f"""<article class=card><header><h3>{escape(c['company'])}</h3><span class="tag {c['confidence'].lower()}">{c['confidence']} confidence</span></header>
<p class=muted>{escape(c['confidence_reason'])}</p><h4>What the company said</h4>{said}<h4>What it reported later</h4><ul>{later}</ul>
<p class=muted>{'Failures or problems reported: ' + str(len(problems)) if problems else 'No failures or problems reported in the filings searched.'}</p></article>""")
    return "".join(parts)


# -------------------------------------------------------------------- scorecard
def _quarter_bounds(q: str) -> tuple[str, str]:
    year, n = int(q[:4]), int(q[-1])
    start = date(year, 3 * (n - 1) + 1, 1)
    end = date(year + (n == 4), (3 * n) % 12 + 1, 1) - timedelta(days=1)
    return start.isoformat(), end.isoformat()


def current_quarter() -> str:
    d = date.today()
    return f"{d.year}Q{(d.month - 1) // 3 + 1}"


def scorecard(ctx, quarter: str) -> dict:
    z = ctx.zetaris
    start, end = _quarter_bounds(quarter)
    recs = z.query(
        f"SELECT record_id, cik, company, event_date, jobs_cut, jobs_cut_text, ai_attribution, ai_quote, source_url, status, "
        f"judge_verdict, reviewer_note FROM {z.t('records')} WHERE event_date >= {lit(start)} AND event_date <= {lit(end)}",
        agent="Scorecard", purpose=f"records with events in {quarter}")
    outs = z.query(f"SELECT company, kind FROM {z.t('outcomes')} WHERE verified = true", agent="Scorecard",
                   purpose="reported outcomes")
    pub = [r for r in recs if r["status"] == "approved"]
    reviewed = [r for r in recs if r["status"] in ("approved", "rejected") and r["judge_verdict"] in ("pass", "fail")]
    agree = sum(1 for r in reviewed if (r["judge_verdict"] == "pass") == (r["status"] == "approved"))
    card = {
        "quarter": quarter, "window": [start, end],
        "published": len(pub), "pending_review": sum(1 for r in recs if r["status"] == "pending_review"),
        "rejected": sum(1 for r in recs if r["status"] == "rejected"),
        "by_label": {k: sum(1 for r in pub if r["ai_attribution"] == k) for k in ("explicit", "partial", "none")},
        "jobs_stated": sum(int(r["jobs_cut"]) for r in pub if r.get("jobs_cut")),
        "judge_editor_agreement": f"{agree}/{len(reviewed)}" if reviewed else "no reviews",
        "companies_with_reported_outcomes": sorted({o["company"] for o in outs} & {r["company"] for r in pub}),
        "records": pub,
    }
    out = ctx.settings.out_dir / ("sample" if ctx.settings.sample_mode else "")
    out.mkdir(parents=True, exist_ok=True)
    (out / f"scorecard-{quarter}.json").write_text(json.dumps(card, indent=1, default=str), encoding="utf-8")
    (out / f"scorecard-{quarter}.html").write_text(_page(f"AI job-cut scorecard, {quarter}", _scorecard_body(card), ctx),
                                                   encoding="utf-8")
    ctx.log.log("Scorecard", "publish", output_summary=f"{quarter}: {len(pub)} published -> {out}/scorecard-{quarter}.html")
    return {k: v for k, v in card.items() if k != "records"}


def _scorecard_body(c: dict) -> str:
    stats = [(c["published"], "published records"), (c["by_label"]["explicit"], "employer names AI as a reason"),
             (c["by_label"]["partial"], "AI part of the story, not named as cause"), (c["by_label"]["none"], "cuts not linked to AI"),
             (f"{c['jobs_stated']:,}", "jobs stated as numbers"), (c["judge_editor_agreement"], "Judge and editor agreed")]
    rows = "".join(f"<tr><td>{escape(r['company'])}</td><td>{escape(str(r['event_date'] or ''))}</td><td>{escape(r['ai_attribution'])}</td>"
                   f"<td>{escape(r.get('jobs_cut_text') or 'not stated')}</td><td><a href=\"{escape(r['source_url'])}\">filing</a></td></tr>"
                   for r in c["records"]) or "<tr><td colspan=5 class=muted>No published records with events in this quarter.</td></tr>"
    return (f"<p class=muted>Events dated {c['window'][0]} to {c['window'][1]}. For investors, analysts and compliance teams: "
            f"what employers said, not what caused the cuts.</p><section class=stats>"
            + "".join(f"<div class=stat><b>{v}</b>{escape(l)}</div>" for v, l in stats)
            + f"</section><div class=wrap><table><tr><th>Company</th><th>Event</th><th>Label</th><th>Jobs</th><th>Source</th></tr>{rows}</table></div>"
            + f"<p class=muted>Awaiting review: {c['pending_review']} · rejected by the editor: {c['rejected']} · companies with later reported outcomes: "
            + (escape(', '.join(c['companies_with_reported_outcomes'])) or "none yet") + "</p>")


def _page(title: str, body: str, ctx) -> str:
    mode = "Sample mode (replayed recorded run)" if ctx.settings.sample_mode else "Live run"
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    return f"""<!doctype html><html lang=en><head><meta charset=utf-8><meta name=viewport content="width=device-width,initial-scale=1">
<title>{escape(title)}</title><style>
:root{{--bg:#f7f5f0;--fg:#1d1b17;--muted:#6b665c;--card:#fff;--line:#e3ded3;--accent:#0b5cad;--hi:#1d6b3a;--md:#9a5b0b;--lo:#5b6475}}
@media (prefers-color-scheme:dark){{:root{{--bg:#16140f;--fg:#ece7dc;--muted:#a39d90;--card:#201d17;--line:#38332a;--accent:#7cb4ff;--hi:#6fd08f;--md:#f0b45a;--lo:#a7b0c0}}}}
body{{margin:0;background:var(--bg);color:var(--fg);font:16px/1.5 system-ui,-apple-system,Segoe UI,sans-serif}}
main{{max-width:960px;margin:0 auto;padding:32px 16px}} h1{{font-size:28px;margin:0 0 6px}} h3{{margin:0;font-size:19px}} h4{{margin:14px 0 6px;font-size:14px;text-transform:uppercase;letter-spacing:.04em;color:var(--muted)}}
.muted{{color:var(--muted)}} a{{color:var(--accent)}} q{{font-family:Georgia,serif}} p{{margin:0 0 8px}}
.note,.card,.stat{{background:var(--card);border:1px solid var(--line);border-radius:10px}} .note{{padding:14px 16px;margin:16px 0}}
.card{{padding:18px;margin:14px 0}} .card header{{display:flex;justify-content:space-between;gap:12px;align-items:center}}
.tag{{font-size:12px;font-weight:700;text-transform:uppercase;letter-spacing:.04em;padding:3px 8px;border-radius:99px;border:1px solid}}
.high{{color:var(--hi)}} .medium{{color:var(--md)}} .low{{color:var(--lo)}}
.stats{{display:grid;grid-template-columns:repeat(auto-fit,minmax(140px,1fr));gap:12px;margin:20px 0}} .stat{{padding:14px}} .stat b{{display:block;font-size:26px}}
.wrap{{overflow-x:auto}} table{{border-collapse:collapse;width:100%;background:var(--card);border:1px solid var(--line);min-width:560px}}
th,td{{text-align:left;padding:10px;border-top:1px solid var(--line);font-size:15px}} th{{border-top:none;font-size:13px;text-transform:uppercase;color:var(--muted)}}
</style></head><body><main><h1>{escape(title)}</h1><p class=muted>{mode} · generated {stamp}</p>{body}
<p class=muted>Seismograph · employers' own words from SEC filings · built for the Open Agent Hackathon 2026</p></main></body></html>"""
