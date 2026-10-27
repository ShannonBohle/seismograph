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
        for t in ("filings", "documents", "warn_notices", "records"):
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


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="seismograph")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("check", help="check Zetaris, Nightshift, the evidence store and H-MEM")
    sub.add_parser("init-db", help="create the evidence-store tables")
    w = sub.add_parser("load-warn", help="load warn-scraper CSV files into the evidence store")
    w.add_argument("files", nargs="+", type=Path)
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
    args = p.parse_args(argv)

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
    if args.cmd == "memory":
        for m in ctx.hmem.all():
            sup = f" superseded_by={m.supersededBy}" if m.supersededBy else ""
            print(f"{m.id} conf={m.confidence:.2f} layer={m.layer}{sup}\n   {m.content[:160]}")
        print(f"ledger entries: {ctx.hmem.ledger_count()}")
        return 0
    return 1


if __name__ == "__main__":
    sys.exit(main())
