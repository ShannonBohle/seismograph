"""Run our H-MEM implementation against the Meterless H-MEM conformance vectors.

tests/vectors/hmem_vectors.json is copied unchanged from
https://github.com/Meterless/Meterless/blob/main/engines/hmem/conformance/vectors/vectors.json
(Apache-2.0). The checks mirror engines/hmem/conformance/runner.ts for the five
sections this project implements. sleep-guardrails and dream-boundary are skipped
because the sleep cycle and dreaming are not implemented.
"""
import json
from pathlib import Path

import pytest

from seismograph.hmem import HMEM, RecordRejected

VECTORS = json.loads((Path(__file__).parent / "vectors" / "hmem_vectors.json").read_text())
T0 = VECTORS["clock"]
S = VECTORS["sections"]


def fresh():
    return HMEM(":memory:", clock=lambda: T0)


@pytest.mark.parametrize("case", S["record-validation"], ids=lambda c: c["id"])
def test_record_validation(case):
    h = fresh()
    if case["expect"] == "reject":
        with pytest.raises(RecordRejected):
            h.add(case["input"]["content"], source=case["input"]["source"])
    else:
        assert h.add(case["input"]["content"], source=case["input"]["source"]).id


@pytest.mark.parametrize("case", S["feedback-deltas"], ids=lambda c: c["id"])
def test_feedback_deltas(case):
    h = fresh()
    m = h.add("feedback target", source="conformance", confidence=case["start"])
    after = h.feedback(m.id, case["kind"])
    assert after.confidence == pytest.approx(case["expectConfidence"], abs=1e-9)
    if "expectAccessDelta" in case:
        assert after.accessCount - m.accessCount == case["expectAccessDelta"]
    if "expectTag" in case:
        assert case["expectTag"] in after.tags


@pytest.mark.parametrize("case", S["ranking-order"], ids=lambda c: c["id"])
def test_ranking_order(case):
    h = fresh()
    by_key = {}
    for r in case["records"]:
        by_key[r["key"]] = h.add(r["content"], source="conformance", confidence=r["confidence"], layer=r["layer"])
    for r in case["records"]:
        if r.get("supersededByKey"):
            h.mark_superseded(by_key[r["key"]].id, by_key[r["supersededByKey"]].id, reason="conformance")
    results = h.query(case["query"], threshold=0, top_n=10)
    id_to_key = {m.id: k for k, m in by_key.items()}
    order = [id_to_key[r["memory"].id] for r in results]
    assert order == case["expectOrder"]


@pytest.mark.parametrize("case", S["conflict-gate"], ids=lambda c: c["id"])
def test_conflict_gate(case):
    h = fresh()
    a = h.add(case["a"]["content"], source="conformance", confidence=case["a"]["confidence"])
    b = h.add(case["b"]["content"], source="conformance", confidence=case["b"]["confidence"])
    found = h.scan_conflicts()
    assert found, "opposing always/never pair on similar content must be detected"
    outcome = h.auto_resolve(found[0])
    assert outcome in ("resolved", "queued")
    if outcome == "resolved":
        assert any(m.supersededBy for m in (h.get(a.id), h.get(b.id)))
    assert h.get(a.id) and h.get(b.id), "conflict resolution must never delete records"


@pytest.mark.parametrize("case", S["ledger-audit"], ids=lambda c: c["id"])
def test_ledger_audit(case):
    h = fresh()
    m = h.add("audited record", source="conformance", confidence=case["start"])
    h.feedback(m.id, case["kind"])
    history = h.history(m.id)
    assert [e["action"] for e in history] == case["expectActions"]
    assert history[-1]["previousState"]["confidence"] == pytest.approx(case["expectPrev"])
    assert history[-1]["newState"]["confidence"] == pytest.approx(case["expectNew"])
