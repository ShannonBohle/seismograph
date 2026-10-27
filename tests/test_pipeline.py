"""End-to-end pipeline test with test doubles for SEC and Nightshift.

The filing text below is SYNTHETIC (an invented company), written for this test only.
The scripted model answers exercise the real agent code paths: Verifier send-back,
Judge send-back, H-MEM memory creation, duplicate detection and the publishing rule.
"""
import json
from types import SimpleNamespace

import pytest

from seismograph import config
from seismograph.context import Context
from seismograph.hmem import HMEM
from seismograph.logs import TraceLog
from seismograph.nightshift import Completion
from seismograph.pipeline import Pipeline
from seismograph.replay import Replay
from seismograph.review import decide, evaluate, publish
from seismograph.store import Store
from seismograph.zetaris import Zetaris

FILING = """EXAMPLE WIDGETS CORP (synthetic test filing)

Item 2.05 Costs Associated with Exit or Disposal Activities.

On March 3, 2026, Example Widgets Corp. approved a plan to reduce its global workforce by approximately 1,200 positions.

"As AI tools now handle a large share of first-line support, we are reducing roles in our customer operations organization," said the Chief Executive Officer.

The Company expects to incur severance charges of $40 million."""

QUOTE = ('As AI tools now handle a large share of first-line support, we are reducing roles in our customer '
         'operations organization,')


class FakeEdgar:
    def search(self, query, forms, start, end, max_hits=40):
        return [{"accession": "0000000000-26-000001", "cik": "999", "company": "Example Widgets Corp",
                 "form": "8-K", "file_date": "2026-03-03", "items": "2.05", "filename": "ex991.htm",
                 "file_type": "EX-99.1", "url": "https://www.sec.gov/Archives/edgar/data/999/000000000026000001/ex991.htm",
                 "index_url": ""}]

    def fetch_text(self, url):
        return FILING


class ScriptedNightshift:
    """Returns pre-written answers in order. Test double only."""

    def __init__(self, answers):
        self.answers = list(answers)
        self.calls = []

    def chat(self, model, system, user, agent):
        self.calls.append((agent, user))
        text = json.dumps(self.answers.pop(0))
        return Completion(text=text, requested_model=model, served_model="nvidia/nemotron-3-super-120b-a12b",
                          is_nemotron=True, usage={})


def make_ctx(tmp_path, answers):
    s = config.load()
    s.sample_mode = False
    s.out_dir = tmp_path / "out"
    s.zetaris_db = "seismo_pg"
    log = TraceLog(tmp_path / "logs", echo=False)
    store = Store(None, sqlite_path=tmp_path / "evidence.sqlite")
    store.init()
    z = Zetaris(s, log, Replay(tmp_path / "sample", sample_mode=True), local_store=store)
    ns = ScriptedNightshift(answers)
    ctx = Context(s, log, Replay(tmp_path / "sample", sample_mode=False), store, z, ns, FakeEdgar(),
                  HMEM(tmp_path / "hmem.sqlite"))
    return ctx, ns


DRAFT_PARAPHRASED = {"is_workforce_reduction": True, "company": "Example Widgets Corp", "event_date": "2026-03-03",
                     "jobs_cut": 1200, "jobs_cut_text": "approximately 1,200 positions", "ai_attribution": "explicit",
                     "cut_quote": "On March 3, 2026, Example Widgets Corp. approved a plan to reduce its global workforce by approximately 1,200 positions.",
                     "ai_quote": "AI handles first-line support so we are cutting roles", "notes": ""}
DRAFT_EXACT = {**DRAFT_PARAPHRASED, "ai_quote": QUOTE}
JUDGE_FAIL = {"verdict": "fail", "label": "explicit", "problems": ["event_date should be the approval date"],
              "critique": "Confirm the date"}
JUDGE_PASS = {"verdict": "pass", "label": "explicit", "problems": [], "critique": ""}


def test_full_loop_with_send_backs_memory_and_publishing(tmp_path):
    ctx, ns = make_ctx(tmp_path, [DRAFT_PARAPHRASED, DRAFT_EXACT, JUDGE_FAIL, DRAFT_EXACT, JUDGE_PASS])
    stats = Pipeline(ctx).run(days=30, end="2026-03-10", max_docs=5)
    assert stats["pending_review"] == 1 and stats["errors"] == 0

    agents = [a for a, _ in ns.calls]
    assert agents == ["Extractor", "Extractor", "Judge", "Extractor", "Judge"]
    assert "does not appear verbatim" in ns.calls[1][1]          # Verifier critique reached the Extractor
    assert "Confirm the date" in ns.calls[3][1]                  # Judge critique reached the Extractor

    rec = ctx.store.fetch("SELECT * FROM records")[0]
    assert rec["status"] == "pending_review" and rec["judge_verdict"] == "pass" and rec["quote_verified"]
    assert rec["memory_id"] and ctx.hmem.get(rec["memory_id"]).confidence == pytest.approx(0.6)

    # Nothing is published before a human approves.
    assert publish(ctx)["published"] == 0
    decide(ctx, rec["record_id"], True, "checked against filing")
    m = ctx.hmem.get(rec["memory_id"])
    assert m.confidence == pytest.approx(0.65) and m.layer == "long_term"
    assert [e["action"] for e in ctx.hmem.history(m.id)][-2:] == ["feedback", "promote"]
    assert publish(ctx)["published"] == 1
    html = (ctx.settings.out_dir / "index.html").read_text()
    assert "Example Widgets Corp" in html and "first-line support" in html
    assert evaluate(ctx)["n"] == 1

    events = [json.loads(l) for l in ctx.log.path.read_text().splitlines()]
    models = {e["model"] for e in events if e["action"] == "model_call"}
    assert models == {"nvidia/nemotron-3-super-120b-a12b"}
    assert any(e["action"] == "zetaris_query" for e in events)
    assert any(e["action"] == "hmem_create" for e in events)


def test_second_document_for_same_event_is_a_duplicate(tmp_path):
    ctx, ns = make_ctx(tmp_path, [DRAFT_EXACT, JUDGE_PASS])
    Pipeline(ctx).run(days=30, end="2026-03-10")
    # Same filing seen again as a different document (e.g. the 8-K body).
    ctx.edgar.search = lambda *a, **k: [{**FakeEdgar().search("", "", "", "")[0], "filename": "form8k.htm", "file_type": "8-K",
                                         "url": "https://www.sec.gov/Archives/edgar/data/999/000000000026000001/form8k.htm"}]
    ns.answers = [DRAFT_EXACT, JUDGE_PASS]
    stats = Pipeline(ctx).run(days=30, end="2026-03-10")
    assert stats.get("duplicates") == 1
    statuses = sorted(r["status"] for r in ctx.store.fetch("SELECT status FROM records"))
    assert statuses == ["duplicate", "pending_review"]


def test_record_that_never_passes_is_not_publishable(tmp_path):
    ctx, ns = make_ctx(tmp_path, [DRAFT_PARAPHRASED] * 3)
    stats = Pipeline(ctx).run(days=30, end="2026-03-10")
    assert stats["judge_failed"] == 1
    rec = ctx.store.fetch("SELECT * FROM records")[0]
    assert not rec["quote_verified"] and rec["memory_id"] is None
    decide(ctx, rec["record_id"], True, "")
    assert publish(ctx)["published"] == 0


def test_log_scrubs_secrets(tmp_path):
    log = TraceLog(tmp_path, echo=False)
    log.log("X", "y", output_summary="key nvapi-abc123DEF and Bearer eyJhbGciOi.eyJzdWIi.sig", api_key="nvapi-zzz")
    text = log.path.read_text()
    assert "nvapi-abc" not in text and "nvapi-zzz" not in text and "eyJ" not in text


class FlakyNightshift(ScriptedNightshift):
    """Raises a provider error on the listed call numbers (1-based). Test double only."""

    def __init__(self, answers, fail_on):
        super().__init__(answers)
        self.fail_on, self.n = set(fail_on), 0

    def chat(self, model, system, user, agent):
        self.n += 1
        if self.n in self.fail_on:
            from seismograph.nightshift import ProviderError
            raise ProviderError("Nightshift HTTP 502: the answer starts with prose")
        return super().chat(model, system, user, agent)


def test_transient_provider_error_is_retried(tmp_path):
    ctx, _ = make_ctx(tmp_path, [])
    ctx.nightshift = FlakyNightshift([DRAFT_EXACT, JUDGE_PASS], fail_on=[2])  # first Judge call fails once
    stats = Pipeline(ctx).run(days=30, end="2026-03-10")
    assert stats["pending_review"] == 1 and stats["errors"] == 0
    events = [json.loads(l) for l in ctx.log.path.read_text().splitlines()]
    assert any(e["action"] == "model_call" and e["status"] == "failure" for e in events)


def test_unreachable_judge_holds_record_back(tmp_path):
    ctx, _ = make_ctx(tmp_path, [])
    ctx.nightshift = FlakyNightshift([DRAFT_EXACT], fail_on=[2, 3, 4])
    stats = Pipeline(ctx).run(days=30, end="2026-03-10")
    assert stats["judge_failed"] == 1 and stats["errors"] == 0
    rec = ctx.store.fetch("SELECT * FROM records")[0]
    assert rec["judge_verdict"] == "not_reached" and rec["memory_id"] is None


BOILERPLATE_FILING = """EXAMPLE MEDIA INC (synthetic test filing)

Example Media today reported second-quarter revenue of $40 million.

This press release contains forward-looking statements, including the effectiveness of our restructuring and workforce reduction program and artificial intelligence laws."""


def test_boilerplate_is_not_an_announcement():
    from seismograph.agents import Draft, Verifier
    from seismograph.logs import TraceLog
    import tempfile, pathlib
    d = Draft(data={"is_workforce_reduction": True, "ai_attribution": "none", "ai_quote": None, "jobs_cut": None,
                    "jobs_cut_text": None,
                    "cut_quote": "the effectiveness of our restructuring and workforce reduction program"})
    problems = Verifier(TraceLog(pathlib.Path(tempfile.mkdtemp()), echo=False)).check(d, BOILERPLATE_FILING, retry=0)
    assert any("boilerplate" in p for p in problems)
