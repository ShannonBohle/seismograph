# Seismograph

**Agentic daily tracking of AI-attributed job cuts, in employers' own words.**

Open Agent Hackathon 2026 entry by Shannon Bohle (solo). Tracks: *The Agent That Can Explain Why* and *Solving Fragmented Intelligence*.

## The problem

Data journalists and labor-market researchers keep hearing that AI is causing layoffs. There is no public record of what employers themselves say about it, and reported framing often goes further than the employer's words. Checking each claim means reading SEC filings, press releases and state WARN notices, which live in different systems.

## What the agent does

Seismograph finds SEC filings that mention both job cuts and AI or automation. For each one it drafts a record that quotes the employer word for word and labels what the employer said:

- **explicit**: the employer says AI or automation is a reason for these cuts
- **partial**: AI appears in the same announcement, but the employer does not say it caused the cuts
- **none**: the employer does not connect the cuts to AI

The labels describe what the employer said, not whether AI caused the cuts. Every record cites the filing it came from. A record is published only when its quote matches the source verbatim, the Judge agent passes it, and a human editor approves it.

## How it works

```
                       Zetaris (data layer: SQL over the evidence store)
                          ▲ reads                 ▲ reads             ▲ reads
SEC EDGAR ─► Scout ─► evidence store ─► Extractor ⇄ Verifier ⇄ Judge ─► MemorySteward ─► Corroborator ─► Editor (human) ─► Publisher
            (search,  (PostgreSQL)      Nemotron    verbatim   Nemotron   Meterless H-MEM   WARN + filing     approve/reject    out/index.html
             fetch)                     via          checks    via        recall, dedupe,   cross-checks      feeds H-MEM       records.json/csv
                                        Nightshift             Nightshift supersede                           confidence
WARN CSVs (warn-scraper) ─► evidence store
```

| Agent | Role | Backed by |
|---|---|---|
| Scout | Searches EDGAR full-text search (8-K, 6-K) for filings that mention AI and workforce reductions; fetches each document into the evidence store; skips documents already recorded (asked through Zetaris) | SEC EDGAR, Zetaris |
| Extractor | Reads the document **through Zetaris** and drafts a claims-only record: company, date, number of jobs and the employer's verbatim quote | Model via Nightshift |
| Verifier | Checks that the quote and the job-number wording appear verbatim in the source, and that the number matches the wording. Sends failures back to the Extractor with the reason | Deterministic code |
| Judge | Independent second opinion against a written rubric (employer's own words? label correct? causation overclaimed? consistent with what memory holds?). Sends failures back to the Extractor with a critique | Model via Nightshift |
| MemorySteward | Recalls what H-MEM already knows about the company and gives it to the Judge; detects duplicates and supersession; turns the editor's decision into H-MEM feedback | Meterless H-MEM |
| Corroborator | Cross-source checks **through Zetaris**: matching WARN notices within 120 days, WARN job totals versus the filing's number, and AI language present in one document of a filing but absent from another | Zetaris |
| Editor | A person approves or rejects each record (`python -m seismograph review`) | Human |
| Publisher | Reads approved, verified records **through Zetaris** and writes `out/index.html`, `records.json` and `records.csv` | Zetaris |

The Extractor, Verifier and Judge loop is not a fixed chain: a draft can be sent back up to two times, by either the Verifier or the Judge, and each send-back carries the specific problems found. Records that still fail are shown to the editor only as labelling material and can never be published (enforced in `review.py`).

### Evidence the logs show

Every run writes `logs/run-<id>.jsonl`. Each line has `timestamp`, `agent_name`, `action`, `input_summary`, `output_summary`, `target_agent`, `model`, `status` and `retry_count`, plus details: the SQL sent to Zetaris, H-MEM memory ids and ledger activity, and for every model call both the requested model and the model Nightshift reports it served (`nemotron_served: true/false`). Secrets are scrubbed before anything is written.

## AI and model usage

**Models (through Meterless Nightshift, the only AI provider the product calls):**
- Extractor: the model set in `EXTRACTOR_MODEL` (a Nemotron model assigned in a Nightshift lane)
- Judge: the model set in `JUDGE_MODEL` (a Nemotron model assigned in a Nightshift lane)
- Which model actually answered each call is recorded in the logs. `REQUIRE_NEMOTRON=true` makes the agent refuse any answer Nightshift did not serve with a Nemotron model.

**Sponsor technology:**
- **Zetaris**: the data layer. The evidence store (PostgreSQL) is registered in Zetaris as the datasource `seismo_pg` (`sql/02_zetaris_register.sql`). The Extractor, Scout, Corroborator, Publisher and Evaluator read everything through Zetaris SQL (`POST /api/proxy/sql-editor/sqls/run-query`), including the cross-source WARN and same-filing checks.
- **Meterless Engine: H-MEM**, implemented in Python in `seismograph/hmem/memory.py` from the engine spec. It powers recall, duplicate and supersession detection, and learning from the editor's decisions (approve = `helpful` +0.05 and promotion to `long_term`; reject = `wrong` −0.20 and a `review` tag). Every mutation is written to an append-only trust ledger. **Test:** `tests/test_hmem_conformance.py` runs the engine's own conformance vectors (11 checks across record validation, feedback deltas, ranking order, conflict gate and ledger audit, all passing). The sleep-cycle and dreaming sections are not implemented. `tests/test_pipeline.py` tests H-MEM inside the product (dedupe, feedback, promotion).
- **Meterless Nightshift**: the primary AI provider, implemented to the Meterless Runtime provider contract (`seismograph/nightshift.py`): normalised `/v1` base URL, bearer token, `meterless-app: seismograph` and `meterless-scope: user` headers, 180 s timeout, and `GET /v1/models` as the status check.
- **NVIDIA**: Nemotron models, reached through Nightshift's NVIDIA provider, run the Extractor and Judge.

**Development tools:** Built with Claude (Anthropic) as the AI coding assistant, for scaffolding, code, tests and documentation. All code was reviewed and tested by the author.

**Agent framework:** none; a small custom orchestrator (`seismograph/pipeline.py`).

## Data sources

| Source | What | Notes |
|---|---|---|
| SEC EDGAR full-text search and Archives | 8-K and 6-K filings and exhibits (press releases filed as EX-99.1) | Public; fetched with a descriptive User-Agent at under 5 requests per second |
| State WARN Act notices | Layoff notices, via Big Local News' [warn-scraper](https://github.com/biglocalnews/warn-scraper) | Public; only the states you load are checked, and the record says which |

No personal data is collected. Test fixtures in `tests/` are synthetic and labelled as such.

## Setup (Windows with WSL, or Linux/macOS)

Prerequisites: Docker, Python 3.11+, a running local Zetaris (see the hackathon Participant Setup Guide) and Meterless Nightshift with your NVIDIA key in its Providers tab.

```bash
bash setup.sh                  # venv, dependencies, evidence store (docker compose), tables, tests
```

1. In the Zetaris SQL Editor (http://localhost:3000), run the statements in `sql/02_zetaris_register.sql` one at a time.
2. Fill in `.env` (copied from `.env.example`): Zetaris org ID and API key (or admin login), the Nightshift token from its Console, the model ids, and `SEC_USER_AGENT`.
3. Load WARN notices for some states (optional but recommended):
   ```bash
   . .venv/bin/activate
   warn-scraper CA NY TX WA --data-dir ./warn
   python -m seismograph load-warn warn/*.csv
   ```
4. Check every connection:
   ```bash
   python -m seismograph check
   ```

## Usage

```bash
python -m seismograph run --days 60 --max-docs 10   # discover, extract, verify, judge, remember, corroborate
python -m seismograph review                         # editor approves or rejects each pending record
python -m seismograph publish                        # writes out/index.html, records.json, records.csv
python -m seismograph evaluate                       # Judge vs editor agreement (Cohen's kappa)
python -m seismograph memory                         # H-MEM memories and ledger size
python -m pytest -q                                  # tests
```

## Sample mode

`SAMPLE_MODE=true` runs without Zetaris, Nightshift, the SEC or any keys. It replays SEC and Nightshift responses that were recorded during a real run (`RECORD_SAMPLE=true`) from `data/sample/`, and runs the same SQL against a local SQLite copy of the evidence store instead of Zetaris. Every log line in sample mode carries `"mode": "sample"`. Replayed data is never presented as a live run.

## Limitations

- The labels record what employers say. They are not evidence that AI caused any job cut.
- Coverage is limited to SEC filers (8-K, 6-K) whose filings match the full-text search terms, and to the WARN states loaded.
- Company matching between filings and WARN notices is name-based and can miss or mis-match subsidiaries.
- H-MEM's semantic signal uses a hashed bag-of-words embedding, not a neural embedder. The sleep cycle and dreaming are not implemented.
- The Judge is a model and can be wrong; `evaluate` reports its agreement with the editor so its accuracy is measured rather than assumed.

## Pre-existing work (declared)

The idea and the earlier jobs-tracker dashboard (WARN Act data and a press-claims ledger) predate the build window. This repository's code was written during the build window.

## Third-party code and licences

- Meterless H-MEM conformance vectors (`tests/vectors/hmem_vectors.json`), Apache-2.0, copied unchanged. The H-MEM implementation follows the Meterless H-MEM spec (Apache-2.0).
- Zetaris request shape follows `zetaris/getting-started` `scripts/query_zetaris.py`.
- requests (Apache-2.0), psycopg (LGPL-3.0), pytest (MIT), warn-scraper (Apache-2.0).

## Troubleshooting

- **Zetaris cannot reach the evidence store** when running `CREATE DATASOURCE`: the Zetaris containers reach the host through `host.docker.internal`. Check that `docker compose ps` shows `evidence-db` running and port 5433 published.
- **Nightshift says unauthorized**: paste the runtime token from Nightshift's Console into `METERLESS_RUNTIME_TOKEN`.
- **`nemotron_served: false` in the logs**: the routing mode sent the call to another model. Assign a Nemotron model to a lane in Nightshift and set `EXTRACTOR_MODEL` and `JUDGE_MODEL` to it.

MIT licence. See `LICENSE`.
