from __future__ import annotations

import re
from datetime import datetime
from typing import Any

from .models import Evidence

SIGNATURES: dict[str, re.Pattern[str]] = {
    "access_denied": re.compile(r"AccessDenied|Access Denied|\b403\b|Forbidden|PermissionDenied|permission denied|Unauthorized|\b401\b", re.I),
    "out_of_memory": re.compile(r"OutOfMemory|OOMKilled|exit code 137", re.I),
    "timeout": re.compile(r"timed out|timeout|deadline exceeded", re.I),
    "connection": re.compile(r"connection refused|connection reset|name or service not known", re.I),
    "not_found": re.compile(r"NoSuchKey|NoSuchBucket|\b404\b|no such file", re.I),
    "schema": re.compile(r"column \S+ does not exist|schema mismatch", re.I),
    "quota": re.compile(r"\b429\b|quota exceeded|rate limit", re.I),
    "disk": re.compile(r"no space left|disk full", re.I),
}
_SPLIT = re.compile(r"[\s/=:(),;'\"\[\]{}<>]+")
_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}")
_VERSION = re.compile(r"^v?\d+(\.\d+)+$")


def error_signatures(text: str) -> list[str]:
    return [name for name, pattern in SIGNATURES.items() if pattern.search(text)]


def entity_tokens(text: str | None) -> set[str]:
    tokens: set[str] = set()
    for token in _SPLIT.split(text or ""):
        token = token.strip(".,").lower()
        if (len(token) >= 4 and re.search(r"[-_.]", token) and re.search(r"[a-z]", token)
                and not _DATE.match(token) and not _VERSION.match(token) and "@" not in token):
            tokens.add(token)
    return tokens


def change_tokens(change: Evidence) -> set[str]:
    attrs = change.attributes
    fields = [change.target, attrs.get("principal"), attrs.get("permission"), attrs.get("component")]
    tokens: set[str] = set()
    for value in fields:
        if isinstance(value, str):
            tokens |= entity_tokens(value)
    return tokens


def _key(ev: Evidence) -> tuple[datetime, str, int]:
    assert ev.ts is not None
    return (ev.ts, ev.source_kind, ev.line_no)


def compute_signals(evidence: list[Evidence]) -> dict[str, Any]:
    timed = sorted((ev for ev in evidence if ev.ts is not None), key=_key)
    failures = [ev for ev in timed if ev.source_kind == "pipeline_log" and ev.level in {"ERROR", "FATAL"}]
    untimed_failures = [ev for ev in evidence if ev.ts is None and ev.level in {"ERROR", "FATAL"}]
    first = failures[0] if failures else None
    signatures = {ev.evidence_id: sigs for ev in failures + untimed_failures if (sigs := error_signatures(ev.message))}
    failure_tokens = {ev.evidence_id: entity_tokens(ev.message) for ev in failures if ev.evidence_id in signatures}

    linked: list[dict[str, Any]] = []
    before: list[str] = []
    after: list[str] = []
    for change in (ev for ev in timed if ev.source_kind == "change_event"):
        if first is None or first.ts is None:
            continue
        if change.ts is not None and change.ts > first.ts:
            after.append(change.evidence_id)
            continue
        before.append(change.evidence_id)
        tokens = change_tokens(change)
        matches = {fid: sorted(tokens & ftoks) for fid, ftoks in failure_tokens.items() if tokens & ftoks}
        if matches:
            shared = sorted({t for ts in matches.values() for t in ts})
            linked.append({"change_id": change.evidence_id, "failure_ids": sorted(matches), "shared_tokens": shared})

    return {
        "timeline": [ev.evidence_id for ev in timed],
        "untimed": [ev.evidence_id for ev in evidence if ev.ts is None],
        "failures": [ev.evidence_id for ev in failures + untimed_failures],
        "first_failure": first.evidence_id if first else None,
        "first_failure_ts": first.ts.isoformat().replace("+00:00", "Z") if first and first.ts else None,
        "error_signatures": signatures,
        "changes_before_failure": before,
        "linked_changes": linked,
        "after_failure_changes": after,
    }
