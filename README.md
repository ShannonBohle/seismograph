# Seismograph

**Agentic daily tracking of AI-attributed job cuts, in employers' own words.**

Open Agent Hackathon 2026 entry by Shannon Bohle (solo). Tracks: *The Agent That Can Explain Why* and *Solving Fragmented Intelligence*.

## The problem

Data journalists and labor-market researchers keep hearing that AI is causing layoffs. There is no public record of what employers themselves say about it, and reported framing often goes further than the employer's words. Checking each claim means reading SEC filings, press releases and state WARN notices, which live in different systems, and separating real announcements from legal boilerplate.

## What the agent does

Seismograph finds SEC filings that mention both job cuts and AI or automation. For each one it decides whether the filing actually announces or reports job cuts, and if so drafts a record that quotes the employer word for word and labels what the employer said:

- **explicit**: the employer says AI or automation is a reason for these cuts
- **partial**: the employer mentions AI or automation alongside the cuts as strategy, reinvestment or efficiency (including AI as the company's new strategic focus), but does not say AI caused the cuts
- **none**: the employer announces cuts and does not connect them to AI; mentions of AI that appear only in risk factors or forward-looking-statement boilerplate do not count

The labels describe what the employer said, not whether AI caused the cuts. Every record cites the filing it came from. A record is published only when its quote matches the source verbatim, the Judge agent passes it, and a human editor approves it (enforced in `review.py`).

## Results from the live runs (27 October 2026)

| | |
|---|---|
| Unique filings processed live | 18 (8-K and 6-K, last 90 days) |
| Identified as not announcing job cuts | 9 (e.g. Gartner, PayPal, Xerox, Wayfair; and an Informa TechTarget release where "workforce reduction" appeared only in boilerplate) |
| Records sent to the editor | 9 |
| Approved and published | 6: Latch (two filings), Quantum-Si, NIQ, monday.com, Valneva |
| Rejected by the editor | 3 |
| Judge vs editor agreement | 67% (6 of 9). Every disagreement was the Judge passing a record the editor rejected; the Judge has not yet failed anything, so Cohen's kappa is 0 and not yet informative |

What the editor caught, and what changed because of it:
- **Boilerplate is not an announcement.** The first run passed an Informa TechTarget revenue update whose only mention of a "workforce reduction program" and of "artificial intelligence laws" was in forward-looking-statement boilerplate. The editor rejected it. The agent now requires the verbatim sentence that announces the cuts (`cut_quote`) and the Verifier rejects any quote taken from forward-looking-statement or risk-factor paragraphs. In the next run, the following TechTarget release was correctly classed as not a layoff.
- **AI as strategy is "partial".** UTStarcom and CollPlant both reported real reductions while describing AI as the company's strategic direction; the agent labelled them "none". The editor rejected both and the label definition above now states the rule.

The published record is in `out/index.html`, `out/records.json` and `out/records.csv`.

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
| Extractor | Reads the document **through Zetaris** and drafts a claims-only record: company, date, the sentence announcing the cuts, number of jobs and the employer's verbatim AI quote | Nemotron via Nightshift |
| Verifier | Checks that the announcement sentence, the AI quote and the job-number wording appear verbatim in the source, that none of them come from boilerplate, and that the number matches the wording. Sends failures back to the Extractor with the reason | Deterministic code |
| Judge | Independent second opinion against a written rubric (employer's own words? label correct? an announcement rather than boilerplate? causation overclaimed? consistent with what memory holds?). Sends failures back to the Extractor with a critique | Nemotron via Nightshift |
| MemorySteward | Recalls what H-MEM already knows about the company and gives it to the Judge; detects duplicates and supersession; turns the editor's decision into H-MEM feedback | Meterless H-MEM |
| Corroborator | Cross-source checks **through Zetaris**: matching WARN notices within 120 days, WARN job totals versus the filing's number, and AI language present in one document of a filing but absent from another | Zetaris |
| Editor | A person approves or rejects each record (`python -m seismograph review`) | Human |
| Publisher | Reads approved, verified records **through Zetaris** and writes the public record | Zetaris |

The Extractor, Verifier and Judge loop is not a fixed chain: a draft can be sent back up to two times, by either the Verifier or the Judge, and each send-back carries the specific problems found. Failed provider calls are retried twice and logged; if the Judge cannot be reached, the record is held back and can never be published.

**H-MEM in action (live run):** the 5 August Latch filing (65 roles eliminated) arrived after the editor had approved a 10 August Latch record with no headcount. MemorySteward recalled the approved memory, recognised the same company and event window, and recorded the new filing as superseding it, because it adds the headcount. The editor's decisions moved memory confidence: approved records rose from 0.60 to 0.65 and were promoted to long-term memory; rejected ones fell to 0.40 with a `review` tag. Every change is in the trust ledger.

### Evidence the logs show

Every run writes `logs/run-<id>.jsonl`. Each line has `timestamp`, `agent_name`, `action`, `input_summary`, `output_summary`, `target_agent`, `model`, `status` and `retry_count`, plus details: the SQL sent to Zetaris, H-MEM memory ids and ledger activity, and for every model call both the requested model and the model Nightshift reports it served (`nemotron_served: true/false`). Secrets are scrubbed before anything is written.

## AI and model usage

**Models (every model call the agent makes while running goes through Meterless Nightshift):**
- Extractor and Judge: `nvidia/nemotron-3-ultra-550b-a55b`, pinned by name in `EXTRACTOR_MODEL` and `JUDGE_MODEL`, with `REQUIRE_NEMOTRON=true` so the agent refuses any answer Nightshift did not serve with a Nemotron model. The two roles have separate prompts; the Verifier's verbatim checks do not depend on any model.
- We first assigned `nvidia/nemotron-3.5-lightning-30b-a3b` to the Extractor (a faster model in its own lane). Through Nightshift it returned empty responses on every call in one run; the agent retried, logged each failure and skipped the documents without creating records. The Extractor was then moved to Ultra.
- In the very first run the models were left on `meterless/auto`, which routed calls to `nemotron-3.5-bolt-e4b`, Lightning and Ultra; the logs record which one answered each call.

**Sponsor technology:**
- **Zetaris**: the data layer. The evidence store (PostgreSQL) is registered in Zetaris as the datasource `seismo_pg` (`sql/02_zetaris_register.sql`). Scout, Extractor, Corroborator, Publisher and Evaluator read everything through Zetaris SQL (`POST /api/proxy/sql-editor/sqls/run-query`), including the cross-source WARN and same-filing checks.
- **Meterless Engine: H-MEM**, implemented in Python in `seismograph/hmem/memory.py` from the engine spec. It powers recall, duplicate and supersession detection, and learning from the editor's decisions (approve = `helpful` +0.05 and promotion to `long_term`; reject = `wrong` −0.20 and a `review` tag), with an append-only trust ledger. **Test:** `tests/test_hmem_conformance.py` runs the engine's own conformance vectors (11 checks across record validation, feedback deltas, ranking order, conflict gate and ledger audit, all passing). The sleep-cycle and dreaming sections are not implemented. `tests/test_pipeline.py` tests H-MEM inside the product.
- **Meterless Nightshift**: the agent's model provider at run time, implemented to the Meterless Runtime provider contract (`seismograph/nightshift.py`): normalised `/v1` base URL, bearer token from Nightshift's `proxy-token`, `meterless-app: seismograph` and `meterless-scope: user` headers, 180 s timeout, and `GET /v1/models` as the status check.
- **NVIDIA**: Nemotron 3 Ultra, reached through Nightshift's NVIDIA provider (the NVIDIA key lives in Nightshift, not in this project), runs the Extractor and the Judge.

**Development tools:** Built with Claude (Anthropic) as the AI coding assistant, for design, code, tests, debugging and documentation, including this README and the slide deck. The earlier jobs-tracker dashboard this project builds on (see Pre-existing work) was also built with Claude. Cursor was not used. All code was reviewed and tested by the author.

**Agent framework:** none; a small custom orchestrator (`seismograph/pipeline.py`).

## Data sources

| Source | What | Notes |
|---|---|---|
| SEC EDGAR full-text search and Archives | 8-K and 6-K filings and exhibits (press releases filed as EX-99.1) | Public; fetched with a descriptive User-Agent at under 5 requests per second |
| State WARN Act notices | Layoff notices, via Big Local News' [warn-scraper](https://github.com/biglocalnews/warn-scraper) | Public; supported by the loader. No WARN data was loaded for the runs above, and each record says so ("WARN notices not checked") |

No personal data is collected. Test fixtures in `tests/` are synthetic and labelled as such.

## Setup

Tested on Windows 11 with Docker Desktop (WSL 2 backend). Zetaris runs in Docker; the agent runs in Windows Python, because Nightshift's runtime listens on Windows' own `127.0.0.1`.

1. **Zetaris:** follow the hackathon Participant Setup Guide (register at Zetaris Cloud, download the platform bundle, `docker compose up -d`, `./verify.sh`). Docker needs 8 GB of memory and about 15 GB of disk on an NTFS drive.
2. **Nightshift:** install from meterless.ai, add your NVIDIA key under Setup → Providers, and launch it.
3. **This project (PowerShell):**
   ```powershell
   git clone https://github.com/ShannonBohle/seismograph.git
   cd seismograph
   docker compose up -d evidence-db
   python -m venv .venv
   .\.venv\Scripts\python -m pip install -r requirements.txt
   Copy-Item .env.example .env      # then fill in .env
   .\.venv\Scripts\python -m seismograph init-db
   .\.venv\Scripts\python -m pytest -q
   ```
   On Linux or macOS, `bash setup.sh` does the same.
4. In the Zetaris SQL Editor (http://localhost:3000), run the statements in `sql/02_zetaris_register.sql` one at a time.
5. Fill in `.env`: Zetaris org ID (Management → Organisation) and admin login or API key; `METERLESS_RUNTIME_TOKEN` from `%LOCALAPPDATA%\ai.meterless.runtime\proxy-token`; the model ids from `GET /v1/models`; `SEC_USER_AGENT` with your email.
6. Check every connection: `.\.venv\Scripts\python -m seismograph check`

Optional WARN data: `warn-scraper CA NY TX WA --data-dir ./warn` then `python -m seismograph load-warn warn/*.csv`.

## Usage

```powershell
.\.venv\Scripts\python -m seismograph run --days 90 --max-docs 15   # discover, extract, verify, judge, remember, corroborate
.\.venv\Scripts\python -m seismograph review                         # editor approves or rejects each pending record
.\.venv\Scripts\python -m seismograph publish                        # writes out/index.html, records.json, records.csv
.\.venv\Scripts\python -m seismograph evaluate                       # Judge vs editor agreement (Cohen's kappa)
.\.venv\Scripts\python -m seismograph memory                         # H-MEM memories and ledger size
.\.venv\Scripts\python -m pytest -q                                  # 19 tests
```

## Sample mode (no Zetaris, Nightshift or keys needed)

```powershell
python -m venv .venv
.\.venv\Scripts\python -m pip install -r requirements.txt
$env:SAMPLE_MODE="true"
.\.venv\Scripts\python -m seismograph run --max-docs 20
.\.venv\Scripts\python -m seismograph review
.\.venv\Scripts\python -m seismograph publish      # writes out/sample/index.html
```

Sample mode replays the SEC and Nightshift responses recorded during the live runs (`RECORD_SAMPLE=true`) from `data/sample/`, and runs the same SQL against a local SQLite copy of the evidence store instead of Zetaris. Replay matches a recorded request on the fields that determine the answer, so it works on another day and with the default model settings; the model that actually answered is replayed and logged. It reproduces the live run: the same 9 filings classed as not layoffs and the same 6 records sent for review. Four filings from the first run were recorded under earlier prompts and are skipped with a `not_in_sample` log line. Every log line carries `"mode": "sample"`, the page says "Sample mode", and its output goes to `out/sample/` so it never overwrites the live record.

## Limitations

- The labels record what employers say. They are not evidence that AI caused any job cut.
- Coverage is limited to SEC filers (8-K, 6-K) whose filings match the full-text search terms, and to the WARN states loaded (none in the runs above).
- The Judge has been lenient: in 9 reviewed records it passed 3 the editor rejected and failed none. The editor step is required, not optional.
- Company matching between filings and WARN notices is name-based and can miss or mis-match subsidiaries.
- Event dates are as precise as the filing: "May 2026" is stored as 2026-05-01.
- H-MEM's semantic signal uses a hashed bag-of-words embedding, not a neural embedder. The sleep cycle and dreaming are not implemented.
- Nemotron 3 Ultra is slow for interactive use; a run of 15 filings takes a few minutes.

## Pre-existing work (declared)

The idea and an earlier jobs-tracker dashboard (WARN Act data and a press-claims ledger) predate the build window. That earlier dashboard was also built with Claude as the AI coding assistant. It is declared as a pre-existing component in the submission form. This repository's code was written during the build window and does not include the earlier dashboard's code.

## Third-party code and licences

- Meterless H-MEM conformance vectors (`tests/vectors/hmem_vectors.json`), Apache-2.0, copied unchanged. The H-MEM implementation follows the Meterless H-MEM spec (Apache-2.0).
- Zetaris request shape follows `zetaris/getting-started` `scripts/query_zetaris.py`.
- requests (Apache-2.0), psycopg (LGPL-3.0), pytest (MIT), warn-scraper (Apache-2.0).

## Troubleshooting

- **Zetaris cannot reach the evidence store** when running `CREATE DATASOURCE`: the Zetaris containers reach the host through `host.docker.internal`. Check that `docker compose ps` shows `evidence-db` running and port 5433 published. Datasource descriptions may only contain letters, digits and `_ . - ,`.
- **Nightshift offline from WSL**: Nightshift listens on Windows' `127.0.0.1`, which WSL cannot reach by default. Run the agent from Windows PowerShell.
- **Nightshift says unauthorized**: copy the token from `%LOCALAPPDATA%\ai.meterless.runtime\proxy-token` into `METERLESS_RUNTIME_TOKEN`.
- **`nemotron_served: false` in the logs**: the routing mode sent the call to another model. Pin a Nemotron model id in `EXTRACTOR_MODEL` and `JUDGE_MODEL`.
- **Docker "read-only file system" or I/O errors**: Docker's disk image is on a FAT32 or removable drive (4 GB file limit). Move it to an NTFS drive.

MIT licence. See `LICENSE`.
