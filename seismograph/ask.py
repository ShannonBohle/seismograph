"""Ask agent: a plain-English question in, a sourced answer out.

Nemotron (through Nightshift) writes one read-only SQL query over the disclosure-intelligence views; Zetaris runs it;
the answer is the returned rows with their source links. The model never states a fact itself: it only writes the
query, and a guard rejects anything that is not a single SELECT over the allowed views. Asked about a claim ("Company X
cut jobs because of AI"), it returns what the employer itself said, which makes it a claim checker.
"""
from __future__ import annotations

import json
import re

from .agents import ModelCaller

VIEWS = {
    "v_employer_claims": "record_id, company, event_date, jobs_cut, jobs_cut_text, ai_attribution ('explicit'|'partial'|'none'), "
                         "ai_quote (employer's verbatim words), source_url, doc_type, country, market",
    "v_warn_notices": "notice_id, state (two letters), company, company_norm, notice_date, jobs, location, reason (often NULL), source",
    "v_reported_outcomes": "record_id, company, file_date, kind ('savings_achieved'|'savings_expected'|'charges'|'problem'|'reversal'|'other'), "
                           "quote, amount_text, source_url",
    "restructuring_events": "event_id, country, company, announced, sector, event_type, employment_change, description "
                            "(Eurofound's summary, not the employer's words), source_url",
}
FORBIDDEN = re.compile(r"\b(insert|update|delete|drop|alter|create|grant|revoke|truncate|merge|call|exec|attach|copy|"
                       r"pragma|set|use)\b", re.I)

SYSTEM = ("You translate a question about AI-related job cuts into ONE read-only SQL SELECT statement. "
          "Use only these tables and columns:\n" + "\n".join(f"- {t}: {c}" for t, c in VIEWS.items()) +
          "\nRules: a single SELECT, no semicolons, no comments, LIMIT 50 or less, always select source_url when the "
          "table has it, use lower(company) LIKE '%name%' for company names. Never answer the question yourself. "
          'Return JSON only: {"sql": "...", "explanation": "one sentence on what the query returns"}')


class UnsafeQuery(ValueError):
    pass


def guard(sql: str) -> str:
    s = (sql or "").strip().rstrip(";").strip()
    if not re.match(r"(?is)^\s*select\b", s) or ";" in s or "--" in s or "/*" in s:
        raise UnsafeQuery("only a single SELECT statement is allowed")
    if FORBIDDEN.search(s):
        raise UnsafeQuery("the query contains a statement that could change data")
    tables = {t.lower().split(".")[-1] for t in re.findall(r"(?i)\b(?:from|join)\s+([A-Za-z_][\w.]*)", s)}
    if not tables or not tables <= set(VIEWS):
        raise UnsafeQuery(f"only these tables may be queried: {', '.join(VIEWS)}")
    if not re.search(r"(?i)\blimit\s+\d+", s):
        s += " LIMIT 50"
    return s


def ask(ctx, question: str) -> dict:
    call = ModelCaller(ctx.nightshift, ctx.replay, ctx.log)
    data, served = call("Ask", ctx.settings.judge_model, SYSTEM, f"Question: {question}", target="Zetaris", retry=0,
                        purpose=f"write SQL for: {question[:120]}")
    try:
        sql = guard(data.get("sql", ""))
    except UnsafeQuery as exc:
        ctx.log.log("SafetyGuard", "sql_guard", status="rejected", input_summary=str(data.get("sql"))[:300],
                    output_summary=str(exc))
        return {"question": question, "refused": str(exc), "model": served}
    z = ctx.zetaris
    for t in VIEWS:  # qualify view names for Zetaris (seismo_pg.<view>)
        sql = re.sub(rf"(?i)\b(from|join)\s+{t}\b", lambda m: f"{m.group(1)} {z.t(t)}", sql)
    rows = z.query(sql, agent="Ask", purpose=question[:120])
    answer = {"question": question, "explanation": data.get("explanation"), "sql": sql, "rows": rows[:50],
              "row_count": len(rows), "model": served,
              "note": "Rows come straight from the published record through Zetaris; the model only wrote the query."}
    ctx.log.log("Ask", "answer", input_summary=question[:200], output_summary=f"{len(rows)} rows", model=served)
    return answer


def render(answer: dict) -> str:
    if answer.get("refused"):
        return f"Refused: {answer['refused']}"
    lines = [f"Q: {answer['question']}", f"How: {answer.get('explanation') or ''}", f"SQL: {answer['sql']}",
             f"{answer['row_count']} row(s):"]
    for r in answer["rows"][:20]:
        lines.append("  - " + json.dumps(r, ensure_ascii=False, default=str)[:400])
    lines.append(answer["note"])
    return "\n".join(lines)
