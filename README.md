# AI Seismograph

**Agentic daily tracking of AI-attributed job cuts, in employers' own words.**

Open Agent Hackathon 2026 entry by Shannon Bohle (solo). Tracks: *The Agent That Can Explain Why* and *Solving Fragmented Intelligence*.

## The problem

Data journalists and labor-market researchers keep hearing that AI is causing layoffs. There is no public record of what employers themselves say about it, and reported framing often goes further than the employer's words. Checking each claim means reading SEC filings, press releases and state WARN notices, which live in different systems, and separating real announcements from legal boilerplate.

## What the agent does

Seismograph finds SEC filings and official company announcements in the UK, France, Australia, India, Japan and China that mention both job cuts and AI or automation. For each one it decides whether the filing actually announces or reports job cuts, and if so drafts a record that quotes the employer word for word and labels what the employer said:

- **explicit**: the employer says AI or automation is a reason for these cuts
- **partial**: the employer mentions AI or automation alongside the cuts as strategy, reinvestment or efficiency (including AI as the company's new strategic focus), but does not say AI caused the cuts
- **none**: the employer announces cuts and does not connect them to AI; mentions of AI that appear only in risk factors or forward-looking-statement boilerplate do not count

The labels describe what the employer said, not whether AI caused the cuts. Every record cites the filing it came from. A record is published only when its quote matches the source verbatim (after normalising whitespace, letter case, typographic quotes and dashes; see Reproducibility), the Judge agent passes it, and a human editor approves it (enforced in `review.py`).

The same engine then produces:
- **What employers tell regulators.** For each employer that linked its cuts to AI, the record shows whether the employer filed a WARN layoff notice in the states loaded within 180 days, and whether that notice's stated reason mentions AI. Across all WARN notices loaded, the report counts how many of those that state a reason cite AI or automation. This compares statements; it does not say which one is true.
- **The largest AI spenders, side by side.** For the ten largest capital spenders among large US-listed technology companies: three fiscal years of capital expenditure, net income and revenue from their SEC XBRL data, next to any AI-attributed cuts in their own filings. Capital expenditure is total purchases of property and equipment, not a measure of AI investment, and nothing in the report says the spending caused any cut (`out/spenders.html`).
- **What happened next, and an employer playbook.** For each approved record, the Outcomes agent reads the employer's later filings and records what it reported about the restructuring (savings achieved or expected, charges, problems, reversals), each quote checked verbatim. The playbook groups these peer cases with a confidence rating: high when later results are reported, medium when only expected results are, low when nothing is reported yet (`out/playbook.html`).
- **A country comparison** of what employers disclosed: documents read, job cuts found, how often AI was linked to them, and later reported results, with Europe's restructuring events alongside. It measures disclosure, not which country is "ahead" in AI (`out/countries.html`).
- **Ask and check.** `python -m seismograph ask "..."` answers a plain-English question from the published record: Nemotron writes one read-only SQL query over the disclosure views, Zetaris runs it, and the answer is the returned rows with their sources. `check "<claim>"` returns what the named employer itself said, which makes it a claim checker.
- **Quarterly scorecards** of AI-attributed cuts by label, jobs stated, and reported outcomes (`out/scorecard-YYYYQn.html`).

**Coverage.** Each record carries the employer's market and, for SEC filers, the country of its business address (SEC submissions data), so coverage is counted from the data: the 12 published records come from employers in six countries: the United States, France and Israel (SEC filers; France also through its official storage mechanism), the United Kingdom (FCA announcements), Australia (ASX) and Japan (TDnet). For SEC filers the country is the business address; for exchange announcements it is the listing market, not necessarily the company's domicile.

## Results from the live runs (27 October 2026)

**Headline: 1 of 5,108 WARN layoff notices that state a reason cites AI or automation.** Only 2 of the 22 states loaded record a reason (Illinois and New York). The one notice is New York's, filed by Nespresso on 13 January 2026 for 46 jobs, reason "Relocation of Business, Artificial Intelligence". An earlier dashboard by the same author, built from a different set of four states, found 1 in 5,081.

| | |
|---|---|
| SEC filings processed live | 18 (8-K and 6-K, last 90 days) |
| UK announcements processed live | 13 unique, from the FCA National Storage Mechanism (a further set failed to download and are retried on the next run) |
| Identified as not announcing job cuts | 9 SEC (e.g. Gartner, PayPal, Xerox, Wayfair; an Informa TechTarget release where "workforce reduction" appeared only in boilerplate) and 10 UK (e.g. Renishaw, Softcat, NatWest Markets; Midwich after the Judge sent the draft back) |
| Documents read, by country (`out/countries.json`) | United States 37, United Kingdom 13, France 10, China 9, Australia 8, Japan 7, India 5, Canada 4, Brazil 3, Israel 3, Netherlands 1, not known 2 (101 in all). Exchange announcements count by listing market |
| Records sent to the editor | 17 with a verified quote and a Judge pass, plus held-back records shown for labelling only |
| Approved and published | 12: Latch (two filings), Quantum-Si, NIQ, monday.com, Valneva (SEC); YouGov, Flowtech Fluidpower (UK); National Australia Bank, Mayfield Childcare (ASX); Kosé Holdings (TDnet); VOLTZ (France) |
| Rejected by the editor | 5 (TechTarget, UTStarcom, CollPlant, Hilton Food Group, METAVISIO) |
| Employers in the published record | Six countries: United States, United Kingdom, France, Israel, Australia, Japan |
| Told regulators | None of the 5 published employers that linked their cuts to AI has a WARN notice in the loaded states within 180 days |
| Judge vs editor agreement | 71% (12 of 17 reviewed). All 5 disagreements were the Judge passing a record the editor rejected; the Judge has not yet failed a record that reached the editor, so Cohen's kappa is 0 and not yet informative |
| Outcomes found in later filings | 15 verbatim, verified statements for 5 companies (charges, expected savings and other reported results); the playbook rates 4 peer cases high confidence (later results reported) and 3 low (nothing reported yet) |
| Quarterly scorecard, 2026 Q3 | 6 published records with events in the quarter: 4 partial, 2 none, 0 explicit; 65 jobs stated by number |

What the editor caught, and what changed because of it:
- **Boilerplate is not an announcement.** The first run passed an Informa TechTarget revenue update whose only mention of a "workforce reduction program" and of "artificial intelligence laws" was in forward-looking-statement boilerplate. The editor rejected it. The agent now requires the verbatim sentence that announces the cuts (`cut_quote`) and the Verifier rejects any quote taken from forward-looking-statement or risk-factor paragraphs. In the next run, the following TechTarget release was correctly classed as not a layoff.
- **AI as strategy is "partial".** UTStarcom and CollPlant both reported real reductions while describing AI as the company's strategic direction; the agent labelled them "none". The editor rejected both and the label definition above now states the rule. The same rule decided Hilton Food Group (UK): real redundancies from "efficiency and restructuring programs", with automation and machine-learning line control named as efficiency strategy in the same report; the Judge passed "none" and the editor rejected it as partial.
- **Close to explicit.** YouGov's CFO review places Wave 1 headcount reductions inside a plan that "combine[s] efficiency improvements and enhanced ways of working along with significant transformation enabled by AI". It is published as partial because YouGov does not say the cuts were caused by AI; a later filing that does would supersede it in H-MEM.

The published record is in `out/index.html`, `out/records.json` and `out/records.csv`.

## Reproducibility

**Independent replication.** On 27 October 2026 a fresh clone of this repository (commit `79cfe2e`) was opened in Cursor, and Cursor's own agent (model: Cursor Grok 4.6), with no access to the original build session, was given a written specification of what to check, not the code. It rebuilt the project, replayed the sample-mode run, wrote its own quote checker (`replication/verify_quotes.py`, which does not import this repository's verifier) and recomputed the headline. Its full report is `replication/REPLICATION.md`; nothing it found was patched before publication.

| Check | Result |
|---|---|
| Fresh environment, `pytest` | pass: 29 passed |
| Sample-mode replay with an empty `.env` | pass: 20 documents, 0 errors; same labels as the published record for the five overlapping SEC employers |
| `jobs_cut_text` found in the live source | pass: 9 of 9 |
| `ai_quote` found in the live source | 4 of 5: NIQ failed |
| Headline recomputed from `out/gap.json` | pass: 1 of 5,108 |

**What it found, and what it means.**
- **NIQ's quote is not character-for-character.** The source uses typographic quotation marks (“AI”, Company’s); the published quote has straight ones. The words are identical. Our Verifier normalises whitespace, letter case, curly quotes and dashes before matching; the replication normalised whitespace only. Earlier versions of this README said "character for character"; that overstated the check, and the wording above now states the normalisation exactly. The published quote should keep the source's punctuation; the Verifier is to be changed to store the source's own characters.
- **The README's counts were out of date** (8 records instead of 12; 9 not-layoffs where the current replay gives 10). The results table above was updated after the replication.
- **One published filing could not be replayed** (Latch, 10 August) because its model response is not in the sample recording, and **CollPlant appears as pending in the replay** because sample mode does not replay the editor's rejection.
- Sample mode replays the SEC run only, so UK and other foreign records cannot appear in it.

## How it works

```
                       Zetaris (data layer: SQL over the evidence store)
                          ▲ reads                 ▲ reads             ▲ reads
SEC EDGAR ─► Scout ─► evidence store ─► Extractor ⇄ Verifier ⇄ Judge ─► MemorySteward ─► Corroborator ─► Editor (human) ─► Publisher
            (search,  (PostgreSQL)      Nemotron    verbatim   Nemotron   Meterless H-MEM   WARN + filing     approve/reject    out/index.html
             fetch)                     via          checks    via        recall, dedupe,   cross-checks      feeds H-MEM       records.json/csv
                                        Nightshift             Nightshift supersede                           confidence
FCA National Storage Mechanism (UK announcements) ─► Scout ─► the same loop
WARN CSVs (warn-scraper, every state it supports) ─► evidence store ─► Corroborator, GapAnalyst (through Zetaris)
SEC XBRL company facts ─► company_financials ─► Spenders report (through Zetaris)
Approved records ─► Outcomes agent (later filings, Nemotron, verbatim check) ─► playbook, quarterly scorecard
```

| Agent | Role | Backed by |
|---|---|---|
| Scout | Searches EDGAR full-text search (8-K, 6-K; 20-F and 10-K/10-Q for the spenders scan) and the FCA National Storage Mechanism for documents that mention AI and workforce reductions; fetches each document into the evidence store; skips documents already recorded (asked through Zetaris) | SEC EDGAR, Zetaris |
| Extractor | Reads the document **through Zetaris** and drafts a claims-only record: company, date, the sentence announcing the cuts, number of jobs and the employer's verbatim AI quote | Nemotron via Nightshift |
| Verifier | Checks that the announcement sentence, the AI quote and the job-number wording appear verbatim in the source (whitespace, letter case, curly quotes and dashes normalised), that none of them come from boilerplate, and that the number matches the wording. Sends failures back to the Extractor with the reason | Deterministic code |
| Judge | Independent second opinion against a written rubric (employer's own words? label correct? an announcement rather than boilerplate? causation overclaimed? consistent with what memory holds?). Sends failures back to the Extractor with a critique | Nemotron via Nightshift |
| MemorySteward | Recalls what H-MEM already knows about the company and gives it to the Judge; detects duplicates and supersession; turns the editor's decision into H-MEM feedback | Meterless H-MEM |
| Corroborator | Cross-source checks **through Zetaris**: matching WARN notices within 120 days, WARN job totals versus the filing's number, and AI language present in one document of a filing but absent from another | Zetaris |
| GapAnalyst | Through Zetaris: WARN notices and their stated reasons by state, and, for each record with an AI link, the same employer's WARN notices within 180 days | Zetaris |
| Outcomes | Reads later filings of each approved employer and records reported results, verbatim; feeds the playbook and scorecards | Nemotron via Nightshift, Zetaris |
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

**Development tools:** Built with Claude (Anthropic) as the AI coding assistant, for design, code, tests, debugging and documentation, including this README and the slide deck. The earlier jobs-tracker dashboard this project builds on (see Pre-existing work) was also built with Claude. Cursor was used only for the independent replication described under Reproducibility, using Cursor's agent with the Grok 4.6 model (SpaceXAI); Grok Build, xAI's separate coding product, was not used. All code was reviewed and tested by the author.

**Agent framework:** none; a small custom orchestrator (`seismograph/pipeline.py`).

## Data sources

| Source | What | Notes |
|---|---|---|
| SEC EDGAR full-text search and Archives | 8-K and 6-K filings and exhibits (press releases filed as EX-99.1) | Public; fetched with a descriptive User-Agent at under 5 requests per second |
| SEC EDGAR 20-F, 10-K, 10-Q | Foreign companies' annual reports; the spenders' periodic reports | Same access rules |
| SEC XBRL company facts and submissions | Capital expenditure, net income, revenue; business-address country | data.sec.gov, same access rules |
| FCA National Storage Mechanism | UK regulated announcements (RNS and other services) in text form; PDFs such as prospectuses are skipped | Public; its terms allow use "for any lawful purpose". The FCA documents no API, so the agent sends the same search request as the public search page and spaces and retries downloads |
| ASX (Australia), BSE (India), TDnet (Japan), CNINFO (China) | Exchange announcements whose headline mentions workforce or AI terms; PDFs read as text; Japanese and Chinese quotes kept in the original language | Public; the agent sends the same requests as each exchange's public page; spaced and retried |
| info-financiere.gouv.fr (France) | France's official storage mechanism for regulated information, as open data | Licence Ouverte; open-data API |
| CVM open data (Brazil) | Listed companies' material facts and market notices (IPE), one open-data file per year with a download link per filing; Portuguese quotes kept in the original language | Public open data (dados.cvm.gov.br) |
| DART (South Korea), EDINET (Japan) | Built, activated by a free regulator API key (`DART_API_KEY`, `EDINET_API_KEY`) | Not run for this submission: keys not issued in time |
| Eurofound European Restructuring Monitor | Restructuring events of roughly 100+ jobs in the EU, Norway and the UK, with Eurofound's written summary | Public; compiled from media and company websites, so summaries are Eurofound's words, not employers' |
| State WARN Act notices | Layoff notices from every state Big Local News' [warn-scraper](https://github.com/biglocalnews/warn-scraper) supports | 29,062 notices from 22 states loaded (AK, AL, AZ, CT, DE, GA, IL, IN, KS, ME, MT, NE, NJ, NY, OK, OR, SC, SD, TX, UT, VT, WA). Missouri and New Mexico returned empty files; the other states did not return data in this run. Only Illinois and New York record a reason |

### Where the data comes from, country by country

- **Read directly from official company disclosures (8 countries):** United States (SEC), United Kingdom (FCA), France (AMF open data), Brazil (CVM open data), Australia (ASX), India (BSE), Japan (TDnet), China (CNINFO).
- **Through SEC filings by foreign companies listed in the US:** any country whose companies file 20-F, 40-F or 6-K reports, for example Canada, Mexico, Israel, Chile, Argentina, South Africa, Ireland and the Netherlands. These are the employer's own words, but only for US-listed companies.
- **Through the Eurofound European Restructuring Monitor:** the EU member states (including Germany, Spain and Portugal), Norway and the UK; 342 events from 29 countries in the last 120 days. These are Eurofound's summaries, not employers' words.
- **Layoff notices:** US state WARN notices (22 states). No other country in this project publishes notice-level layoff filings openly.
- **Built, waiting for a free key:** South Korea (DART), Japan (EDINET).
- **Considered, not reachable or not built in the hackathon window:** Hong Kong (HKEXnews blocks automated requests), Singapore (SGXNet needs a session token), Canada (SEDAR+ has no public API), Germany (Unternehmensregister has no public API), Mexico (BMV Emisnet), Spain (CNMV), Portugal (CMVM), Chile (CMF), South Africa (JSE SENS) and the rest of Africa and Latin America. Their US-listed companies are still covered through SEC filings, and EU countries through Eurofound.

No personal data is collected. Test fixtures in `tests/` are synthetic and labelled as such.

## What it could become, and why each sponsor would want it

The aim is a trusted data service for AI and work, in the way financial markets rely on data built from company filings: a daily, verified, sourced record of primary sources (not news) that newsrooms, researchers, investors and regulators cite. It does not collect or summarise news reports; newsrooms are among its users. It would grow country by country, with a free public record, a professional subscription (alerts, playbook, scorecards, per-company gap reports) and an enterprise data feed.

- **Zetaris.** A ready "disclosure intelligence" vertical on the Zetaris AI Data Harness: connectors for SEC, FCA, five foreign exchanges, WARN and Eurofound, plus SQL views (`v_employer_claims`, `v_warn_notices`, `v_reported_outcomes`) that any Zetaris customer in finance, insurance or compliance can query in place, and an Ask agent showing AI querying governed data where it lives.
- **Meterless.** A reference "verify" layer for agents built on Nightshift and H-MEM: verbatim checks, an independent judge, a human gate, and memory that learns from the editor, with every check logged as a receipt.
- **NVIDIA.** Nemotron 3 Ultra doing measurable, multilingual evidence work (English, Japanese, Chinese, French) with its accuracy against human editors published, and an open, labelled judge benchmark for evaluating and tuning Nemotron.
- **SpaceXAI.** A claim checker for fast-moving posts about AI and jobs: `check "<claim>"` returns what the employer itself said, with the source. (Not built with Grok; it would plug in as the source of claims to check.)

## Safety

- **Untrusted sources.** Every document is scanned for text aimed at a model (prompt injection) and hidden characters before the Extractor reads it (`safety.py`). A match puts a SAFETY flag on the record, and the record cannot be published unless the editor's note says "safety checked".
- **No invented evidence.** Quotes must match the source word for word; paraphrases are sent back and, if they persist, held back.
- **Human in the loop.** Nothing is published without an editor's approval, enforced in code; the Judge's measured accuracy is published.
- **Model provenance.** Models are pinned by name; with `REQUIRE_NEMOTRON=true` the agent refuses answers not served by Nemotron, and every call logs the model that actually answered.
- **Guarded SQL.** The Ask agent's query must be a single read-only SELECT over four views (`ask.guard()`); the model never states facts itself.
- **Claims, not causes.** Labels record what employers said; pages state what each comparison does and does not show.
- **Secrets and access.** Secrets are scrubbed from logs and never committed; sites' terms are checked, requests are rate-limited, and blocked sites are not worked around.
- See [FAILURE_MODES.md](FAILURE_MODES.md) for every failure found and what it changed, and [EVALS.md](EVALS.md) for the evaluations.

## Run it in one command (judges)

```bash
docker build -t seismograph . && docker run --rm -p 8000:8000 seismograph
curl -X POST localhost:8000/run -H "Content-Type: application/json" -d @samples/02_ask.input.json
```

The container runs `SAMPLE_MODE=true` with an empty `.env`: it replays the recorded live run, needs no keys, Zetaris or Nightshift, and still runs the real agent logic (verification, memory, Judge loop). `POST /run` takes `{"task": "discover" | "ask" | "check" | "publish" | "countries" | "evaluate", ...}` and returns JSON with the trace path; errors come back as JSON. Three sample inputs and their real outputs are in `samples/`.

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

WARN data: `python -m seismograph warn-all` runs warn-scraper for every state and loads the results (states that fail are logged). Then run `REGISTER DATASOURCE TABLES FROM seismo_pg` once more in Zetaris if `init-db` created new tables.

## Usage

```powershell
.\.venv\Scripts\python -m seismograph run --days 90 --max-docs 15   # SEC 8-K and 6-K: discover, extract, verify, judge, remember, corroborate
.\.venv\Scripts\python -m seismograph run --forms 20-F --days 365    # foreign companies' annual reports
.\.venv\Scripts\python -m seismograph uk --days 90 --max-docs 15    # UK regulatory announcements (FCA NSM)
.\.venv\Scripts\python -m seismograph foreign --source all --days 30  # ASX, BSE, TDnet, CNINFO, France, Brazil (DART, EDINET with keys)
.\.venv\Scripts\python -m seismograph erm --days 120                 # European restructuring events (Eurofound)
.\.venv\Scripts\python -m seismograph countries                      # out/countries.html
.\.venv\Scripts\python -m seismograph ask "Which employers linked cuts to AI?"
.\.venv\Scripts\python -m seismograph check-claim "monday.com cut staff because of AI"
.\.venv\Scripts\python -m seismograph benchmark                      # out/judge_benchmark.jsonl
.\.venv\Scripts\python -m seismograph serve                          # HTTP API on 0.0.0.0:8000
.\.venv\Scripts\python -m seismograph warn-all                       # WARN notices, every state warn-scraper supports
.\.venv\Scripts\python -m seismograph review                         # editor approves or rejects each pending record
.\.venv\Scripts\python -m seismograph publish                        # out/index.html (+ gap headline, countries), records.json/csv; copy in docs/
.\.venv\Scripts\python -m seismograph spenders --scan                # ten largest capital spenders: financials and their filings
.\.venv\Scripts\python -m seismograph outcomes                       # later filings of approved records
.\.venv\Scripts\python -m seismograph playbook                       # out/playbook.html
.\.venv\Scripts\python -m seismograph scorecard --quarter 2026Q3     # out/scorecard-2026Q3.html
.\.venv\Scripts\python -m seismograph evaluate                       # Judge vs editor agreement (Cohen's kappa)
.\.venv\Scripts\python -m seismograph memory                         # H-MEM memories and ledger size
.\.venv\Scripts\python -m seismograph schedule-daily --time 06:30    # Windows Task Scheduler: every step daily, then push the pages
.\.venv\Scripts\python -m pytest -q                                  # 29 tests
```

**Daily run.** `schedule-daily` registers a Windows scheduled task that runs every step above once a day (WARN, SEC, 20-F, UK, spenders, outcomes, playbook, scorecard, publish), then commits and pushes only the generated pages (`docs/`, `out/`) so GitHub Pages serves the public record. It runs on the host machine, so that machine must be on with Docker, Zetaris and Nightshift running. New records still wait for the editor; the daily run never approves anything.
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
- Coverage is limited to SEC filers and UK-listed companies whose documents match the search terms, and to the WARN states that loaded (22 states; see Data sources). Most states' WARN files record no reason, so the regulator comparison rests on the few that do.
- There is no public UK equivalent of WARN notices: HR1 advance notifications of collective redundancies go to the UK government and are not published. UK records are therefore not compared with a regulator filing.
- The FCA's announcement archive has no documented API; if the FCA changes its search service, UK discovery stops and the failure is logged. Its server sometimes resets connections; downloads are spaced and retried.
- Capital expenditure is total capital spending, not AI investment; companies do not report AI spending separately.
- Company announcements and shareholder letters are covered only when filed with the SEC or the FCA; company newsrooms are not crawled.
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
