"""Human review (the editor) and publishing.

Publishing rule, enforced in code: a record is published only if the quote passed the
verbatim check, the Judge passed it, and a human approved it; a record whose source was flagged by the
safety scan (possible prompt injection) also needs the editor's note to say "safety checked".
"""
from __future__ import annotations

import csv
import json
from datetime import datetime, timezone
from html import escape
from pathlib import Path

from .agents import MemorySteward

PUBLISHABLE_SQL = ("status = 'approved' AND judge_verdict = 'pass' AND quote_verified = {true} "
                   "AND (COALESCE(gap_flags, '') NOT LIKE '%SAFETY:%' OR lower(COALESCE(reviewer_note, '')) LIKE '%safety checked%')")


def out_dir(ctx) -> Path:
    """Live results go to out/; SAMPLE_MODE results go to out/sample/ so they never overwrite live ones."""
    out = ctx.settings.out_dir / "sample" if ctx.settings.sample_mode else ctx.settings.out_dir
    out.mkdir(parents=True, exist_ok=True)
    return out


def pending(ctx) -> list[dict]:
    return ctx.store.fetch("SELECT * FROM records WHERE status = 'pending_review' ORDER BY created_at")


def show(r: dict) -> str:
    flags = json.loads(r.get("gap_flags") or "[]")
    warn = json.loads(r.get("warn_match") or "[]")
    lines = [
        f"{r['record_id']}  {r['company']}  ({r.get('event_date') or 'date not stated'})",
        f"  attribution: {r['ai_attribution']}   jobs: {r.get('jobs_cut_text') or 'not stated'}",
        f"  quote: {r.get('ai_quote') or '-'}",
        f"  source: {r['source_url']}  [{r.get('doc_type')}]",
        f"  quote verified: {bool(r['quote_verified'])}   judge: {r['judge_verdict']} ({r.get('judge_model') or '-'})",
        f"  WARN matches: {len(warn)}   flags: {'; '.join(flags) or 'none'}",
    ]
    return "\n".join(lines)


def decide(ctx, record_id: str, approve: bool, note: str = "") -> dict:
    rows = ctx.store.fetch("SELECT * FROM records WHERE record_id = %s", (record_id,))
    if not rows:
        raise KeyError(record_id)
    r = rows[0]
    publishable = bool(r["quote_verified"]) and r["judge_verdict"] == "pass"
    status = "approved" if approve and publishable else "rejected"
    if approve and not publishable:
        note = (note + " | approval recorded as a label only: record failed automated checks").strip(" |")
    ctx.store.execute("UPDATE records SET status = %s, reviewer_note = %s, reviewed_at = %s WHERE record_id = %s",
                      (status, note, datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"), record_id))
    if r.get("memory_id"):
        MemorySteward(ctx.hmem, ctx.log).learn_from_review(r["memory_id"], approve, note)
    ctx.log.log("Editor", "human_review", input_summary=record_id,
                output_summary=f"{'approve' if approve else 'reject'} -> status {status}. {note}"[:300],
                human_label="approve" if approve else "reject", judge_verdict=r["judge_verdict"])
    return {**r, "status": status, "human_label": approve}


def interactive(ctx) -> None:
    rows = pending(ctx)
    if not rows:
        print("Nothing waiting for review.")
        return
    for r in rows:
        print("\n" + show(r))
        ans = input("  approve [a] / reject [r] / skip [s] / quit [q]: ").strip().lower()
        if ans == "q":
            break
        if ans in ("a", "r"):
            note = input("  note (optional): ").strip()
            decide(ctx, r["record_id"], ans == "a", note)


def publish(ctx) -> dict:
    z = ctx.zetaris
    true = "true"
    rows = z.query(
        f"SELECT record_id, company, cik, event_date, jobs_cut, jobs_cut_text, ai_attribution, ai_quote, source_url, "
        f"doc_type, judge_model, extractor_model, warn_match, gap_flags, supersedes, memory_id, reviewed_at "
        f"FROM {z.t('records')} WHERE {PUBLISHABLE_SQL.format(true=true)} ORDER BY event_date DESC",
        agent="Publisher", purpose="approved, verified records for the public record")
    out = out_dir(ctx)
    superseded = {r.get("supersedes") for r in rows if r.get("supersedes")}
    warn_loaded = bool(z.query(f"SELECT COUNT(*) AS n FROM {z.t('warn_notices')}", agent="Publisher",
                               purpose="is WARN data loaded now")[0].get("n") not in (0, "0", None))
    STALE = "WARN notices not checked: no state WARN data is loaded yet"
    for r in rows:
        for k in ("warn_match", "gap_flags"):
            try:
                r[k] = json.loads(r.get(k) or "[]")
            except (TypeError, ValueError):
                r[k] = []
        if warn_loaded:  # drafted before WARN was loaded; the "Told regulators" line now carries the current check
            r["gap_flags"] = [f for f in r["gap_flags"] if f != STALE]
        if r.get("memory_id") and r["memory_id"] in superseded:
            r["gap_flags"].insert(0, "Superseded: a later record for the same event adds detail (see the other entry)")
    (out / "records.json").write_text(json.dumps(rows, indent=1, default=str, ensure_ascii=False), encoding="utf-8")
    with (out / "records.csv").open("w", newline="", encoding="utf-8") as fh:
        cols = ["record_id", "company", "event_date", "jobs_cut", "jobs_cut_text", "ai_attribution", "ai_quote",
                "source_url", "doc_type", "gap_flags"]
        w = csv.DictWriter(fh, fieldnames=cols, extrasaction="ignore")
        w.writeheader()
        for r in rows:
            w.writerow({**r, "gap_flags": " | ".join(r["gap_flags"])})
    totals = ctx.zetaris.query(
        f"SELECT status, judge_verdict, ai_attribution, COUNT(*) AS n FROM {z.t('records')} "
        f"GROUP BY status, judge_verdict, ai_attribution", agent="Publisher", purpose="pipeline totals for the report")
    from . import gap, issuers
    where = issuers.ensure(ctx, rows)
    for r in rows:
        i = where.get(str(r.get("cik") or ""), {})
        r["market"], r["country"] = i.get("market"), i.get("country")
    gaps = gap.compute(ctx, rows)
    (out / "gap.json").write_text(json.dumps(gaps, indent=1, default=str, ensure_ascii=False), encoding="utf-8")
    (out / "records.json").write_text(json.dumps(rows, indent=1, default=str, ensure_ascii=False), encoding="utf-8")
    (out / "index.html").write_text(render(rows, totals, ctx, gaps), encoding="utf-8")
    site = copy_to_site(ctx, out)
    ctx.log.log("Publisher", "publish", output_summary=f"{len(rows)} records -> {out}/index.html, records.json, records.csv"
                + (f"; site copy in {site}" if site else ""))
    countries = sorted({r["country"] for r in rows if r.get("country")})
    return {"published": len(rows), "countries": countries, "headline": gaps["headline"],
            "erm_headline": (gaps.get("erm") or {}).get("headline"),
            "record_gaps": gaps["record_counts"]}


def copy_to_site(ctx, out: Path) -> Path | None:
    """Live runs only: copy the published pages to docs/, which GitHub Pages serves."""
    if ctx.settings.sample_mode:
        return None
    import shutil
    site = ctx.settings.out_dir.parent / "docs"
    site.mkdir(exist_ok=True)
    for f in out.iterdir():
        if f.is_file() and f.suffix in (".html", ".json", ".csv"):
            shutil.copy2(f, site / f.name)
    (site / ".nojekyll").write_text("", encoding="utf-8")
    return site


def render(rows: list[dict], totals: list[dict], ctx, gaps: dict | None = None) -> str:
    gaps = gaps or {"headline": "", "records": {}, "labels": {}, "warn": {}}
    def count(**kw):
        return sum(int(t["n"]) for t in totals if all(str(t.get(k)) == v for k, v in kw.items()))
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    mode = "Sample mode (replayed recorded run)" if ctx.settings.sample_mode else "Live run"
    explicit = sum(1 for r in rows if r["ai_attribution"] == "explicit")
    jobs = sum(int(r["jobs_cut"]) for r in rows if r.get("jobs_cut"))
    cards = []
    for r in rows:
        flags = "".join(f"<li>{escape(f)}</li>" for f in r["gap_flags"]) or "<li>No gaps flagged</li>"
        warn = r["warn_match"]
        warn_txt = (f"{len(warn)} WARN notice(s): " + "; ".join(
            f"{escape(w.get('state') or '')} {escape(str(w.get('notice_date') or ''))} {escape(str(w.get('jobs') or '?'))} jobs"
            for w in warn[:4])) if warn else "No WARN notice matched"
        g = gaps["records"].get(r["record_id"])
        gap_txt = f"<p class=gap><b>Told regulators:</b> {escape(gaps['labels'].get(g['category'], ''))}</p>" if g else ""
        where = " · ".join(x for x in (r.get("country"), r.get("market")) if x)
        quote = f"<blockquote>&ldquo;{escape(r['ai_quote'])}&rdquo;</blockquote>" if r.get("ai_quote") else \
            "<p class=muted>The employer did not connect these cuts to AI.</p>"
        cards.append(f"""
<article class=card>
  <header><h3>{escape(r['company'])}</h3><span class="tag {escape(r['ai_attribution'])}">{escape(r['ai_attribution'])}</span></header>
  <p class=meta>{escape(str(r.get('event_date') or 'date not stated'))} · {escape(r.get('jobs_cut_text') or 'number not stated')}{(' · ' + escape(where)) if where else ''}</p>
  {quote}
  {gap_txt}
  <p class=src>Source: <a href="{escape(r['source_url'])}">{escape(r.get('doc_type') or 'filing')}</a> · {warn_txt}</p>
  <ul class=flags>{flags}</ul>
</article>""")
    return f"""<!doctype html><html lang=en><head><meta charset=utf-8><meta name=viewport content="width=device-width,initial-scale=1">
<title>Seismograph record</title><style>
:root{{--bg:#f7f5f0;--fg:#1d1b17;--muted:#6b665c;--card:#fff;--line:#e3ded3;--ex:#b42318;--pa:#b54708;--no:#475467;--accent:#0b5cad}}
@media (prefers-color-scheme:dark){{:root{{--bg:#16140f;--fg:#ece7dc;--muted:#a39d90;--card:#201d17;--line:#38332a;--ex:#f97066;--pa:#fdb022;--no:#98a2b3;--accent:#7cb4ff}}}}
body{{margin:0;background:var(--bg);color:var(--fg);font:16px/1.5 system-ui,-apple-system,Segoe UI,sans-serif}}
main{{max-width:880px;margin:0 auto;padding:32px 16px}} h1{{font-size:28px;margin:0 0 4px}} .muted,.meta{{color:var(--muted)}}
.stats{{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:12px;margin:24px 0}}
.stat{{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:14px}} .stat b{{display:block;font-size:26px}}
.card{{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:18px;margin:14px 0}}
.card header{{display:flex;justify-content:space-between;gap:12px;align-items:center}} .card h3{{margin:0;font-size:19px}}
.tag{{font-size:12px;font-weight:600;text-transform:uppercase;letter-spacing:.04em;padding:3px 8px;border-radius:99px;border:1px solid}}
.explicit{{color:var(--ex)}} .partial{{color:var(--pa)}} .none{{color:var(--no)}}
blockquote{{margin:12px 0;padding:8px 14px;border-left:3px solid var(--line);font-family:Georgia,serif}}
a{{color:var(--accent)}} .src{{font-size:14px}} .flags{{font-size:14px;color:var(--muted);margin:8px 0 0;padding-left:18px}}
.headline{{font-size:20px;font-weight:600;border-left:4px solid var(--ex);padding:6px 14px;margin:20px 0}} .gap{{font-size:14px;margin:6px 0}}
footer{{font-size:13px;color:var(--muted);margin-top:32px;border-top:1px solid var(--line);padding-top:16px}}
</style></head><body><main>
<h1>Seismograph: AI-attributed job cuts, in employers' own words</h1>
<p class=muted>{mode} · generated {stamp}. Each entry quotes the employer verbatim and links the filing it came from. The labels describe what the employer said, not whether AI caused the cuts.</p>
{f'<p class=headline>{escape(gaps["headline"])}</p>' if gaps.get("headline") else ''}
{f'<p class=headline>{escape(gaps["erm"]["headline"])} <span class=muted>(Eurofound summaries compiled from media, not employer statements)</span></p>' if (gaps.get("erm") or {}).get("headline") else ''}
<section class=stats>
<div class=stat><b>{len(rows)}</b>published records</div>
<div class=stat><b>{len({r.get("country") for r in rows if r.get("country")})}</b>countries (employer business address)</div>
<div class=stat><b>{explicit}</b>employer says AI is a reason</div>
<div class=stat><b>{jobs:,}</b>jobs stated in those filings</div>
<div class=stat><b>{count(status='pending_review')}</b>awaiting editor review</div>
<div class=stat><b>{count(judge_verdict='fail') + count(judge_verdict='not_reached')}</b>held back by the checks</div>
<div class=stat><b>{count(status='rejected')}</b>rejected by the editor</div>
</section>
{''.join(cards) or '<p>No approved records yet.</p>'}
<footer>Published only when the quote matched the source verbatim, the Judge agent passed it and a human editor approved it.
Data: SEC EDGAR filings; UK regulatory announcements from the FCA National Storage Mechanism; WARN notices via Big Local News warn-scraper (states loaded only).
"Told regulators" compares what the employer said with WARN notices filed by the same employer within 180 days; it does not say which statement is true. Built for the Open Agent Hackathon 2026.</footer>
</main></body></html>"""


def evaluate(ctx) -> dict:
    """Agreement between the Judge's verdict and the human editor's decision (Cohen's kappa)."""
    z = ctx.zetaris
    rows = z.query(f"SELECT judge_verdict, status, reviewer_note FROM {z.t('records')} "
                   f"WHERE status IN ('approved', 'rejected') AND judge_verdict IN ('pass', 'fail')",
                   agent="Evaluator", purpose="judge verdicts vs human review decisions")
    pairs = []
    for r in rows:
        human = r["status"] == "approved" or "approval recorded as a label only" in (r.get("reviewer_note") or "")
        pairs.append((r["judge_verdict"] == "pass", human))
    n = len(pairs)
    if not n:
        result = {"n": 0, "note": "No reviewed records yet. Review some with `python -m seismograph review`."}
    else:
        agree = sum(1 for j, h in pairs if j == h) / n
        pj = sum(1 for j, _ in pairs if j) / n
        ph = sum(1 for _, h in pairs if h) / n
        pe = pj * ph + (1 - pj) * (1 - ph)
        kappa = (agree - pe) / (1 - pe) if pe < 1 else float("nan")
        tp = sum(1 for j, h in pairs if j and h)
        fp = sum(1 for j, h in pairs if j and not h)
        fn = sum(1 for j, h in pairs if not j and h)
        result = {"n": n, "accuracy": round(agree, 3), "cohens_kappa": None if kappa != kappa else round(kappa, 3),
                  "judge_pass_but_human_rejected": fp, "judge_fail_but_human_approved": fn, "both_pass": tp}
    ctx.log.log("Evaluator", "judge_vs_human", output_summary=json.dumps(result))
    out = out_dir(ctx)
    (out / "judge_eval.json").write_text(json.dumps(result, indent=1), encoding="utf-8")
    return result
