"""What employers tell shareholders versus what they tell regulators.

Two measurements, both read through Zetaris:
1. Across every WARN notice loaded: of the notices whose state records a reason, how many cite AI or automation.
2. For each published record where the employer linked its cuts to AI (explicit or partial) in an SEC filing or UK
   announcement: whether a WARN notice from that employer exists in the loaded states near the same date, and whether
   that notice's reason mentions AI.

This compares statements. It does not say which statement is true, and a missing WARN notice can have ordinary causes
(cuts outside the US, below the WARN threshold, or in a state whose data did not load).
"""
from __future__ import annotations

from datetime import date

from .agents import _same_company
from .store import norm_company
from .zetaris import lit

AI_SQL = ("(lower(reason) LIKE '%artificial intelligence%' OR lower(reason) LIKE '%automation%' "
          "OR lower(reason) LIKE '%automated%' OR lower(reason) LIKE '%machine learning%' "
          "OR lower(reason) LIKE '% ai %' OR lower(reason) LIKE 'ai %' OR lower(reason) LIKE '% ai' "
          "OR lower(reason) LIKE '%(ai)%' OR lower(reason) LIKE '% ai-%' OR lower(reason) LIKE '% ai,%' "
          "OR lower(reason) LIKE '% ai.%')")
WINDOW_DAYS = 180


def _cites_ai(reason: str | None) -> bool:
    r = f" {(reason or '').lower()} "
    return any(k in r for k in ("artificial intelligence", "automation", "automated", "machine learning", " ai ",
                                "(ai)", " ai-", " ai,", " ai."))


def warn_reasons(ctx) -> dict:
    z = ctx.zetaris
    per_state = z.query(f"SELECT state, COUNT(*) AS n FROM {z.t('warn_notices')} GROUP BY state",
                        agent="GapAnalyst", purpose="WARN notices loaded, by state")
    reasons = z.query(f"SELECT state, COUNT(*) AS n, SUM(CASE WHEN {AI_SQL} THEN 1 ELSE 0 END) AS ai "
                      f"FROM {z.t('warn_reasons')} GROUP BY state",
                      agent="GapAnalyst", purpose="WARN notices that state a reason, and how many cite AI")
    ai_rows = z.query(
        f"SELECT r.notice_id, r.state, r.reason, w.company, w.notice_date, w.jobs FROM {z.t('warn_reasons')} r "
        f"JOIN {z.t('warn_notices')} w ON r.notice_id = w.notice_id WHERE {AI_SQL.replace('reason', 'r.reason')}",
        agent="GapAnalyst", purpose="the WARN notices whose stated reason cites AI or automation")
    total = sum(int(r["n"]) for r in per_state)
    with_reason = sum(int(r["n"]) for r in reasons)
    ai = sum(int(r["ai"] or 0) for r in reasons)
    return {
        "warn_notices": total,
        "states_loaded": sorted(str(r["state"]) for r in per_state),
        "states_recording_a_reason": sorted(str(r["state"]) for r in reasons if int(r["n"])),
        "notices_with_reason": with_reason,
        "notices_citing_ai": ai,
        "ai_notices": [{k: (str(v) if v is not None else None) for k, v in r.items()} for r in ai_rows],
    }


ERM_AI_SQL = ("(lower(description) LIKE '%artificial intelligence%' OR lower(description) LIKE '%automation%' "
              "OR lower(description) LIKE '%automated%' OR lower(description) LIKE '% ai %' OR lower(description) LIKE '% ai-%' "
              "OR lower(description) LIKE '% ai,%' OR lower(description) LIKE '% ai.%' OR lower(description) LIKE '%(ai)%')")


def erm_summary(ctx) -> dict:
    """European restructuring events (Eurofound ERM): how many summaries mention AI or automation."""
    z = ctx.zetaris
    rows = z.query(f"SELECT country, COUNT(*) AS n, SUM(CASE WHEN {ERM_AI_SQL} THEN 1 ELSE 0 END) AS ai, "
                   f"SUM(CASE WHEN employment_change < 0 THEN -employment_change ELSE 0 END) AS jobs_lost "
                   f"FROM {z.t('restructuring_events')} GROUP BY country",
                   agent="GapAnalyst", purpose="European restructuring events and AI mentions, by country")
    ai_rows = z.query(f"SELECT event_id, country, company, announced, employment_change, event_type, source_url "
                      f"FROM {z.t('restructuring_events')} WHERE {ERM_AI_SQL}",
                      agent="GapAnalyst", purpose="European restructuring events whose summary mentions AI or automation")
    total = sum(int(r["n"]) for r in rows)
    ai = sum(int(r["ai"] or 0) for r in rows)
    return {"events": total, "mention_ai_or_automation": ai, "countries": len(rows),
            "by_country": sorted(({"country": r["country"], "events": int(r["n"]), "mention_ai": int(r["ai"] or 0),
                                   "jobs_lost": int(r["jobs_lost"] or 0)} for r in rows), key=lambda x: -x["events"]),
            "ai_events": [{k: (str(v) if v is not None else None) for k, v in r.items()} for r in ai_rows],
            "headline": (f"{ai:,} of {total:,} European restructuring events in Eurofound's monitor have a summary that "
                         f"mentions AI or automation ({len(rows)} countries)." if total else "")}


def record_gaps(ctx, records: list[dict], states_loaded: list[str]) -> dict[str, dict]:
    """For each record with an AI link, what the WARN record shows for the same employer."""
    z = ctx.zetaris
    out = {}
    for rec in records:
        if rec.get("ai_attribution") not in ("explicit", "partial"):
            continue
        cn = norm_company(rec["company"])
        first = cn.split(" ")[0] if cn else ""
        if not states_loaded:
            out[rec["record_id"]] = {"category": "warn_not_loaded", "notices": []}
            continue
        rows = z.query(
            f"SELECT w.notice_id, w.state, w.company, w.notice_date, w.jobs, r.reason FROM {z.t('warn_notices')} w "
            f"LEFT JOIN {z.t('warn_reasons')} r ON w.notice_id = r.notice_id WHERE w.company_norm LIKE {lit(first + '%')}",
            agent="GapAnalyst", purpose=f"WARN notices and reasons for {rec['company']}") if first else []
        ev = str(rec.get("event_date") or "")[:10]
        notices = []
        for r in rows:
            if not _same_company(cn, norm_company(r["company"])):
                continue
            if ev and r.get("notice_date"):
                try:
                    if abs((date.fromisoformat(str(r["notice_date"])[:10]) - date.fromisoformat(ev)).days) > WINDOW_DAYS:
                        continue
                except ValueError:
                    pass
            notices.append({k: (str(v) if v is not None else None) for k, v in r.items()})
        if not notices:
            cat = "no_warn_found"
        elif any(_cites_ai(n.get("reason")) for n in notices):
            cat = "warn_cites_ai"
        elif any(n.get("reason") for n in notices):
            cat = "warn_reason_without_ai"
        else:
            cat = "warn_without_reason"
        out[rec["record_id"]] = {"category": cat, "notices": notices[:10], "erm_events": _erm_matches(ctx, rec, cn, first)}
    return out


def _erm_matches(ctx, rec: dict, cn: str, first: str) -> list[dict]:
    if not first:
        return []
    z = ctx.zetaris
    try:
        rows = z.query(f"SELECT event_id, country, company, announced, employment_change, event_type, source_url "
                       f"FROM {z.t('restructuring_events')} WHERE company_norm LIKE {lit(first + '%')}",
                       agent="GapAnalyst", purpose=f"European restructuring events for {rec['company']}")
    except Exception:
        return []
    return [{k: (str(v) if v is not None else None) for k, v in r.items()} for r in rows
            if _same_company(cn, norm_company(r["company"]))][:5]


LABELS = {
    "warn_not_loaded": "WARN data not loaded",
    "no_warn_found": "No WARN notice found in the loaded states within 180 days",
    "warn_cites_ai": "WARN notice filed and its stated reason mentions AI or automation",
    "warn_reason_without_ai": "WARN notice filed; its stated reason does not mention AI or automation",
    "warn_without_reason": "WARN notice filed; the state records no reason",
}


def compute(ctx, records: list[dict]) -> dict:
    w = warn_reasons(ctx)
    gaps = record_gaps(ctx, records, w["states_loaded"])
    try:
        erm = erm_summary(ctx)
    except Exception as exc:  # table not registered or empty
        erm = {"events": 0, "headline": "", "error": str(exc)[:200]}
    counts: dict[str, int] = {}
    for g in gaps.values():
        counts[g["category"]] = counts.get(g["category"], 0) + 1
    if w["notices_with_reason"]:
        headline = (f"{w['notices_citing_ai']:,} of {w['notices_with_reason']:,} WARN layoff notices that state a reason "
                    f"cite AI or automation ({len(w['states_recording_a_reason'])} of {len(w['states_loaded'])} "
                    f"states loaded record a reason).")
    elif w["warn_notices"]:
        headline = f"{w['warn_notices']:,} WARN notices loaded; none of the loaded states records a reason."
    else:
        headline = "WARN data not loaded yet."
    result = {"headline": headline, "erm": erm, "warn": w, "records": gaps, "record_counts": counts,
              "labels": LABELS, "window_days": WINDOW_DAYS}
    ctx.log.log("GapAnalyst", "shareholder_vs_regulator", output_summary=headline + f" Records: {counts}")
    return result
