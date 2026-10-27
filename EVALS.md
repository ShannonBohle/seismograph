# Evaluations shipped with Seismograph

| What | How to run | Result on 27 October 2026 |
|---|---|---|
| Unit and integration tests | `python -m pytest -q` | 31 passing (CLI parses every command, site navigation on every page, sample-mode Ask answers from the published record, Postgres LIKE patterns, Brazil CVM filter, full loop with send-backs, duplicate and supersession memory, unpublishable records, secret scrubbing, provider retries, unreachable Judge, boilerplate, replay, spenders, outcomes/playbook/scorecard, country and shareholder-vs-regulator gap, UK and foreign sources, prompt-injection guard, Ask SQL guard, benchmark export) |
| Meterless H-MEM conformance | `python -m pytest -q tests/test_hmem_conformance.py` | 11 of the engine's own conformance vectors pass (record validation, feedback deltas, ranking order, conflict gate, ledger audit) |
| Judge vs human editor | `python -m seismograph evaluate` | n = 17; agreement 71% (12/17); all 5 disagreements were the Judge passing a record the editor rejected; Cohen's kappa 0 |
| Judge benchmark (open data) | `python -m seismograph benchmark` → `out/judge_benchmark.jsonl` | Each human-reviewed record with its source passage, the draft, the Judge's verdict and the editor's decision; for evaluating or tuning judge models |
| Verbatim verification | every run; Verifier log lines | Every published quote appears in its source after normalising whitespace, letter case, typographic quotes and dashes (an independent check found one quote, NIQ, that differs only in curly versus straight quotation marks); TD Bank's paraphrased quotes were rejected three times and never published |
| Reproducibility | `SAMPLE_MODE=true` or `docker run` | Replays the recorded live run with no keys; reproduces the same classifications |
| Independent replication | `replication/REPLICATION.md` (Cursor agent, Grok 4.6, fresh clone) | 29 tests passed at commit `79cfe2e`; replay matches the published labels; 9/9 job-number strings and 4/5 AI quotes found verbatim in live sources (NIQ: curly vs straight quotes); headline recomputed exactly; 4 documentation discrepancies reported |
| Sample inputs and real outputs | `samples/*.input.json`, `samples/*.output.json` (`python -m seismograph samples`) | discover, ask, check-a-claim |

## How to read the Judge numbers
Kappa compares agreement with chance. Because the Judge passed every record that reached the editor, its verdict carried no information beyond "pass", so kappa is 0 even though raw agreement is 67%. That is why a human editor approves every record, and why the labelled set is published: it is the training and test set for a stricter Judge.
