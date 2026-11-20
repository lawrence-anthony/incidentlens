from __future__ import annotations

import json
import logging
import re
import secrets
import threading
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from .blobs import Blobs, raw_key
from .inference import LLM, PROMPT_VERSION, InferenceError, build_payload
from .ingest import IngestError, ParsedFile, parse_change_events, parse_pipeline_log, sha256_hex
from .signals import compute_signals
from .store import Store
from .verify import POLICY_VERSION, verify

log = logging.getLogger("incidentlens")
STATIC = Path(__file__).parent / "static"
SCENARIOS = Path(__file__).resolve().parent.parent / "scenarios"
SECURITY_HEADERS = {
    "Content-Security-Policy": "default-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'",
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
}


class ServiceUnavailable(Exception):
    def __init__(self, service: str) -> None:
        super().__init__(service)
        self.service = service


def _status(check: Any) -> str:
    try:
        check()
        return "connected"
    except Exception as error:
        log.warning("health check failed: %s", type(error).__name__)
        return f"error: {type(error).__name__}"


def list_scenarios(root: Path) -> list[dict[str, str]]:
    out = []
    for folder in sorted(p for p in root.iterdir() if p.is_dir()) if root.is_dir() else []:
        meta_path = folder / "expected.json"
        if meta_path.is_file():
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
            out.append({"slug": folder.name, "title": meta.get("title", folder.name),
                        "description": meta.get("description", "")})
    return out


def _evidence_json(evidence: list[Any]) -> list[dict[str, Any]]:
    return [e.model_dump(mode="json") for e in evidence]


def create_app(store: Store | None, blobs: Blobs | None, llm: LLM | None,
               scenarios_dir: Path = SCENARIOS) -> FastAPI:
    app = FastAPI(title="IncidentLens", docs_url=None, redoc_url=None, openapi_url=None)
    app.mount("/static", StaticFiles(directory=STATIC), name="static")
    analysis_lock = threading.Lock()

    @app.middleware("http")
    async def security_headers(request: Request, call_next: Any) -> Any:
        response = await call_next(request)
        response.headers.update(SECURITY_HEADERS)
        return response

    @app.exception_handler(ServiceUnavailable)
    async def unavailable(_: Request, error: ServiceUnavailable) -> JSONResponse:
        return JSONResponse({"detail": f"{error.service} is unavailable. Check /health."}, status_code=503)

    def need_store() -> Store:
        if store is None:
            raise HTTPException(503, "The database is not configured.")
        return store

    def need_blobs() -> Blobs:
        if blobs is None:
            raise HTTPException(503, "Object Storage is not configured.")
        return blobs

    def guarded(service: str, fn: Any, *args: Any) -> Any:
        try:
            return fn(*args)
        except HTTPException:
            raise
        except Exception as error:
            log.warning("%s call failed: %s", service, type(error).__name__)
            raise ServiceUnavailable(service) from None

    @app.get("/", include_in_schema=False)
    def page() -> FileResponse:
        return FileResponse(STATIC / "index.html")

    @app.get("/health")
    def health() -> dict[str, str]:
        return {
            "inference": "configured" if llm else "not set",
            "database": _status(store.ping) if store else "not set",
            "object_storage": _status(blobs.ping) if blobs else "not set",
        }

    @app.get("/api/scenarios")
    def scenarios() -> list[dict[str, str]]:
        return list_scenarios(scenarios_dir)

    def ingest(title: str, scenario: str | None, pipeline: tuple[str, bytes],
               changes: tuple[str, bytes] | None) -> dict[str, Any]:
        db, bucket = need_store(), need_blobs()
        try:
            parsed_log = parse_pipeline_log(pipeline[1])
            parsed_changes: ParsedFile | None = parse_change_events(changes[1]) if changes else None
        except IngestError as error:
            raise HTTPException(error.status_code, str(error)) from None
        incident_id = f"inc-{secrets.token_hex(5)}"
        files = [("pipeline_log", "pipeline.jsonl", pipeline, parsed_log)]
        if changes and parsed_changes:
            files.append(("change_events", "change_events.jsonl", changes, parsed_changes))
        artifacts = []
        for kind, name, (original, data), parsed in files:
            key = raw_key(incident_id, name)
            guarded("Object Storage", bucket.put, key, data, {"sha256": parsed.sha256, "incident-id": incident_id})
            artifacts.append({"kind": kind, "object_key": key, "filename": original,
                              "sha256": parsed.sha256, "size_bytes": parsed.size})
        evidence = parsed_log.evidence + (parsed_changes.evidence if parsed_changes else [])
        warnings = parsed_log.warnings + (parsed_changes.warnings if parsed_changes else [])
        incident = {"id": incident_id, "title": title, "scenario": scenario, "parse_warnings": warnings}
        guarded("The database", db.create_incident, incident, artifacts, evidence)
        return detail(incident_id)

    @app.post("/api/incidents")
    async def create_incident(request: Request) -> dict[str, Any]:
        if request.headers.get("content-type", "").startswith("application/json"):
            body = await request.json()
            slug = body.get("scenario") if isinstance(body, dict) else None
            known = {s["slug"]: s for s in list_scenarios(scenarios_dir)}
            if not isinstance(slug, str) or not re.fullmatch(r"[a-z0-9_]+", slug) or slug not in known:
                raise HTTPException(400, "Unknown scenario.")
            folder = scenarios_dir / slug
            changes_path = folder / "change_events.jsonl"
            changes = ("change_events.jsonl", changes_path.read_bytes()) if changes_path.is_file() else None
            return ingest(known[slug]["title"], slug, ("pipeline.jsonl", (folder / "pipeline.jsonl").read_bytes()),
                          changes)
        form = await request.form()
        pipeline_file = form.get("pipeline_log")
        if pipeline_file is None or isinstance(pipeline_file, str):
            raise HTTPException(400, "Choose a pipeline log file.")
        title = str(form.get("title") or "").strip()[:200] or "Uploaded incident"
        pipeline_data = await pipeline_file.read(2_000_000)
        changes_upload = form.get("change_events")
        changes_tuple = None
        if changes_upload is not None and not isinstance(changes_upload, str) and changes_upload.filename:
            changes_data = await changes_upload.read(2_000_000)
            if changes_data.strip():
                changes_tuple = (Path(changes_upload.filename).name[:100], changes_data)
        return ingest(title, None, (Path(pipeline_file.filename or "pipeline").name[:100], pipeline_data),
                      changes_tuple)

    @app.get("/api/incidents")
    def incidents() -> list[dict[str, Any]]:
        return guarded("The database", need_store().list_incidents)

    def detail(incident_id: str) -> dict[str, Any]:
        db = need_store()
        incident = guarded("The database", db.get_incident, incident_id)
        if incident is None:
            raise HTTPException(404, "Incident not found.")
        evidence = guarded("The database", db.get_evidence, incident_id)
        return {
            "incident": incident,
            "evidence": _evidence_json(evidence),
            "signals": compute_signals(evidence),
            "analysis": guarded("The database", db.latest_analysis, incident_id),
        }

    @app.get("/api/incidents/{incident_id}")
    def get_incident(incident_id: str) -> dict[str, Any]:
        return detail(incident_id)

    @app.post("/api/incidents/{incident_id}/analyze")
    def analyze(incident_id: str) -> dict[str, Any]:
        db, bucket = need_store(), need_blobs()
        if llm is None:
            raise HTTPException(503, "Inference is not configured.")
        if not analysis_lock.acquire(blocking=False):
            raise HTTPException(409, "Another analysis is running. Try again when it finishes.")
        try:
            incident = guarded("The database", db.get_incident, incident_id)
            if incident is None:
                raise HTTPException(404, "Incident not found.")
            for artifact in incident["artifacts"]:
                if artifact["kind"] in ("pipeline_log", "change_events"):
                    data = guarded("Object Storage", bucket.get, artifact["object_key"])
                    if sha256_hex(data) != artifact["sha256"]:
                        raise HTTPException(422, "Evidence integrity check failed: a stored file changed.")
            evidence = guarded("The database", db.get_evidence, incident_id)
            signals = compute_signals(evidence)
            try:
                result = llm.analyze(build_payload(incident["title"], evidence, signals))
            except InferenceError as error:
                raise HTTPException(error.status_code, str(error)) from None
            verified = verify(result.analysis, evidence, signals)
            citations = verified.pop("_citations")
            verified["verification"]["evidence_sha256_verified"] = True
            verified.update({"model": result.model, "prompt_version": PROMPT_VERSION,
                             "policy_version": POLICY_VERSION, "latency_ms": result.latency_ms,
                             "prompt_tokens": result.prompt_tokens, "completion_tokens": result.completion_tokens})
            record = {"model": result.model, "prompt_version": PROMPT_VERSION, "policy_version": POLICY_VERSION,
                      "model_verdict": verified["model_verdict"], "verdict": verified["verdict"],
                      "result": verified, "raw_model_output": result.raw, "latency_ms": result.latency_ms,
                      "prompt_tokens": result.prompt_tokens, "completion_tokens": result.completion_tokens}
            analysis_id = guarded("The database", db.save_analysis, incident_id, record, citations)
            return {**verified, "analysis_id": analysis_id}
        finally:
            analysis_lock.release()

    return app
