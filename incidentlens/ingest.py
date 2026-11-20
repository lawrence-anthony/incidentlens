from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from .models import Evidence

MAX_BYTES = 1_000_000
MAX_LINES = 2_000
MAX_LINE_CHARS = 4_000
LEVELS = {"DEBUG", "INFO", "WARN", "ERROR", "FATAL"}


class IngestError(ValueError):
    def __init__(self, message: str, status_code: int = 400) -> None:
        super().__init__(message)
        self.status_code = status_code


@dataclass
class ParsedFile:
    evidence: list[Evidence]
    sha256: str
    size: int
    warnings: list[str] = field(default_factory=list)


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def parse_ts(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        ts = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    return ts.astimezone(timezone.utc)


def _lines(data: bytes, label: str) -> list[tuple[int, str, list[str]]]:
    if len(data) > MAX_BYTES:
        raise IngestError(f"{label} is larger than 1 MB.", 413)
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        raise IngestError(f"{label} must be UTF-8 text.", 415) from None
    rows = text.splitlines()
    if len(rows) > MAX_LINES:
        raise IngestError(f"{label} has more than {MAX_LINES} lines.", 413)
    out = []
    for number, line in enumerate(rows, start=1):
        warnings: list[str] = []
        if not line.strip():
            continue
        if len(line) > MAX_LINE_CHARS:
            line = line[:MAX_LINE_CHARS]
            warnings.append(f"{label} line {number} was truncated to {MAX_LINE_CHARS} characters.")
        out.append((number, line, warnings))
    return out


def _load_object(line: str) -> dict[str, Any] | None:
    try:
        obj = json.loads(line)
    except json.JSONDecodeError:
        return None
    return obj if isinstance(obj, dict) else None


def _text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def parse_pipeline_log(data: bytes) -> ParsedFile:
    evidence: list[Evidence] = []
    warnings: list[str] = []
    for number, line, line_warnings in _lines(data, "Pipeline log"):
        warnings.extend(line_warnings)
        evidence_id = f"LOG-{number:04d}"
        obj = _load_object(line)
        if obj is None:
            warnings.append(f"{evidence_id}: not a JSON object; kept as untimed text.")
            evidence.append(Evidence(evidence_id=evidence_id, source_kind="pipeline_log", line_no=number,
                                     message=line.strip(), raw=line))
            continue
        level = (_text(obj.pop("level", None)) or "").upper()
        level = "WARN" if level == "WARNING" else level
        ts_value = obj.pop("ts", None)
        ts = parse_ts(ts_value)
        if ts is None:
            warnings.append(f"{evidence_id}: missing or invalid ts.")
        evidence.append(Evidence(
            evidence_id=evidence_id, source_kind="pipeline_log", line_no=number, ts=ts,
            level=level if level in LEVELS else None,
            component=_text(obj.pop("component", None)),
            message=_text(obj.pop("message", None)) or "",
            attributes=obj, raw=line,
        ))
    if not evidence:
        raise IngestError("The pipeline log has no lines.")
    return ParsedFile(evidence, sha256_hex(data), len(data), warnings)


def parse_change_events(data: bytes) -> ParsedFile:
    evidence: list[Evidence] = []
    warnings: list[str] = []
    for number, line, line_warnings in _lines(data, "Change events"):
        warnings.extend(line_warnings)
        evidence_id = f"CHG-{number:04d}"
        obj = _load_object(line)
        if obj is None:
            warnings.append(f"{evidence_id}: not a JSON object; kept as untimed text.")
            evidence.append(Evidence(evidence_id=evidence_id, source_kind="change_event", line_no=number,
                                     message=line.strip(), raw=line))
            continue
        ts = parse_ts(obj.pop("ts", None))
        if ts is None:
            warnings.append(f"{evidence_id}: missing or invalid ts.")
        evidence.append(Evidence(
            evidence_id=evidence_id, source_kind="change_event", line_no=number, ts=ts,
            event_type=_text(obj.pop("event_type", None)),
            actor=_text(obj.pop("actor", None)),
            target=_text(obj.pop("target", None)),
            message=_text(obj.pop("summary", None)) or "",
            attributes=obj, raw=line,
        ))
    return ParsedFile(evidence, sha256_hex(data), len(data), warnings)
