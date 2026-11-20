from __future__ import annotations

from typing import Any, Protocol

from .models import Evidence

SCHEMA_SQL = [
    "create schema if not exists incidentlens",
    """create table if not exists incidentlens.incidents (
  id text primary key,
  title text not null,
  scenario text,
  parse_warnings jsonb not null default '[]',
  created_at timestamptz not null default now()
)""",
    """create table if not exists incidentlens.artifacts (
  id bigserial primary key,
  incident_id text not null references incidentlens.incidents(id) on delete cascade,
  kind text not null check (kind in ('pipeline_log', 'change_events', 'report_md', 'report_json')),
  object_key text not null,
  filename text not null,
  sha256 text not null,
  size_bytes integer not null,
  created_at timestamptz not null default now()
)""",
    """create table if not exists incidentlens.evidence (
  incident_id text not null references incidentlens.incidents(id) on delete cascade,
  evidence_id text not null,
  source_kind text not null check (source_kind in ('pipeline_log', 'change_event')),
  line_no integer not null,
  ts timestamptz,
  level text,
  component text,
  event_type text,
  actor text,
  target text,
  message text not null,
  attributes jsonb not null default '{}',
  raw text not null,
  primary key (incident_id, evidence_id)
)""",
    """create table if not exists incidentlens.analyses (
  id bigserial primary key,
  incident_id text not null references incidentlens.incidents(id) on delete cascade,
  model text not null,
  prompt_version text not null,
  policy_version text not null,
  model_verdict text not null,
  final_verdict text not null,
  result jsonb not null,
  verification jsonb not null,
  raw_model_output text not null,
  latency_ms integer,
  prompt_tokens integer,
  completion_tokens integer,
  created_at timestamptz not null default now()
)""",
    """create table if not exists incidentlens.citations (
  analysis_id bigint not null references incidentlens.analyses(id) on delete cascade,
  incident_id text not null,
  evidence_id text not null,
  claim_ref text not null,
  role text not null check (role in ('supports', 'contradicts', 'action')),
  foreign key (incident_id, evidence_id) references incidentlens.evidence(incident_id, evidence_id)
)""",
]


class Store(Protocol):
    def ping(self) -> None: ...
    def create_incident(self, incident: dict[str, Any], artifacts: list[dict[str, Any]],
                        evidence: list[Evidence]) -> None: ...
    def list_incidents(self, limit: int = 20) -> list[dict[str, Any]]: ...
    def get_incident(self, incident_id: str) -> dict[str, Any] | None: ...
    def get_evidence(self, incident_id: str) -> list[Evidence]: ...
    def save_analysis(self, incident_id: str, record: dict[str, Any],
                      citations: list[tuple[str, str, str]]) -> int: ...
    def latest_analysis(self, incident_id: str) -> dict[str, Any] | None: ...


class PgStore:
    """PostgreSQL case file. psycopg reads the PG* settings from the environment."""

    def __init__(self) -> None:
        self._ready = False

    def _connect(self) -> Any:
        import psycopg
        try:
            conn = psycopg.connect(connect_timeout=10)
        except psycopg.OperationalError:
            # Managed PostgreSQL occasionally drops a first connection attempt; one retry is enough.
            conn = psycopg.connect(connect_timeout=10)
        if not self._ready:
            with conn.transaction():
                for statement in SCHEMA_SQL:
                    conn.execute(statement)
            self._ready = True
        return conn

    def ping(self) -> None:
        with self._connect() as conn:
            conn.execute("select 1")

    def create_incident(self, incident: dict[str, Any], artifacts: list[dict[str, Any]],
                        evidence: list[Evidence]) -> None:
        from psycopg.types.json import Jsonb
        with self._connect() as conn, conn.transaction():
            conn.execute(
                "insert into incidentlens.incidents (id, title, scenario, parse_warnings) values (%s, %s, %s, %s)",
                (incident["id"], incident["title"], incident.get("scenario"), Jsonb(incident.get("parse_warnings", []))),
            )
            for a in artifacts:
                conn.execute(
                    "insert into incidentlens.artifacts (incident_id, kind, object_key, filename, sha256, size_bytes)"
                    " values (%s, %s, %s, %s, %s, %s)",
                    (incident["id"], a["kind"], a["object_key"], a["filename"], a["sha256"], a["size_bytes"]),
                )
            with conn.cursor() as cur:
                cur.executemany(
                    "insert into incidentlens.evidence (incident_id, evidence_id, source_kind, line_no, ts, level,"
                    " component, event_type, actor, target, message, attributes, raw)"
                    " values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
                    [(incident["id"], e.evidence_id, e.source_kind, e.line_no, e.ts, e.level, e.component,
                      e.event_type, e.actor, e.target, e.message, Jsonb(e.attributes), e.raw) for e in evidence],
                )

    def list_incidents(self, limit: int = 20) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                "select i.id, i.title, i.scenario, i.created_at,"
                " (select final_verdict from incidentlens.analyses a where a.incident_id = i.id"
                "  order by a.id desc limit 1)"
                " from incidentlens.incidents i order by i.created_at desc limit %s", (limit,),
            ).fetchall()
        return [{"id": r[0], "title": r[1], "scenario": r[2], "created_at": r[3].isoformat(), "verdict": r[4]}
                for r in rows]

    def get_incident(self, incident_id: str) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute(
                "select id, title, scenario, parse_warnings, created_at from incidentlens.incidents where id = %s",
                (incident_id,),
            ).fetchone()
            if row is None:
                return None
            arts = conn.execute(
                "select kind, object_key, filename, sha256, size_bytes from incidentlens.artifacts"
                " where incident_id = %s order by id", (incident_id,),
            ).fetchall()
        return {
            "id": row[0], "title": row[1], "scenario": row[2], "parse_warnings": row[3],
            "created_at": row[4].isoformat(),
            "artifacts": [{"kind": a[0], "object_key": a[1], "filename": a[2], "sha256": a[3], "size_bytes": a[4]}
                          for a in arts],
        }

    def get_evidence(self, incident_id: str) -> list[Evidence]:
        with self._connect() as conn:
            rows = conn.execute(
                "select evidence_id, source_kind, line_no, ts, level, component, event_type, actor, target,"
                " message, attributes, raw from incidentlens.evidence where incident_id = %s"
                " order by source_kind desc, line_no", (incident_id,),
            ).fetchall()
        keys = ["evidence_id", "source_kind", "line_no", "ts", "level", "component", "event_type", "actor",
                "target", "message", "attributes", "raw"]
        return [Evidence(**dict(zip(keys, r))) for r in rows]

    def save_analysis(self, incident_id: str, record: dict[str, Any],
                      citations: list[tuple[str, str, str]]) -> int:
        from psycopg.types.json import Jsonb
        with self._connect() as conn, conn.transaction():
            analysis_id = conn.execute(
                "insert into incidentlens.analyses (incident_id, model, prompt_version, policy_version,"
                " model_verdict, final_verdict, result, verification, raw_model_output, latency_ms,"
                " prompt_tokens, completion_tokens) values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)"
                " returning id",
                (incident_id, record["model"], record["prompt_version"], record["policy_version"],
                 record["model_verdict"], record["verdict"], Jsonb(record["result"]),
                 Jsonb(record["result"]["verification"]), record["raw_model_output"], record.get("latency_ms"),
                 record.get("prompt_tokens"), record.get("completion_tokens")),
            ).fetchone()[0]
            with conn.cursor() as cur:
                cur.executemany(
                    "insert into incidentlens.citations (analysis_id, incident_id, evidence_id, claim_ref, role)"
                    " values (%s, %s, %s, %s, %s)",
                    [(analysis_id, incident_id, ev_id, ref, role) for ev_id, ref, role in citations],
                )
        return int(analysis_id)

    def latest_analysis(self, incident_id: str) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute(
                "select id, result, created_at from incidentlens.analyses where incident_id = %s"
                " order by id desc limit 1", (incident_id,),
            ).fetchone()
        if row is None:
            return None
        return {**row[1], "analysis_id": row[0], "created_at": row[2].isoformat()}
