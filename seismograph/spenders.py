"""The largest AI spenders: capital spending, AI-attributed job cuts and profit, side by side.

Financials come from the SEC's XBRL company-facts API (data.sec.gov), as reported in each
company's annual reports. Companies do not report "AI investment" as a separate figure, so
this uses TOTAL capital expenditure (purchases of property and equipment), and says so.
Job-cut records come from this agent's own verified records for the same companies.
Nothing here claims that spending caused any job cut.
"""
from __future__ import annotations

import json
from datetime import date, datetime, timezone
from html import escape

from .zetaris import lit

# Large US-listed technology companies whose capital spending is dominated by AI infrastructure
# in 2025-2026 by their own accounts. The report ranks them by reported capital expenditure
# and keeps the top 10.
CANDIDATES = ["MSFT", "GOOGL", "AMZN", "META", "ORCL", "AAPL", "NVDA", "TSLA", "IBM", "CRM", "AVGO", "CRWV"]

CAPEX_TAGS = ["PaymentsToAcquirePropertyPlantAndEquipment", "PaymentsToAcquireProductiveAssets",
              "PaymentsToAcquirePropertyPlantAndEquipmentAndIntangibleAssets"]
NET_INCOME_TAGS = ["NetIncomeLoss", "ProfitLoss"]
REVENUE_TAGS = ["Revenues", "RevenueFromContractWithCustomerExcludingAssessedTax"]
TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
FACTS_URL = "https://data.sec.gov/api/xbrl/companyfacts/CIK{cik:010d}.json"

AI_QUERIES = [
    '"artificial intelligence" "workforce reduction"',
    '"artificial intelligence" "reduction in force"',
    '"artificial intelligence" "restructuring" "employees"',
    '"automation" "workforce reduction"',
]


def _annual(facts: dict, tags: list[str], years: int = 3) -> tuple[str | None, list[dict]]:
    """Latest fiscal-year values (10-K/20-F, ~12-month duration) for the first tag with recent data."""
    best_tag, best = None, []
    for tag in tags:
        units = facts.get("facts", {}).get("us-gaap", {}).get(tag, {}).get("units", {}).get("USD", [])
        by_end: dict[str, dict] = {}
        for f in units:
            if f.get("form") not in ("10-K", "20-F", "10-K/A") or not f.get("start") or not f.get("end"):
                continue
            days = (date.fromisoformat(f["end"]) - date.fromisoformat(f["start"])).days
            if not 330 <= days <= 400:
                continue
            prev = by_end.get(f["end"])
            if prev is None or f.get("filed", "") > prev.get("filed", ""):
                by_end[f["end"]] = f
        rows = [by_end[k] for k in sorted(by_end, reverse=True)[:years]]
        if rows and (not best or rows[0]["end"] > best[0]["end"]):
            best_tag, best = tag, rows
    return best_tag, [{"end": r["end"], "value": r["val"], "accession": r.get("accn"), "filed": r.get("filed"),
                       "form": r.get("form")} for r in best]


def fetch(ctx) -> list[dict]:
    """Fetch and store annual financials for the candidates; return one summary per company."""
    edgar, replay, log, store = ctx.edgar, ctx.replay, ctx.log, ctx.store
    req = {"url": TICKERS_URL, "subset": CANDIDATES}
    if replay.sample_mode:
        tickers = replay.load("sec_json", req)
    else:
        raw = edgar._get(TICKERS_URL).json()
        tickers = {v["ticker"]: {"cik": v["cik_str"], "title": v["title"]} for v in raw.values()
                   if v["ticker"] in CANDIDATES}
        replay.save("sec_json", req, tickers)
    out = []
    for t in CANDIDATES:
        if t not in tickers:
            log.log("Spenders", "ticker_lookup", status="failure", input_summary=t, output_summary="not in SEC ticker list")
            continue
        cik, title = int(tickers[t]["cik"]), tickers[t]["title"]
        url = FACTS_URL.format(cik=cik)
        try:
            if replay.sample_mode:
                trimmed = replay.load("sec_json", {"url": url})
            else:
                facts = edgar._get(url).json()
                trimmed = {}
                for name, tags in (("capex", CAPEX_TAGS), ("net_income", NET_INCOME_TAGS), ("revenue", REVENUE_TAGS)):
                    tag, rows = _annual(facts, tags)
                    trimmed[name] = {"tag": tag, "rows": rows}
                replay.save("sec_json", {"url": url}, trimmed)  # only the extracted figures are recorded
        except Exception as exc:
            log.log("Spenders", "xbrl_fetch", status="failure", input_summary=f"{t} {url}", output_summary=str(exc)[:200])
            continue
        for metric, data in trimmed.items():
            for r in data["rows"]:
                store.upsert("company_financials", {
                    "fact_id": f"{cik}:{metric}:{r['end']}", "cik": str(cik), "ticker": t, "company": title,
                    "metric": metric, "xbrl_tag": data["tag"], "period_end": r["end"], "value_usd": r["value"],
                    "form": r["form"], "accession": r["accession"], "filed": r["filed"],
                    "source_url": url}, "fact_id")
        latest_capex = (trimmed["capex"]["rows"] or [{}])[0].get("value")
        log.log("Spenders", "xbrl_fetch", input_summary=f"{t} ({title})",
                output_summary=f"capex tag {trimmed['capex']['tag']}; latest FY capex {latest_capex}")
        out.append({"ticker": t, "cik": str(cik), "company": title, "latest_capex": latest_capex or 0})
    out.sort(key=lambda r: r["latest_capex"], reverse=True)
    return out


def top10(ctx) -> list[dict]:
    z = ctx.zetaris
    rows = z.query(
        f"SELECT ticker, cik, company, metric, period_end, value_usd, xbrl_tag, accession FROM {z.t('company_financials')}",
        agent="Spenders", purpose="annual capex, net income and revenue for the candidates")
    by = {}
    for r in rows:
        c = by.setdefault(r["ticker"], {"ticker": r["ticker"], "cik": str(r["cik"]), "company": r["company"],
                                        "capex": [], "net_income": [], "revenue": []})
        c[r["metric"]].append({"end": str(r["period_end"])[:10], "value": float(r["value_usd"]),
                               "tag": r["xbrl_tag"], "accession": r["accession"]})
    for c in by.values():
        for m in ("capex", "net_income", "revenue"):
            c[m].sort(key=lambda x: x["end"], reverse=True)
    ranked = sorted(by.values(), key=lambda c: c["capex"][0]["value"] if c["capex"] else 0, reverse=True)
    return ranked[:10]


def scan_ciks(companies: list[dict]) -> str:
    return ",".join(f"{int(c['cik']):010d}" for c in companies)


def report(ctx, companies: list[dict]) -> dict:
    z = ctx.zetaris
    ciks = [str(int(c["cik"])) for c in companies]
    recs = z.query(
        f"SELECT record_id, cik, company, event_date, jobs_cut, jobs_cut_text, ai_attribution, ai_quote, source_url, "
        f"status, judge_verdict FROM {z.t('records')} WHERE cik IN ({', '.join(lit(c) for c in ciks)})",
        agent="Spenders", purpose="this agent's records for the top-10 spenders")
    docs = z.query(
        f"SELECT f.cik, COUNT(*) AS n FROM {z.t('documents')} d JOIN {z.t('filings')} f ON d.accession = f.accession "
        f"WHERE f.cik IN ({', '.join(lit(c) for c in ciks)}) GROUP BY f.cik",
        agent="Spenders", purpose="how many of each company's filings the agent read")
    read = {str(d["cik"]): int(d["n"]) for d in docs}
    for c in companies:
        c["records"] = [r for r in recs if str(r["cik"]) == str(int(c["cik"]))]
        c["published"] = [r for r in c["records"] if r["status"] == "approved"]
        c["pending"] = [r for r in c["records"] if r["status"] == "pending_review"]
        c["documents_read"] = read.get(str(int(c["cik"])), 0)
    out = ctx.settings.out_dir / ("sample" if ctx.settings.sample_mode else "")
    out.mkdir(parents=True, exist_ok=True)
    (out / "spenders.json").write_text(json.dumps(companies, indent=1, default=str), encoding="utf-8")
    (out / "spenders.html").write_text(render(companies, ctx), encoding="utf-8")
    ctx.log.log("Spenders", "report", output_summary=f"{len(companies)} companies -> {out}/spenders.html")
    return {"companies": len(companies), "records": sum(len(c["records"]) for c in companies)}


def _b(v: float | None) -> str:
    if v is None:
        return "–"
    return f"{'−' if v < 0 else ''}${abs(v) / 1e9:,.1f}B"


def render(companies: list[dict], ctx) -> str:
    rows = []
    for i, c in enumerate(companies, 1):
        cap = c["capex"]
        ni = c["net_income"]
        def cell(series):
            return "<br>".join(f"<span class=yr>FY to {escape(x['end'])}</span> {_b(x['value'])}" for x in series[:3]) or "not reported"
        if c["published"]:
            cuts = "".join(
                f"<p><b>{escape(r['ai_attribution'])}</b> · {escape(str(r['event_date'] or ''))} · "
                f"{escape(r.get('jobs_cut_text') or 'number not stated')}"
                + (f"<br><q>{escape(r['ai_quote'])}</q>" if r.get("ai_quote") else "")
                + f" <a href=\"{escape(r['source_url'])}\">source</a></p>" for r in c["published"])
        else:
            cuts = (f"<p class=muted>No editor-approved AI-attributed job-cut record. Filings read: {c['documents_read']}"
                    + (f"; {len(c['pending'])} awaiting review" if c["pending"] else "") + ".</p>")
        rows.append(f"<tr><td class=rank>{i}</td><td><b>{escape(c['company'])}</b><br><span class=muted>{escape(c['ticker'])}</span></td>"
                    f"<td>{cell(cap)}</td><td>{cell(ni)}</td><td>{cuts}</td></tr>")
    mode = "Sample mode (replayed recorded run)" if ctx.settings.sample_mode else "Live run"
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    return f"""<!doctype html><html lang=en><head><meta charset=utf-8><meta name=viewport content="width=device-width,initial-scale=1">
<title>Seismograph: AI spenders</title><style>
:root{{--bg:#f7f5f0;--fg:#1d1b17;--muted:#6b665c;--card:#fff;--line:#e3ded3;--accent:#0b5cad}}
@media (prefers-color-scheme:dark){{:root{{--bg:#16140f;--fg:#ece7dc;--muted:#a39d90;--card:#201d17;--line:#38332a;--accent:#7cb4ff}}}}
body{{margin:0;background:var(--bg);color:var(--fg);font:16px/1.5 system-ui,-apple-system,Segoe UI,sans-serif}}
main{{max-width:1180px;margin:0 auto;padding:32px 16px}} h1{{font-size:28px;margin:0 0 6px}} .muted,.yr{{color:var(--muted)}} .yr{{font-size:13px}}
.wrap{{overflow-x:auto}} table{{border-collapse:collapse;width:100%;background:var(--card);border:1px solid var(--line);border-radius:10px;min-width:820px}}
th,td{{text-align:left;vertical-align:top;padding:12px;border-top:1px solid var(--line);font-size:15px}} th{{font-size:13px;text-transform:uppercase;letter-spacing:.04em;color:var(--muted);border-top:none}}
td.rank{{font-weight:700;color:var(--muted)}} q{{font-family:Georgia,serif}} a{{color:var(--accent)}} p{{margin:0 0 8px}}
.note{{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:14px 16px;margin:16px 0}}
</style></head><body><main>
<h1>The largest AI spenders: capital spending, AI-attributed job cuts and profit</h1>
<p class=muted>{mode} · generated {stamp}</p>
<div class=note><b>Read this first.</b> These figures sit side by side; nothing here claims that spending caused any job cut, or the reverse.
Companies do not report AI investment separately, so "capital expenditure" is total purchases of property and equipment as reported in each annual report (SEC XBRL data). It is not a measure of AI investment, and nothing here says the spending caused the cuts.
Job-cut entries are this agent's editor-approved records, quoted from each company's own filings; a blank means none was found in the filings searched, not that none happened.</div>
<div class=wrap><table><tr><th>#</th><th>Company</th><th>Capital expenditure</th><th>Net income</th><th>AI-attributed job cuts (employer's words)</th></tr>
{''.join(rows)}</table></div>
<p class=muted>Ranked by latest fiscal-year capital expenditure among: {', '.join(CANDIDATES)}. Source: SEC company facts API (data.sec.gov); fiscal years end on different dates for different companies.</p>
</main></body></html>"""
