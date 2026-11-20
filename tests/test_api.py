from fastapi.testclient import TestClient

from incidentlens.app import create_app
from incidentlens.ingest import sha256_hex
from tests.conftest import GOOD_A, SCENARIOS, FakeLLM, MemoryBlobs, MemoryStore


def make(analysis=GOOD_A):
    store, blobs, llm = MemoryStore(), MemoryBlobs(), FakeLLM(analysis)
    return TestClient(create_app(store, blobs, llm, SCENARIOS)), store, blobs, llm


def test_health_and_security_headers():
    client, *_ = make()
    response = client.get("/health")
    assert response.json() == {"inference": "configured", "database": "connected", "object_storage": "connected"}
    assert "default-src 'self'" in response.headers["content-security-policy"]
    assert response.headers["x-content-type-options"] == "nosniff"


def test_health_without_services():
    client = TestClient(create_app(None, None, None, SCENARIOS))
    assert client.get("/health").json() == {"inference": "not set", "database": "not set", "object_storage": "not set"}
    assert client.post("/api/incidents", json={"scenario": "a_iam_change"}).status_code == 503


def test_scenarios_listed():
    client, *_ = make()
    slugs = [s["slug"] for s in client.get("/api/scenarios").json()]
    assert slugs == ["a_iam_change", "b_insufficient_evidence"]


def test_scenario_a_end_to_end_with_fakes():
    client, store, blobs, llm = make()
    created = client.post("/api/incidents", json={"scenario": "a_iam_change"})
    assert created.status_code == 200
    detail = created.json()
    incident_id = detail["incident"]["id"]
    assert len(detail["evidence"]) == 18
    assert detail["signals"]["first_failure"] == "LOG-0005"
    keys = [a["object_key"] for a in detail["incident"]["artifacts"]]
    assert all(k.startswith(f"incidentlens/incidents/{incident_id}/raw/") for k in keys)
    assert all(sha256_hex(blobs.objects[a["object_key"]]) == a["sha256"] for a in detail["incident"]["artifacts"])

    analysis = client.post(f"/api/incidents/{incident_id}/analyze").json()
    assert analysis["verdict"] == "probable_cause"
    assert analysis["hypotheses"][0]["supporting"][0]["evidence_id"] == "CHG-0002"
    assert analysis["verification"]["evidence_sha256_verified"] is True
    assert all(a["requires_human_approval"] and not a["executed"] for a in analysis["recovery_actions"])
    assert "_citations" not in analysis
    assert store.analyses[0]["citations"]

    reopened = client.get(f"/api/incidents/{incident_id}").json()
    assert reopened["analysis"]["verdict"] == "probable_cause"
    assert llm.calls == 1


def test_tampered_evidence_is_refused():
    client, _, blobs, llm = make()
    incident_id = client.post("/api/incidents", json={"scenario": "a_iam_change"}).json()["incident"]["id"]
    key = next(k for k in blobs.objects if k.endswith("pipeline.jsonl"))
    blobs.objects[key] += b"\n"
    response = client.post(f"/api/incidents/{incident_id}/analyze")
    assert response.status_code == 422 and llm.calls == 0


def test_upload_flow_and_validation():
    client, *_ = make()
    pipeline = (SCENARIOS / "b_insufficient_evidence" / "pipeline.jsonl").read_bytes()
    response = client.post("/api/incidents", data={"title": "<script>x</script>"},
                           files={"pipeline_log": ("p.jsonl", pipeline, "application/json")})
    assert response.status_code == 200
    assert response.json()["incident"]["title"] == "<script>x</script>"
    bad = client.post("/api/incidents", files={"pipeline_log": ("p.jsonl", b"\xff\xfe", "text/plain")})
    assert bad.status_code == 415
    assert client.post("/api/incidents", json={"scenario": "../etc"}).status_code == 400
    assert client.get("/api/incidents/inc-missing").status_code == 404


def test_analysis_lock_returns_409():
    client, *_ = make()
    app = client.app
    incident_id = client.post("/api/incidents", json={"scenario": "a_iam_change"}).json()["incident"]["id"]
    lock = next(c.cell_contents for r in app.routes if getattr(r, "name", "") == "analyze"
                for c in (r.endpoint.__closure__ or []) if type(c.cell_contents).__name__ == "lock")
    lock.acquire()
    try:
        assert client.post(f"/api/incidents/{incident_id}/analyze").status_code == 409
    finally:
        lock.release()
