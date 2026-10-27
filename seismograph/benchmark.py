"""Export the Judge benchmark: every record a human editor reviewed, with the source passage, the draft, the Judge's
verdict and the editor's decision. Published openly so others (including model builders) can measure a judge model
against human editorial labels. Only public filing text is included; no personal data.
"""
from __future__ import annotations

import json

from .agents import Judge
from .review import out_dir


def export(ctx) -> dict:
    z = ctx.zetaris
    rows = z.query(f"SELECT r.record_id, r.company, r.event_date, r.ai_attribution, r.ai_quote, r.jobs_cut_text, r.source_url, "
                   f"r.doc_type, r.judge_verdict, r.judge_model, r.extractor_model, r.status, r.reviewer_note, d.text "
                   f"FROM {z.t('records')} r JOIN {z.t('documents')} d ON d.doc_id = r.doc_id "
                   f"WHERE r.status IN ('approved', 'rejected')",
                   agent="Benchmark", purpose="human-reviewed records with their source text")
    out = out_dir(ctx) / "judge_benchmark.jsonl"
    with out.open("w", encoding="utf-8") as fh:
        for r in rows:
            passage = Judge.passage(r.pop("text") or "", r.get("ai_quote") or r.get("jobs_cut_text"))
            item = {**r, "source_passage": passage,
                    "human_label": "approve" if r["status"] == "approved" else "reject",
                    "judge_label": "approve" if r["judge_verdict"] == "pass" else "reject"}
            fh.write(json.dumps(item, ensure_ascii=False, default=str) + "\n")
    agree = sum(1 for r in rows if (r["status"] == "approved") == (r["judge_verdict"] == "pass"))
    ctx.log.log("Benchmark", "export", output_summary=f"{len(rows)} labelled items -> {out}")
    return {"items": len(rows), "judge_editor_agreement": f"{agree}/{len(rows)}", "file": str(out)}
