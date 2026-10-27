"""Country-by-country comparison of what employers disclosed.

For each country (SEC filers: business-address country; other exchanges: listing market) this counts, through Zetaris,
the documents the agents read, the job cuts they found, how many the editor published, how those employers described
AI's role, the jobs they stated, and the later outcomes they reported.

What this can and cannot say: it compares disclosures in the documents this agent read. Disclosure rules, languages,
search terms, listing populations and sample sizes differ by country, so the figures describe this record. They do not
measure which country is "ahead" in AI, and a low count can mean less disclosure rather than fewer AI-linked cuts.
"""
from __future__ import annotations

import json
from html import escape

from . import issuers
from .review import PUBLISHABLE_SQL, out_dir

REGULATOR_NOTICES = {
    "United States": "Yes: state WARN Act notices (loaded for the states listed on the main page)",
    "United Kingdom": "No: HR1 advance notifications of collective redundancies are not published",
}


def compare(ctx) -> dict:
    z = ctx.zetaris
    filings = z.query(f"SELECT f.cik, f.company, COUNT(*) AS docs FROM {z.t('documents')} d JOIN {z.t('filings')} f "
                      f"ON d.accession = f.accession GROUP BY f.cik, f.company",
                      agent="CountryAnalyst", purpose="documents read, by employer")
    records = z.query(f"SELECT record_id, cik, company, status, judge_verdict, quote_verified, ai_attribution, jobs_cut "
                      f"FROM {z.t('records')} WHERE status <> 'duplicate'",
                      agent="CountryAnalyst", purpose="job cuts found, by employer")
    published_ids = {r["record_id"] for r in z.query(
        f"SELECT record_id FROM {z.t('records')} WHERE {PUBLISHABLE_SQL.format(true='true')}",
        agent="CountryAnalyst", purpose="published records")}
    outcomes = z.query(f"SELECT record_id, kind, COUNT(*) AS n FROM {z.t('outcomes')} WHERE verified = true "
                       f"GROUP BY record_id, kind", agent="CountryAnalyst", purpose="verified later outcomes, by record and kind")
    out_by_rec: dict[str, dict] = {}
    for o in outcomes:
        out_by_rec.setdefault(o["record_id"], {})[o["kind"]] = int(o["n"])
    where = issuers.ensure(ctx, [{"cik": f["cik"], "company": f["company"]} for f in filings] +
                           [{"cik": r["cik"], "company": r["company"]} for r in records])

    def country(cik) -> str:
        i = where.get(str(cik or ""), {})
        from .issuers import country_name
        return country_name(i.get("country")) or "Not known"

    rows: dict[str, dict] = {}

    def row(c):
        return rows.setdefault(c, {"country": c, "markets": set(), "documents_read": 0, "employers_read": 0,
                                   "job_cuts_found": 0, "published": 0, "explicit": 0, "partial": 0, "none": 0,
                                   "jobs_stated": 0, "outcomes_reported": 0, "savings_achieved": 0,
                                   "savings_expected": 0, "problems_or_reversals": 0, "companies": set()})
    for f in filings:
        r = row(country(f["cik"]))
        r["documents_read"] += int(f["docs"])
        r["employers_read"] += 1
        m = where.get(str(f["cik"]), {}).get("market")
        if m:
            r["markets"].add(m)
    for rec in records:
        r = row(country(rec["cik"]))
        r["job_cuts_found"] += 1
        if rec["record_id"] in published_ids:
            r["published"] += 1
            r[rec["ai_attribution"] if rec["ai_attribution"] in ("explicit", "partial", "none") else "none"] += 1
            r["jobs_stated"] += int(rec["jobs_cut"] or 0)
            kinds = out_by_rec.get(rec["record_id"], {})
            r["outcomes_reported"] += sum(kinds.values())
            r["savings_achieved"] += kinds.get("savings_achieved", 0)
            r["savings_expected"] += kinds.get("savings_expected", 0)
            r["problems_or_reversals"] += kinds.get("problem", 0) + kinds.get("reversal", 0)
            r["companies"].add(rec["company"])
    table = []
    for r in sorted(rows.values(), key=lambda x: (-x["documents_read"], x["country"])):
        r["markets"] = sorted(r["markets"])
        r["companies"] = sorted(r["companies"])
        r["ai_linked_share_of_published"] = round((r["explicit"] + r["partial"]) / r["published"], 2) if r["published"] else None
        r["job_cuts_per_100_documents"] = round(100 * r["job_cuts_found"] / r["documents_read"], 1) if r["documents_read"] else None
        r["public_regulator_layoff_notices"] = REGULATOR_NOTICES.get(r["country"], "None connected; none found public")
        table.append(r)
    try:
        from .gap import erm_summary
        erm = erm_summary(ctx)
    except Exception:
        erm = {"events": 0, "by_country": []}
    result = {"countries": table, "erm": erm, "caveat": __doc__.split("What this can and cannot say: ")[1].strip()}
    out = out_dir(ctx)
    (out / "countries.json").write_text(json.dumps(result, indent=1, ensure_ascii=False, default=str), encoding="utf-8")
    from .outcomes import _page
    (out / "countries.html").write_text(_page("Country comparison: what employers disclosed", _body(result), ctx),
                                        encoding="utf-8")
    ctx.log.log("CountryAnalyst", "publish", output_summary=f"{len(table)} countries -> {out}/countries.html")
    return {"countries": [{k: r[k] for k in ("country", "documents_read", "job_cuts_found", "published",
                                              "explicit", "partial", "none")} for r in table]}


def _body(result: dict) -> str:
    head = ("<tr><th>Country</th><th>Documents read</th><th>Job cuts found</th><th>Published</th><th>Explicit</th>"
            "<th>Partial</th><th>None</th><th>AI-linked share of published</th><th>Jobs stated</th><th>Later outcomes</th>"
            "<th>Savings reported achieved / expected</th><th>Problems or reversals reported</th>"
            "<th>Public regulator layoff notices</th></tr>")
    cells = []
    for r in result["countries"]:
        share = "" if r["ai_linked_share_of_published"] is None else f"{r['ai_linked_share_of_published']:.0%}"
        cells.append(f"<tr><td><b>{escape(r['country'])}</b><br><span class=muted>{escape('; '.join(r['markets']))}</span></td>"
                     f"<td>{r['documents_read']}</td><td>{r['job_cuts_found']}</td><td>{r['published']}</td>"
                     f"<td>{r['explicit']}</td><td>{r['partial']}</td><td>{r['none']}</td><td>{share}</td>"
                     f"<td>{r['jobs_stated']:,}</td><td>{r['outcomes_reported']}</td>"
                     f"<td>{r['savings_achieved']} / {r['savings_expected']}</td><td>{r['problems_or_reversals']}</td>"
                     f"<td class=muted>{escape(r['public_regulator_layoff_notices'])}</td></tr>")
    return (""
            f"<div class=note><b>Read this first.</b> {escape(result['caveat'])}</div>"
            f"<div style='overflow-x:auto'><table>{head}{''.join(cells)}</table></div>"
            "<p class=muted>Country: business-address country for SEC filers; listing market for announcements from the FCA "
            "(UK), ASX (Australia), BSE (India), TDnet (Japan) and CNINFO (China). Explicit, partial and none are the labels "
            "for published records only. Savings, problems and reversals count verified statements in the employers' later "
            "filings about the same restructuring; they are not amounts and do not show that AI produced them. Quotes from Japanese and Chinese documents are kept in the original language.</p>"
            + (_erm_table(result["erm"]) if result.get("erm", {}).get("events") else "") +
            "<style>table{border-collapse:collapse;width:100%;font-size:14px}th,td{border-bottom:1px solid var(--line);"
            "padding:8px;text-align:left;vertical-align:top}</style>")


def _erm_table(erm: dict) -> str:
    rows = "".join(f"<tr><td>{escape(r['country'])}</td><td>{r['events']}</td><td>{r['mention_ai']}</td>"
                   f"<td>{r['jobs_lost']:,}</td></tr>" for r in erm["by_country"])
    return ("<h2 style='margin-top:28px'>Europe: restructuring events reported in Eurofound's monitor</h2>"
            "<p class=muted>Europe publishes no WARN-style notices. Eurofound's European Restructuring Monitor compiles "
            "large restructuring events from national media and company websites; its summaries are Eurofound's words, "
            "and an AI mention may come from a union or a journalist rather than the employer.</p>"
            "<table><tr><th>Country</th><th>Events</th><th>Summary mentions AI or automation</th><th>Planned job losses</th></tr>"
            f"{rows}</table>")
