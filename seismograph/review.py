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


BIG_PICTURE_CONTEXT = """
<section id=picture><h2>The big picture</h2>
<p class=lede>Headlines say AI is taking jobs. Economy-wide data mostly says not yet, except at the entry level. Until now nobody could check which story an employer tells, or to whom. This record makes that checkable.</p>
<div class=ctx>
<div><span class=num>120,136</span><b>The noise.</b> US job cuts this year where employers cited AI: about 1 in 5 of all cuts, more than double 2025's 54,836. The count records reasons announced, not what drove each cut.<small>Challenger, Gray &amp; Christmas, 1 Oct 2026 (Jan–Sep)</small></div>
<div><span class=num>“Stability”</span><b>Not major disruption.</b> Yale's Budget Lab calls AI's labor-market effect largely speculative, and warns some firms may be AI-washing cuts with other causes.<small>Yale Budget Lab, via <i>Fortune</i>, 2 Feb 2026</small></div>
<div><span class=num>+4%</span><b>Exposed jobs grew.</b> Jobs in the occupations most exposed to AI grew from 2019 to 2024, while less exposed occupations trimmed payrolls.<small>Federal Reserve Bank of Chicago, Jul 2026</small></div>
<div><span class=num>−13%</span><b>Except the young.</b> Workers aged 22 to 25 in the most AI-exposed jobs saw a 13% relative decline in employment: the early-career “canaries in the coal mine.”<small>Brynjolfsson, Chandar &amp; Chen, Stanford, Aug 2025</small></div>
</div>
"""

BIG_PICTURE_POLICY = """
<div class=two>
<div><h3>Why the records disagree</h3><ul>
<li>Investors reward AI stories; most WARN forms have no box for AI.</li>
<li>WARN covers large US sites 60 days ahead; filings cover worldwide cuts.</li>
<li>There is no shared definition of an “AI layoff.”</li>
<li>News counts announcements, not causes.</li></ul></div>
<div><h3>Who can act</h3><ul>
<li><b>Congress:</b> the AI-Related Job Impacts Clarity Act (quarterly reports to the Labor Department).</li>
<li><b>States:</b> New York's WARN question on AI, with definitions and enforcement.</li>
<li><b>Labor Department and BLS:</b> restore national layoff tracking, cut in 2013.</li>
<li><b>SEC:</b> consistent AI workforce disclosure; it already pursues AI-washing.</li>
<li><b>Employers, newsrooms, researchers:</b> one story to every audience; cite the record; publish methods.</li></ul></div>
</div>
<h3>Trends to watch <span class=muted>(hypotheses, not predictions)</span></h3>
<ol class=trends>
<li><b>The disclosure gap widens.</b> AI cited to investors more than doubled in a year; WARN forms still almost never say it.</li>
<li><b>“AI as strategy” is where AI-washing hides.</b> Watch partial labels turn explicit, or turn into reversals.</li>
<li><b>Entry-level work moves first.</b> The early-career signal is the place to look before economy-wide data moves.</li>
<li><b>A recession is the real test.</b> AI's labor effect may show first in job mix and unemployment duration.</li>
<li><b>Disclosure rules spread.</b> New York's AI question and a federal bill would let this gap be measured at the source.</li>
</ol>
</section>
"""


def render(rows: list[dict], totals: list[dict], ctx, gaps: dict | None = None) -> str:
    gaps = gaps or {"headline": "", "records": {}, "labels": {}, "warn": {}}
    def count(**kw):
        return sum(int(t["n"]) for t in totals if all(str(t.get(k)) == v for k, v in kw.items()))
    stamp = datetime.now(timezone.utc).strftime("%d %B %Y, %H:%M UTC")
    mode = "Sample mode: a replay of a recorded run" if ctx.settings.sample_mode else "Live record"
    by_label = {k: sum(1 for r in rows if r["ai_attribution"] == k) for k in ("explicit", "partial", "none")}
    jobs = sum(int(r["jobs_cut"]) for r in rows if r.get("jobs_cut"))
    countries = sorted({r.get("country") for r in rows if r.get("country")})
    warn = gaps.get("warn") or {}
    n_reason, n_ai = warn.get("notices_with_reason"), warn.get("notices_citing_ai")
    erm = gaps.get("erm") or {}
    label_text = {"explicit": "Says AI is a reason", "partial": "AI named beside the cuts", "none": "No AI link"}
    total = max(1, len(rows))
    bar = "".join(f'<span class="seg {k}" style="flex:{v}" title="{label_text[k]}: {v}"></span>' for k, v in by_label.items() if v)
    legend = "".join(f'<span class=lg><i class="dot {k}"></i>{label_text[k]} <b>{v}</b></span>' for k, v in by_label.items())
    chips = "".join(f'<button class=chip data-country="{escape(c)}">{escape(c)} <b>{sum(1 for r in rows if r.get("country") == c)}</b></button>'
                    for c in countries)
    cards = []
    for r in rows:
        flags = "".join(f"<li>{escape(f)}</li>" for f in r["gap_flags"] if not f.startswith("no matching WARN notice"))
        w = r["warn_match"]
        warn_txt = (f"{len(w)} WARN notice(s): " + "; ".join(
            f"{escape(x.get('state') or '')} {escape(str(x.get('notice_date') or ''))} {escape(str(x.get('jobs') or '?'))} jobs"
            for x in w[:4])) if w else "No WARN notice matched"
        g = gaps["records"].get(r["record_id"])
        gap_txt = (f'<p class=told><b>Told regulators:</b> {escape(gaps["labels"].get(g["category"], ""))}</p>') if g else ""
        quote = (f'<blockquote>&ldquo;{escape(r["ai_quote"])}&rdquo;</blockquote>' if r.get("ai_quote") else
                 '<p class=noai>The employer did not connect these cuts to AI.</p>')
        lab = r["ai_attribution"]
        cards.append(f"""
<article class="card {escape(lab)}" data-label="{escape(lab)}" data-country="{escape(r.get('country') or '')}" data-text="{escape((r['company'] + ' ' + (r.get('ai_quote') or '') + ' ' + (r.get('jobs_cut_text') or '')).lower())}">
  <div class=top><span class="pill {escape(lab)}">{escape(label_text.get(lab, lab))}</span><span class=where>{escape(r.get('country') or '')}{(' · ' + escape(r.get('market'))) if r.get('market') else ''}</span></div>
  <h3>{escape(r['company'])}</h3>
  <p class=jobs><span class=n>{escape(r.get('jobs_cut_text') or 'Number not stated')}</span><span class=date>{escape(str(r.get('event_date') or 'date not stated'))}</span></p>
  {quote}
  {gap_txt}
  <p class=src><a href="{escape(r['source_url'])}" rel=noopener>Read the source: {escape((r.get('doc_type') or 'filing')[:90])}</a>{('<br><span>' + warn_txt + '</span>') if (w or not g) else ''}</p>
  {f'<ul class=flags>{flags}</ul>' if flags else ''}
</article>""")
    rc = gaps.get("record_counts") or {}
    n_linked = by_label["partial"] + by_label["explicit"]
    n_nowarn = rc.get("no_warn_found", 0)
    n_reason_s = f"{n_reason:,}" if n_reason else "the"
    n_ai_s = f"{n_ai:,}" if n_ai is not None else "few"
    erm_find = (f'<div><b>Europe, in Eurofound\'s words.</b> {escape(erm["headline"])} These are keyword matches in media-compiled summaries; some are automation-equipment makers, so this overstates AI-linked cuts.</div>' if erm.get("headline") else "")
    gap_big = (f'<div class=big><span class=num>{n_ai:,} <small>of</small> {n_reason:,}</span>'
               f'<span class=cap>US WARN layoff notices that give a reason cites AI or automation</span></div>') if n_reason else ""
    return f"""<!doctype html><html lang=en><head><meta charset=utf-8><meta name=viewport content="width=device-width,initial-scale=1">
<title>AI Seismograph</title>
<link rel=preconnect href="https://fonts.googleapis.com"><link rel=preconnect href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Libre+Baskerville:wght@400;700&family=Public+Sans:wght@400;600;700&display=swap" rel=stylesheet>
<style>
:root{{--bg:#F6F3EC;--fg:#1B1F2A;--muted:#5d6370;--card:#FFFDF8;--line:#D8D2C4;--ink:#1B1F2A;--paper:#F6F3EC;--ex:#B42318;--pa:#C2410C;--no:#1D4E89;--accent:#1D4E89;--hero:#1B1F2A;--herofg:#F6F3EC;--peach:#E8A27C}}
@media (prefers-color-scheme:dark){{:root:not([data-theme=light]){{--bg:#12151c;--fg:#ECE7DC;--muted:#a4a9b4;--card:#1b1f29;--line:#323847;--ex:#f97066;--pa:#f59e6b;--no:#7cb4ff;--accent:#7cb4ff}}}}
:root[data-theme=dark]{{--bg:#12151c;--fg:#ECE7DC;--muted:#a4a9b4;--card:#1b1f29;--line:#323847;--ex:#f97066;--pa:#f59e6b;--no:#7cb4ff;--accent:#7cb4ff}}
*{{box-sizing:border-box}} body{{margin:0;background:var(--bg);color:var(--fg);font:16px/1.55 'Public Sans',system-ui,sans-serif}}
a{{color:var(--accent)}} .wrap{{max-width:1120px;margin:0 auto;padding:0 16px}}
.hero{{background:var(--hero);color:var(--herofg);padding:28px 0 96px}}
.logo{{display:flex;align-items:center;margin:6px 0 8px -8px;max-width:100%;overflow:hidden}} .tag{{font-family:'Libre Baskerville',Georgia,serif;font-style:italic;font-size:clamp(20px,2.6vw,28px);color:var(--peach);margin:0 0 10px}} .logo h1{{margin:0 10px}} .logo .ai{{color:var(--peach)}}.logo svg{{height:clamp(40px,6vw,64px);flex:none}} .logo .l{{width:clamp(50px,7vw,90px)}} .logo .r{{width:clamp(90px,13vw,170px)}}.logo polyline{{fill:none;stroke:#C2410C;stroke-width:3;stroke-linejoin:round;stroke-linecap:round;stroke-dasharray:400;stroke-dashoffset:400;animation:draw 1.6s ease-out forwards}}.logo .r polyline{{animation-delay:.5s}} .nav{{margin-top:22px}}

@keyframes draw{{to{{stroke-dashoffset:0}}}} @media (prefers-reduced-motion:reduce){{.logo polyline{{animation:none;stroke-dashoffset:0}}}}
.kicker{{font-size:13px;letter-spacing:.18em;text-transform:uppercase;color:var(--peach);font-weight:700}}
h1{{font-family:'Libre Baskerville',Georgia,serif;font-size:clamp(34px,6vw,64px);line-height:1.05;margin:8px 0 6px}}
.sub{{max-width:760px;color:#d9d4c7;font-size:18px;margin:0}}
.nav{{display:flex;flex-wrap:wrap;gap:8px;position:relative}} .nav a{{color:var(--herofg);text-decoration:none;border:1px solid #4a5163;border-radius:99px;padding:6px 14px;font-size:14px}}
.nav a:hover{{background:#2b3140}}
.bigs{{display:grid;grid-template-columns:repeat(auto-fit,minmax(240px,1fr));gap:16px;margin:-64px 0 0;position:relative}}
.big{{background:var(--card);color:var(--fg);border:1px solid var(--line);border-radius:16px;padding:20px 22px;box-shadow:0 8px 24px rgba(0,0,0,.08)}}
.big .num{{display:block;font-family:'Libre Baskerville',Georgia,serif;font-size:44px;font-weight:700;color:var(--pa);line-height:1.1}}
.big .num small{{font-size:22px;color:var(--muted)}} .big .cap{{display:block;color:var(--muted);font-size:15px;margin-top:6px}}
.big.first{{border-top:6px solid var(--pa)}}
section h2{{font-family:'Libre Baskerville',Georgia,serif;font-size:26px;margin:40px 0 12px}}
.bar{{display:flex;height:16px;border-radius:99px;overflow:hidden;background:var(--line);margin:8px 0}} .seg{{display:block}}
.seg.explicit,.dot.explicit{{background:var(--ex)}} .seg.partial,.dot.partial{{background:var(--pa)}} .seg.none,.dot.none{{background:var(--no)}}
.lg{{margin-right:18px;font-size:14px;color:var(--muted);white-space:nowrap}} .dot{{display:inline-block;width:10px;height:10px;border-radius:50%;margin-right:6px}}
.tools{{display:flex;flex-wrap:wrap;gap:8px;align-items:center;margin:14px 0}}
.chip{{font:inherit;font-size:14px;border:1px solid var(--line);background:var(--card);color:var(--fg);border-radius:99px;padding:6px 12px;cursor:pointer}}
.chip.on{{background:var(--fg);color:var(--bg);border-color:var(--fg)}} .chip b{{opacity:.7;margin-left:4px}}
input[type=search]{{font:inherit;font-size:15px;padding:8px 12px;border:1px solid var(--line);border-radius:10px;background:var(--card);color:var(--fg);min-width:220px;flex:1}}
.grid{{display:grid;grid-template-columns:repeat(auto-fill,minmax(330px,1fr));gap:16px}}
.card{{background:var(--card);border:1px solid var(--line);border-radius:14px;padding:18px 20px;border-top:5px solid var(--no);display:flex;flex-direction:column}}
.card.partial{{border-top-color:var(--pa)}} .card.explicit{{border-top-color:var(--ex)}}
.top{{display:flex;justify-content:space-between;gap:8px;align-items:center;font-size:13px}} .where{{color:var(--muted);text-align:right}}
.pill{{font-weight:700;font-size:12px;text-transform:uppercase;letter-spacing:.05em;padding:3px 9px;border-radius:99px;border:1.5px solid currentColor}}
.pill.explicit{{color:var(--ex)}} .pill.partial{{color:var(--pa)}} .pill.none{{color:var(--no)}}
.card h3{{font-size:20px;margin:10px 0 2px}} .jobs{{margin:0 0 6px;display:flex;justify-content:space-between;gap:10px;font-size:14px}}
.jobs .n{{font-weight:700}} .jobs .date{{color:var(--muted);white-space:nowrap}}
blockquote{{font-family:'Libre Baskerville',Georgia,serif;font-size:16px;line-height:1.6;margin:8px 0;padding:10px 14px;border-left:4px solid var(--pa);background:rgba(194,65,12,.06);border-radius:0 8px 8px 0}}
.noai{{color:var(--muted);font-style:italic;margin:8px 0}} .told{{font-size:14px;margin:6px 0}}
.src{{font-size:13px;color:var(--muted);margin:auto 0 0;padding-top:8px}} .flags{{font-size:13px;color:var(--muted);margin:6px 0 0;padding-left:18px}}
.empty{{display:none;color:var(--muted)}}
.stats{{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:10px}}
.stat{{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:12px 14px;font-size:14px;color:var(--muted)}} .stat b{{display:block;font-size:24px;color:var(--fg)}}
.method{{display:grid;grid-template-columns:repeat(auto-fit,minmax(240px,1fr));gap:14px;font-size:15px}}
.method div{{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:14px 16px}} .method b{{display:block;margin-bottom:4px}}
h3{{font-size:19px;margin:26px 0 10px}} .lede{{font-size:18px;max-width:860px}}
.ctx,.find{{display:grid;grid-template-columns:repeat(auto-fit,minmax(240px,1fr));gap:14px}}
.ctx div,.find div{{background:var(--card);border:1px solid var(--line);border-radius:14px;padding:16px 18px;font-size:15px}}
.ctx .num{{display:block;font-family:'Libre Baskerville',Georgia,serif;font-size:30px;font-weight:700;color:var(--no);margin-bottom:6px}}
.ctx b,.find b{{display:block;margin-bottom:4px}} .ctx small{{display:block;color:var(--muted);font-size:12px;margin-top:8px}}
.find div{{border-left:5px solid var(--pa)}}
.two{{display:grid;grid-template-columns:repeat(auto-fit,minmax(320px,1fr));gap:20px}} .two ul,.trends{{padding-left:20px;font-size:15px}} .two li,.trends li{{margin:6px 0}}
footer{{font-size:13px;color:var(--muted);margin:40px 0;border-top:1px solid var(--line);padding-top:16px}}
</style></head><body>
<header class=hero><div class=wrap>
<div class=kicker>{mode} · updated {stamp}</div>
<div class=logo><svg class=l viewBox="0 0 90 60" aria-hidden=true><polyline points="0,32 46,32 54,26 60,38 66,32 90,32"/></svg><h1><span class=ai>AI</span> Seismograph</h1><svg class=r viewBox="0 0 170 60" aria-hidden=true><polyline points="0,32 14,32 22,18 30,48 38,6 46,58 54,14 62,44 70,26 78,36 88,32 170,32"/></svg></div>
<p class=tag>When AI advances, labor markets tremble.</p>
<p class=sub>What employers themselves say about AI and job cuts, quoted word for word from official filings, with a link to every source. The labels describe what the employer said, not whether AI caused the cuts.</p>

<nav class=nav><a href="#picture">Big picture</a><a href="#record">The record</a><a href="countries.html">Countries</a><a href="playbook.html">What happened next</a><a href="scorecard-2026Q3.html">Scorecard</a><a href="spenders.html">Big spenders</a><a href="records.csv">Download CSV</a><a href="https://github.com/ShannonBohle/seismograph">Code &amp; method</a></nav>
</div>

</header>
<main class=wrap>
<div class=bigs>
{gap_big.replace('class=big', 'class="big first"')}
<div class=big><span class=num>{by_label['explicit']} <small>of</small> {len(rows)}</span><span class=cap>published employers say AI is a reason for their cuts. {by_label['partial']} name AI beside the cuts.</span></div>
<div class=big><span class=num>{len(countries)}</span><span class=cap>countries in the published record: {escape(', '.join(countries))}</span></div>
</div>

{BIG_PICTURE_CONTEXT}
<h3>What this record shows so far</h3>
<div class=find>
<div><b>AI is named beside the cuts, never as the reason.</b> Of {len(rows)} published records, {by_label['partial']} put AI next to the cuts (for example “transformation enabled by AI”), {by_label['none']} make no AI link, and {by_label['explicit']} say AI did the work of the people cut.</div>
<div><b>Investors hear it; layoff notices don't.</b> {n_linked} {'employer' if n_linked == 1 else 'employers'} linked cuts to AI in shareholder filings; {'none has' if n_nowarn == n_linked else str(n_linked - n_nowarn) + ' have'} a matching US WARN notice. Across {n_reason_s} WARN notices that give a reason, {n_ai_s} cite AI.</div>
{erm_find}
<div><b>Small numbers, now measurable.</b> These counts come from the first days of runs. The point is that what employers say, and to whom, can now be checked and counted every day.</div>
</div>
{BIG_PICTURE_POLICY}

<section id=record><h2>The record</h2>
<div class=bar>{bar}</div><div>{legend}</div>
<div class=tools>
<button class="chip on" data-label="">All labels</button><button class=chip data-label="partial">AI named beside the cuts</button><button class=chip data-label="none">No AI link</button><button class=chip data-label="explicit">Says AI is a reason</button>
</div>
<div class=tools><button class="chip on" data-country="">All countries</button>{chips}<input type=search id=q placeholder="Search companies and quotes" aria-label="Search the record"></div>
<div class=grid id=cards>{''.join(cards) or '<p>No approved records yet.</p>'}</div>
<p class=empty id=empty>No records match these filters.</p>
</section>

<section><h2>How the record is kept honest</h2>
<div class=stats>
<div class=stat><b>{len(rows)}</b>published records</div>
<div class=stat><b>{jobs:,}</b>jobs stated by number in those filings</div>
<div class=stat><b>{count(judge_verdict='fail') + count(judge_verdict='not_reached')}</b>held back by the checks</div>
<div class=stat><b>{count(status='rejected')}</b>rejected by the human editor</div>
<div class=stat><b>{count(status='pending_review')}</b>awaiting the editor</div>
</div>
<div class=method style="margin-top:14px">
<div><b>Word for word</b>Every quote is checked against the source by code; paraphrases are sent back and never published.</div>
<div><b>A second opinion</b>An independent Judge model reviews every draft; its agreement with the editor is measured and published.</div>
<div><b>A human editor</b>Nothing is published without an editor's approval, enforced in code.</div>
<div><b>Told regulators</b>Each AI-linked record is compared with US WARN layoff notices filed by the same employer within 180 days. It compares statements; it does not say which is true.</div>
</div>
{f'<p class=told style="margin-top:14px">{escape(erm["headline"])} Keyword matches in Eurofound summaries compiled from media, not employer statements; some are automation-equipment makers.</p>' if erm.get("headline") else ''}
</section>
<footer>Context figures: Challenger, Gray &amp; Christmas; Yale Budget Lab; Federal Reserve Bank of Chicago; Stanford Digital Economy Lab. Record sources: SEC EDGAR; FCA National Storage Mechanism (UK); ASX, BSE, TDnet, CNINFO, AMF and CVM open data; US state WARN notices via Big Local News warn-scraper; Eurofound European Restructuring Monitor.
Built by Shannon Bohle for the Open Agent Hackathon 2026 with Zetaris, Meterless Nightshift and H-MEM, and NVIDIA Nemotron. <a href="records.json">JSON</a> · <a href="gap.json">gap.json</a></footer>
</main>
<script>
(function(){{
  var label='', country='', q=document.getElementById('q'), cards=[].slice.call(document.querySelectorAll('.card')), empty=document.getElementById('empty');
  function apply(){{ var t=(q.value||'').toLowerCase(), shown=0;
    cards.forEach(function(c){{ var ok=(!label||c.dataset.label===label)&&(!country||c.dataset.country===country)&&(!t||c.dataset.text.indexOf(t)>-1);
      c.style.display=ok?'':'none'; if(ok) shown++; }}); empty.style.display=shown?'none':'block'; }}
  document.querySelectorAll('[data-label]').forEach(function(b){{ if(b.tagName!=='BUTTON') return; b.addEventListener('click',function(){{
    label=b.dataset.label; document.querySelectorAll('button[data-label]').forEach(function(x){{x.classList.toggle('on',x===b)}}); apply(); }}); }});
  document.querySelectorAll('button[data-country]').forEach(function(b){{ b.addEventListener('click',function(){{
    country=b.dataset.country; document.querySelectorAll('button[data-country]').forEach(function(x){{x.classList.toggle('on',x===b)}}); apply(); }}); }});
  q.addEventListener('input',apply);
}})();
</script>
</body></html>"""


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
