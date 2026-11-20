import copy
import json

import pytest

from incidentlens.inference import InferenceError, AkamaiLLM, build_payload, parse_content
from incidentlens.models import ModelAnalysis
from incidentlens.signals import compute_signals
from incidentlens.verify import risky_terms, verify
from tests.conftest import GOOD_A, load_scenario


def run(slug, analysis):
    evidence = load_scenario(slug)
    return verify(ModelAnalysis.model_validate(analysis), evidence, compute_signals(evidence))


def test_good_scenario_a_keeps_high_and_ranks_iam_first(good_a):
    out = run("a_iam_change", good_a)
    assert out["verdict"] == "probable_cause"
    h1, h2 = out["hypotheses"]
    assert h1["id"] == "H1" and h1["confidence"] == "high" and not h1["adjustments"]
    assert h2["confidence"] == "low" and h2["adjustments"][0]["rule"] == "R4"
    assert out["verification"]["quotes_matched"] == out["verification"]["quotes_checked"] == 4


def test_fabricated_ids_are_dropped_and_hypothesis_rejected(good_a):
    good_a["hypotheses"] = [{"id": "H1", "statement": "Disk filled up.", "category": "infrastructure",
                             "supporting": [{"evidence_id": "LOG-0999", "quote": "No space left", "why": "x"}],
                             "contradicting": [], "confidence": "high", "reasoning": "See LOG-0998."}]
    good_a["recovery_actions"][0]["evidence_ids"] = ["CHG-0777"]
    out = run("a_iam_change", good_a)
    h = out["hypotheses"][0]
    assert h["rejected"] and h["confidence"] == "low" and h["supporting"] == []
    assert out["verdict"] == "insufficient_evidence"
    assert set(out["verification"]["citations_invalid"]) == {"LOG-0999", "CHG-0777"}
    assert out["verification"]["free_text_unknown_ids"] == ["LOG-0998"]
    assert all(c[0] not in {"LOG-0999", "CHG-0777"} for c in out["_citations"])


def test_cause_after_effect_is_capped_low(good_a):
    good_a["hypotheses"][0]["supporting"].append({"evidence_id": "CHG-0004", "quote": "", "why": "x"})
    out = run("a_iam_change", good_a)
    h1 = next(h for h in out["hypotheses"] if h["id"] == "H1")
    assert h1["confidence"] == "low" and any(a["rule"] == "R3" for a in h1["adjustments"])


def test_contradicting_evidence_caps_at_medium(good_a):
    good_a["hypotheses"][0]["contradicting"] = [{"evidence_id": "LOG-0004", "why": "listing worked"}]
    out = run("a_iam_change", good_a)
    assert out["hypotheses"][0]["confidence"] == "medium"
    assert out["verdict"] == "probable_cause"


def test_verifier_never_raises_confidence_or_verdict(good_a):
    good_a["verdict"] = "insufficient_evidence"
    good_a["hypotheses"][0]["confidence"] = "low"
    out = run("a_iam_change", good_a)
    assert out["verdict"] == "insufficient_evidence"
    assert out["hypotheses"][0]["confidence"] == "low"


def test_overconfident_scenario_b_is_lowered():
    analysis = {
        "summary": "The schedule change caused it.", "verdict": "probable_cause",
        "hypotheses": [{"id": "H1", "statement": "Schedule move broke the feed.", "category": "config_change",
                        "supporting": [{"evidence_id": "CHG-0002", "quote": "Moved inventory_sync schedule", "why": "x"},
                                       {"evidence_id": "LOG-0005", "quote": "non-zero status 1", "why": "y"}],
                        "contradicting": [], "confidence": "high", "reasoning": "Timing."}],
        "missing_evidence": [], "recovery_actions": [],
    }
    out = run("b_insufficient_evidence", analysis)
    assert out["model_verdict"] == "probable_cause"
    assert out["verdict"] == "insufficient_evidence"
    assert out["hypotheses"][0]["confidence"] == "low"


def test_actions_always_require_approval_and_risky_wording_is_flagged(good_a):
    good_a["recovery_actions"].append({"id": "A2", "action": "Grant admin role to svc-etl-loader on all buckets.",
                                       "rationale": "Fast fix.", "evidence_ids": [], "risk": "low",
                                       "verify": "", "rollback": ""})
    out = run("a_iam_change", good_a)
    a1, a2 = out["recovery_actions"]
    assert all(a["requires_human_approval"] is True and a["executed"] is False for a in (a1, a2))
    assert a1["flags"] == [] and a1["risk"] == "low"
    assert a2["risk"] == "high" and "broad_or_destructive" in a2["flags"] and "general" in a2["flags"]


def test_risky_terms_respect_negation():
    assert risky_terms("Do not delete any data; confirm with the owner of CR-1042.") == []
    assert risky_terms("Delete the partition and retry") != []
    assert risky_terms("Grant storage.* to the loader") != []


def test_limits_are_enforced(good_a):
    good_a["hypotheses"] = [copy.deepcopy(GOOD_A["hypotheses"][0]) | {"id": f"H{i}"} for i in range(5)]
    out = run("a_iam_change", good_a)
    assert len(out["hypotheses"]) == 3 and out["verification"]["notes"]


def test_parse_content_strips_think_and_fences():
    body = json.dumps(GOOD_A)
    assert parse_content(f"<think>hmm</think>\n```json\n{body}\n```").verdict == "probable_cause"
    with pytest.raises(ValueError):
        parse_content('{"summary": "x"}')


class _Choice:
    def __init__(self, content, finish):
        self.message = type("M", (), {"content": content})()
        self.finish_reason = finish


class _Client:
    def __init__(self, replies):
        self.replies = list(replies)
        self.chat = type("C", (), {"completions": self})()
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        content, finish = self.replies.pop(0)
        return type("R", (), {"choices": [_Choice(content, finish)], "usage": None})()


def test_llm_retries_once_after_truncated_reply():
    client = _Client([("{\"summary\": ", "length"), (json.dumps(GOOD_A), "stop")])
    result = AkamaiLLM(client, "qwen3-8-27b").analyze({"x": 1})
    assert result.analysis.verdict == "probable_cause" and len(client.calls) == 2
    assert client.calls[0]["response_format"]["type"] == "json_schema"


def test_llm_gives_up_after_two_bad_replies():
    client = _Client([("nope", "stop"), ("still nope", "stop")])
    with pytest.raises(InferenceError):
        AkamaiLLM(client, "qwen3-8-27b").analyze({"x": 1})


def test_payload_contains_ids_and_signals():
    evidence = load_scenario("a_iam_change")
    payload = build_payload("t", evidence, compute_signals(evidence))
    assert payload["evidence"][0]["id"] == "LOG-0001"
    assert payload["signals"]["linked_changes"][0]["change_id"] == "CHG-0002"
