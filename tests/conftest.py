from __future__ import annotations

import copy
from pathlib import Path
from typing import Any

import pytest

from incidentlens.inference import LLMResult
from incidentlens.ingest import parse_change_events, parse_pipeline_log
from incidentlens.models import Evidence, ModelAnalysis

ROOT = Path(__file__).resolve().parent.parent
SCENARIOS = ROOT / "scenarios"


def load_scenario(slug: str) -> list[Evidence]:
    folder = SCENARIOS / slug
    return (parse_pipeline_log((folder / "pipeline.jsonl").read_bytes()).evidence
            + parse_change_events((folder / "change_events.jsonl").read_bytes()).evidence)


class MemoryStore:
    def __init__(self) -> None:
        self.incidents: dict[str, dict[str, Any]] = {}
        self.evidence: dict[str, list[Evidence]] = {}
        self.analyses: list[dict[str, Any]] = []

    def ping(self) -> None:
        pass

    def create_incident(self, incident, artifacts, evidence) -> None:
        self.incidents[incident["id"]] = {**incident, "created_at": "2026-11-20T00:00:00+00:00",
                                          "artifacts": artifacts}
        self.evidence[incident["id"]] = list(evidence)

    def list_incidents(self, limit: int = 20):
        return [{"id": i["id"], "title": i["title"], "scenario": i["scenario"], "created_at": i["created_at"],
                 "verdict": None} for i in self.incidents.values()][:limit]

    def get_incident(self, incident_id):
        return copy.deepcopy(self.incidents.get(incident_id))

    def get_evidence(self, incident_id):
        return list(self.evidence.get(incident_id, []))

    def save_analysis(self, incident_id, record, citations) -> int:
        known = {e.evidence_id for e in self.evidence[incident_id]}
        missing = [c for c in citations if c[0] not in known]
        if missing:
            raise AssertionError(f"foreign key violation: {missing}")
        self.analyses.append({"incident_id": incident_id, **record, "citations": citations})
        return len(self.analyses)

    def latest_analysis(self, incident_id):
        rows = [a for a in self.analyses if a["incident_id"] == incident_id]
        return {**rows[-1]["result"], "analysis_id": len(rows)} if rows else None


class MemoryBlobs:
    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}

    def ping(self) -> None:
        pass

    def put(self, key, data, metadata) -> None:
        self.objects[key] = data

    def get(self, key) -> bytes:
        return self.objects[key]


class FakeLLM:
    model = "fake-model"

    def __init__(self, analysis: dict[str, Any]) -> None:
        self.analysis = analysis
        self.calls = 0

    def analyze(self, payload):
        self.calls += 1
        return LLMResult(analysis=ModelAnalysis.model_validate(self.analysis), raw="{}", model=self.model,
                         latency_ms=1)


GOOD_A = {
    "summary": "extract_orders failed with AccessDenied after CHG-0002 removed read access.",
    "verdict": "probable_cause",
    "hypotheses": [
        {"id": "H1", "statement": "IAM change CR-1042 removed storage.objects.get for svc-etl-loader.",
         "category": "permissions",
         "supporting": [
             {"evidence_id": "CHG-0002", "quote": "removed storage.objects.get for principal svc-etl-loader", "why": "cause"},
             {"evidence_id": "LOG-0005", "quote": "403 AccessDenied", "why": "effect"}],
         "contradicting": [], "confidence": "high", "reasoning": "Change precedes the failure and names the same principal."},
        {"id": "H2", "statement": "The orders_daily deploy broke extraction.", "category": "code_change",
         "supporting": [{"evidence_id": "CHG-0001", "quote": "Deployed orders_daily v1.8.2", "why": "recent deploy"},
                        {"evidence_id": "LOG-0005", "quote": "AccessDenied", "why": "failure"}],
         "contradicting": [], "confidence": "medium", "reasoning": "Deploy happened before the run."},
    ],
    "missing_evidence": [{"need": "Confirmation from the CR-1042 owner", "why": "Was the removal intended?"}],
    "recovery_actions": [
        {"id": "A1", "action": "Restore storage.objects.get for svc-etl-loader on raw-orders-synth through a reviewed change.",
         "rationale": "Least privilege restore.", "evidence_ids": ["CHG-0002"], "risk": "low",
         "verify": "Re-run extract_orders.", "rollback": "Remove the binding again."}],
}


@pytest.fixture
def good_a() -> dict[str, Any]:
    return copy.deepcopy(GOOD_A)
