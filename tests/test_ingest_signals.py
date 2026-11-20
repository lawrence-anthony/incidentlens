import json

import pytest

from incidentlens.ingest import IngestError, parse_change_events, parse_pipeline_log, sha256_hex
from incidentlens.signals import compute_signals, error_signatures, entity_tokens
from tests.conftest import SCENARIOS, load_scenario


def test_ids_are_stable_and_follow_line_numbers():
    data = b'{"ts": "2026-11-20T03:00:00Z", "level": "info", "message": "a"}\n\n{"ts": "2026-11-20T03:00:01Z", "level": "ERROR", "message": "b"}\n'
    parsed = parse_pipeline_log(data)
    assert [e.evidence_id for e in parsed.evidence] == ["LOG-0001", "LOG-0003"]
    assert parsed.evidence[0].level == "INFO"
    assert parsed.sha256 == sha256_hex(data)
    assert parse_pipeline_log(data).evidence == parsed.evidence


def test_malformed_line_is_kept_with_warning():
    parsed = parse_pipeline_log(b'not json at all\n{"level": "ERROR", "message": "x"}\n')
    assert parsed.evidence[0].ts is None and parsed.evidence[0].message == "not json at all"
    assert len(parsed.warnings) == 2


def test_limits_and_encoding():
    with pytest.raises(IngestError) as too_big:
        parse_pipeline_log(b"x" * 1_000_001)
    assert too_big.value.status_code == 413
    with pytest.raises(IngestError) as not_utf8:
        parse_pipeline_log(b"\xff\xfe")
    assert not_utf8.value.status_code == 415
    with pytest.raises(IngestError):
        parse_pipeline_log(b"\n\n")


def test_change_events_keep_structured_fields():
    parsed = parse_change_events((SCENARIOS / "a_iam_change" / "change_events.jsonl").read_bytes())
    chg = parsed.evidence[1]
    assert chg.evidence_id == "CHG-0002" and chg.event_type == "iam.binding_removed"
    assert chg.attributes["principal"] == "svc-etl-loader"


@pytest.mark.parametrize("slug", ["a_iam_change", "b_insufficient_evidence"])
def test_scenario_signals_match_expected(slug):
    expected = json.loads((SCENARIOS / slug / "expected.json").read_text())["deterministic"]
    signals = compute_signals(load_scenario(slug))
    assert signals["first_failure"] == expected["first_failure"]
    assert [l["change_id"] for l in signals["linked_changes"]] == expected["linked_changes"]
    assert signals["after_failure_changes"] == expected["after_failure_changes"]


def test_linked_change_shares_tokens_in_scenario_a():
    link = compute_signals(load_scenario("a_iam_change"))["linked_changes"][0]
    assert {"svc-etl-loader", "raw-orders-synth", "storage.objects.get"} <= set(link["shared_tokens"])
    assert "LOG-0005" in link["failure_ids"]


def test_timeline_is_time_ordered():
    evidence = {e.evidence_id: e for e in load_scenario("a_iam_change")}
    timeline = compute_signals(list(evidence.values()))["timeline"]
    stamps = [evidence[i].ts for i in timeline]
    assert stamps == sorted(stamps)
    assert timeline[0] == "CHG-0001"


def test_signature_catalog_ignores_generic_failures():
    assert error_signatures("403 AccessDenied: denied") == ["access_denied"]
    assert error_signatures("Task exited with non-zero status 1") == []
    assert error_signatures("DAG run inventory_sync failed") == []


def test_scenario_b_messages_have_no_signatures():
    for ev in load_scenario("b_insufficient_evidence"):
        assert error_signatures(ev.message) == [], ev.evidence_id


def test_entity_tokens_skip_dates_versions_and_words():
    tokens = entity_tokens("raw-orders-synth/orders/dt=2026-11-19/part-0001.json v1.8.2 bucket")
    assert "raw-orders-synth" in tokens and "2026-11-19" not in tokens
    assert "v1.8.2" not in tokens and "bucket" not in tokens
