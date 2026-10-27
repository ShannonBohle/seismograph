"""Command line: python -m seismograph <command>"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import review as rv
from .context import build
from .pipeline import Pipeline


def cmd_check(ctx, args) -> int:
    ok = True
    s = ctx.settings
    print(f"mode: {'SAMPLE' if s.sample_mode else 'LIVE'}")
    try:
        rows = ctx.zetaris.query("SELECT 1 AS one", agent="Check", purpose="Zetaris reachable")
        print(f"Zetaris: ok ({rows})")
        for t in ("filings", "documents", "warn_notices", "warn_reasons", "restructuring_events", "records", "issuers",
                  "company_financials", "outcomes"):
            n = ctx.zetaris.query(f"SELECT COUNT(*) AS n FROM {ctx.zetaris.t(t)}", agent="Check",
                                  purpose=f"{t} visible through Zetaris")
            print(f"  {ctx.zetaris.t(t)}: {n[0].get('n')} rows")
    except Exception as exc:
        ok = False
        print(f"Zetaris: FAILED - {exc}")
    if ctx.nightshift:
        status, ids = ctx.nightshift.status()
        print(f"Nightshift: {status}; {len(ids)} model ids: {', '.join(ids[:12])}")
        ok &= status == "online"
        if status == "online":
            try:
                c = ctx.nightshift.chat(s.judge_model, "Reply with JSON only.", 'Return {"ok": true}', agent="Check")
                print(f"  test call on {s.judge_model}: served by {c.served_model} (nemotron={c.is_nemotron})")
                ctx.log.log("Check", "model_call", model=c.served_model, output_summary=c.text[:80],
                            requested_model=s.judge_model, nemotron_served=c.is_nemotron)
            except Exception as exc:
                ok = False
                print(f"  test call FAILED - {exc}")
    print(f"H-MEM: {len(ctx.hmem.all())} memories, {ctx.hmem.ledger_count()} ledger entries")
    print(f"log: {ctx.log.path}")
    return 0 if ok else 1


def schedule_daily(args) -> int:
    """Daily run: discover and verify new filings, then republish. Records still wait for the editor."""
    import subprocess
    root = Path(__file__).resolve().parent.parent
    if sys.platform == "win32":
        script = root / "scripts" / "daily.cmd"
        script.parent.mkdir(exist_ok=True)
        py = '".venv\\Scripts\\python.exe" -m seismograph'
        lines = [f"{py} {c} >> logs\\daily.txt 2>&1" for c in DAILY_STEPS(args.max_docs)]
        script.write_text('@echo off\r\ncd /d "' + str(root) + '"\r\nif not exist logs mkdir logs\r\n'
                          'echo ===== %date% %time% >> logs\\daily.txt\r\n' + "\r\n".join(lines + SITE_STEPS) + "\r\n",
                          encoding="ascii")
        cmd = ["schtasks", "/Create", "/SC", "DAILY", "/TN", "Seismograph-Daily", "/TR", f'"{script}"',
               "/ST", args.time, "/F"]
        r = subprocess.run(cmd, capture_output=True, text=True)
        print((r.stdout or r.stderr).strip())
        print(f"Daily task 'Seismograph-Daily' runs {script} at {args.time}. Check with: schtasks /Query /TN Seismograph-Daily")
        return r.returncode
    hh, mm = args.time.split(":")
    steps = " ; ".join(f".venv/bin/python -m seismograph {c} >> logs/daily.txt 2>&1" for c in DAILY_STEPS(args.max_docs))
    print(f"Add this line with `crontab -e`:\n{int(mm)} {int(hh)} * * * cd {root} && {steps}")
    return 0


# Publish the public pages: commit only the generated pages (docs/, out/) and push them to GitHub Pages.
SITE_STEPS = [
    'git add docs out >> logs\\daily.txt 2>&1',
    'git commit -m "Daily update %date%" >> logs\\daily.txt 2>&1',
    'git push >> logs\\daily.txt 2>&1',
]


def DAILY_STEPS(max_docs: int) -> list[str]:
    """Every feature, once a day. Each step runs even if an earlier one fails; new records still wait for the editor."""
    return [
        "check",
        "warn-all",                                        # WARN notices, all states warn-scraper supports
        "erm --days 14",                                   # European restructuring events (Eurofound ERM)
        f"run --days 3 --max-docs {max_docs}",             # new SEC filings (8-K, 6-K)
        "run --days 3 --forms 20-F --max-docs 5",          # foreign companies' annual reports filed with the SEC
        "run --days 3 --forms 40-F --max-docs 5",          # Canadian companies' annual reports filed with the SEC
        "uk --days 3 --max-docs 10",                       # UK regulatory announcements (FCA NSM)
        "foreign --source all --days 2 --max-docs 8",      # ASX, BSE, TDnet, CNINFO announcements
        "spenders --scan --days 7 --max-docs 10",          # top-10 AI spenders: refresh figures, scan new filings
        "outcomes",                                        # later filings of approved records
        "playbook",
        "scorecard",                                       # current quarter
        "countries",                                       # country-by-country comparison
        "publish",                                         # also copies the pages to docs/ for GitHub Pages
    ]


def main(argv=None) -> int:
    for stream in (sys.stdout, sys.stderr):  # Windows consoles default to cp1252; filings include Japanese, Chinese, French
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    p = argparse.ArgumentParser(prog="seismograph")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("check", help="check Zetaris, Nightshift, the evidence store and H-MEM")
    sub.add_parser("init-db", help="create the evidence-store tables")
    w = sub.add_parser("load-warn", help="load warn-scraper CSV files into the evidence store")
    w.add_argument("files", nargs="+", type=Path)
    wa = sub.add_parser("warn-all", help="scrape WARN notices for every state warn-scraper supports, then load them")
    wa.add_argument("--data-dir", type=Path, default=Path("warn"))
    wa.add_argument("--workers", type=int, default=6)
    sd = sub.add_parser("schedule-daily", help="register a daily run with Windows Task Scheduler (or print a cron line)")
    sd.add_argument("--time", default="06:30")
    sd.add_argument("--max-docs", type=int, default=25)
    r = sub.add_parser("run", help="discover, extract, verify, judge, remember, corroborate")
    r.add_argument("--days", type=int, default=60)
    r.add_argument("--end", default=None, help="end date YYYY-MM-DD (default today)")
    r.add_argument("--forms", default="8-K,6-K")
    r.add_argument("--max-docs", type=int, default=10)
    rv_p = sub.add_parser("review", help="editor review of pending records")
    rv_p.add_argument("--list", action="store_true")
    rv_p.add_argument("--approve", nargs="*", default=[])
    rv_p.add_argument("--reject", nargs="*", default=[])
    rv_p.add_argument("--note", default="")
    sub.add_parser("publish", help="write out/index.html, records.json and records.csv from approved records")
    sub.add_parser("evaluate", help="Judge vs human agreement (Cohen's kappa)")
    sub.add_parser("memory", help="show H-MEM memories and recent ledger entries")
    oc = sub.add_parser("outcomes", help="follow up approved records: what companies later reported")
    oc.add_argument("--max-docs", type=int, default=3, help="later filings to read per company")
    sub.add_parser("playbook", help="employer playbook: peer cases, reported results and confidence ratings")
    sc = sub.add_parser("scorecard", help="quarterly AI job-cut scorecard")
    sc.add_argument("--quarter", default=None, help="e.g. 2026Q3 (default: current quarter)")
    sp = sub.add_parser("spenders", help="top-10 AI spenders: capex, AI-attributed job cuts and net income side by side")
    sp.add_argument("--scan", action="store_true", help="also search these companies' filings for AI-attributed cuts")
    sp.add_argument("--days", type=int, default=730)
    sp.add_argument("--max-docs", type=int, default=20)
    uk = sub.add_parser("uk-probe", help="check whether the FCA National Storage Mechanism search answers")
    uk.add_argument("--keyword", default="redundancies")
    fr = sub.add_parser("foreign", help="run the agents on exchange announcements: ASX, BSE, TDnet, CNINFO, AMF, CVM (Brazil), DART, EDINET")
    fr.add_argument("--source", default="all", choices=["all", "asx", "bse", "tdnet", "cninfo", "amf", "cvm", "dart", "edinet"])
    fr.add_argument("--days", type=int, default=7)
    fr.add_argument("--end", default=None)
    fr.add_argument("--max-docs", type=int, default=8, help="per source")
    ak = sub.add_parser("ask", help="ask a question in plain English; answered from the published record via Zetaris")
    ak.add_argument("question")
    ck = sub.add_parser("check-claim", help="check a claim about a company against what the employer itself said")
    ck.add_argument("claim")
    sub.add_parser("benchmark", help="export the Judge benchmark (human-reviewed records) to out/judge_benchmark.jsonl")
    sv = sub.add_parser("serve", help="HTTP API: POST /run (binds 0.0.0.0:8000)")
    sv.add_argument("--port", type=int, default=8000)
    sub.add_parser("samples", help="run samples/*.input.json and write their real outputs")
    em = sub.add_parser("erm", help="load European restructuring events (Eurofound ERM)")
    em.add_argument("--days", type=int, default=120)
    sub.add_parser("countries", help="country-by-country comparison of what employers disclosed")
    ukr = sub.add_parser("uk", help="run the agents on UK regulatory announcements (FCA National Storage Mechanism)")
    ukr.add_argument("--days", type=int, default=90)
    ukr.add_argument("--end", default=None)
    ukr.add_argument("--max-docs", type=int, default=10)
    args = p.parse_args(argv)

    if args.cmd == "schedule-daily":
        return schedule_daily(args)
    if args.cmd == "serve":
        from .server import serve
        serve(port=args.port)
        return 0
    if args.cmd == "samples":
        from .server import run_samples
        print("\n".join(run_samples(Path(__file__).resolve().parent.parent / "samples")))
        return 0
    if args.cmd in ("ask", "check-claim"):
        from .server import handle
        out = handle({"task": "ask" if args.cmd == "ask" else "check", "question": getattr(args, "question", None), "claim": getattr(args, "claim", None)})
        if out.get("ok"):
            from .ask import render
            print(render(out["result"]))
        else:
            print(json.dumps(out, indent=1, default=str))
        return 0 if out.get("ok") else 1
    ctx = build(echo=args.cmd not in ("review",))
    if args.cmd == "check":
        return cmd_check(ctx, args)
    if args.cmd == "init-db":
        print("evidence store tables ready")
        return 0
    if args.cmd == "load-warn":
        from . import warn
        print(warn.load(ctx.store, args.files, ctx.log))
        return 0
    if args.cmd == "warn-all":
        from . import warn
        results = warn.scrape_all(args.data_dir, ctx.log, workers=args.workers)
        ok = sorted(s for s, v in results.items() if v == "ok")
        print(f"scraped {len(ok)} of {len(results)} states: {', '.join(ok)}")
        files = sorted(args.data_dir.glob("*.csv"))
        print(warn.load(ctx.store, files, ctx.log))
        return 0
    if args.cmd == "run":
        stats = Pipeline(ctx).run(days=args.days, forms=args.forms, max_docs=args.max_docs, end=args.end)
        print(json.dumps(stats, indent=1))
        print(f"trace: {ctx.log.path}")
        return 0
    if args.cmd == "review":
        if args.list:
            for row in rv.pending(ctx):
                print(rv.show(row) + "\n")
            return 0
        if args.approve or args.reject:
            for rid in args.approve:
                print(rv.decide(ctx, rid, True, args.note)["status"], rid)
            for rid in args.reject:
                print(rv.decide(ctx, rid, False, args.note)["status"], rid)
            return 0
        rv.interactive(ctx)
        return 0
    if args.cmd == "publish":
        print(rv.publish(ctx))
        return 0
    if args.cmd == "evaluate":
        print(json.dumps(rv.evaluate(ctx), indent=1))
        return 0
    if args.cmd in ("outcomes", "playbook", "scorecard"):
        from . import outcomes
        if args.cmd == "outcomes":
            print(json.dumps(outcomes.follow_up(ctx, args.max_docs), indent=1))
        elif args.cmd == "playbook":
            print(json.dumps(outcomes.playbook(ctx), indent=1))
        else:
            print(json.dumps(outcomes.scorecard(ctx, args.quarter or outcomes.current_quarter()), indent=1, default=str))
        return 0
    if args.cmd == "spenders":
        from . import spenders
        print(json.dumps(spenders.fetch(ctx)[:10], indent=1))
        top = spenders.top10(ctx)
        if args.scan:
            stats = Pipeline(ctx).run(days=args.days, forms="8-K,10-K,10-Q", max_docs=args.max_docs,
                                      ciks=spenders.scan_ciks(top), queries=spenders.AI_QUERIES)
            print(json.dumps(stats, indent=1))
        print(spenders.report(ctx, top))
        return 0
    if args.cmd == "foreign":
        import os
        from .foreign import KEYED
        sources = ["asx", "bse", "tdnet", "cninfo", "amf", "cvm", "dart", "edinet"] if args.source == "all" else [args.source]
        for src in [s for s in sources if s in KEYED and not os.environ.get(KEYED[s])]:
            print(f"{src}: skipped, {KEYED[src]} not set")
        sources = [s for s in sources if not (s in KEYED and not os.environ.get(KEYED[s]))]
        for src in sources:
            stats = Pipeline(ctx).run(days=args.days, forms="", max_docs=args.max_docs, end=args.end, source=src)
            print(src, json.dumps(stats))
        print(f"trace: {ctx.log.path}")
        return 0
    if args.cmd == "benchmark":
        from . import benchmark
        print(json.dumps(benchmark.export(ctx), indent=1))
        return 0
    if args.cmd == "erm":
        from datetime import date, timedelta
        from . import erm
        end = date.today()
        print(json.dumps(erm.load(ctx, (end - timedelta(days=args.days)).isoformat(), end.isoformat()), ensure_ascii=False))
        return 0
    if args.cmd == "countries":
        from . import compare
        print(json.dumps(compare.compare(ctx), indent=1, ensure_ascii=False))
        return 0
    if args.cmd == "uk":
        stats = Pipeline(ctx).run(days=args.days, forms="", max_docs=args.max_docs, end=args.end, source="uk")
        print(json.dumps(stats, indent=1))
        print(f"trace: {ctx.log.path}")
        return 0
    if args.cmd == "uk-probe":
        from . import uk
        print(json.dumps(uk.probe(ctx.log, args.keyword), indent=1))
        return 0
    if args.cmd == "memory":
        for m in ctx.hmem.all():
            sup = f" superseded_by={m.supersededBy}" if m.supersededBy else ""
            print(f"{m.id} conf={m.confidence:.2f} layer={m.layer}{sup}\n   {m.content[:160]}")
        print(f"ledger entries: {ctx.hmem.ledger_count()}")
        return 0
    return 1


if __name__ == "__main__":
    sys.exit(main())
