from __future__ import annotations

import re
from typing import Any

from .models import Evidence, ModelAnalysis

POLICY_VERSION = "v1"
RANK = {"low": 0, "medium": 1, "high": 2}
_ID = re.compile(r"\b(?:LOG|CHG)-\d{4}\b")
_RISKY = re.compile(
    r"\b(delet\w*|drop\w*|truncat\w*|purg\w*|wipe\w*|disabl\w*|bypass\w*|all permissions|full access)\b"
    r"|\b(grant|assign|add)\b[^.]{0,40}\b(admin\w*|owner\w*|editor)\b"
    r"|[:./]\*",
    re.I,
)
_NEGATION = re.compile(r"\b(not|never|avoid|without|no|don't|do not)\b[^.]{0,20}$", re.I)


def _norm(text: str) -> str:
    return " ".join(text.lower().split())


def risky_terms(text: str) -> list[str]:
    hits = []
    for match in _RISKY.finditer(text):
        if not _NEGATION.search(text[max(0, match.start() - 30):match.start()]):
            hits.append(match.group(0))
    return hits


def _cap(caps: list[tuple[str, str, str]], level: str, rule: str, reason: str) -> None:
    caps.append((level, rule, reason))


def verify(analysis: ModelAnalysis, evidence: list[Evidence], signals: dict[str, Any]) -> dict[str, Any]:
    by_id = {ev.evidence_id: ev for ev in evidence}
    first_id = signals.get("first_failure")
    first_ts = by_id[first_id].ts if first_id in by_id else None
    failures = set(signals.get("failures", []))
    signed = set(signals.get("error_signatures", {}))
    linked = {item["change_id"]: set(item["failure_ids"]) for item in signals.get("linked_changes", [])}

    notes: list[str] = []
    invalid: list[str] = []
    quotes_checked = quotes_matched = citations_total = 0
    citations: list[tuple[str, str, str]] = []

    hyps_in = analysis.hypotheses
    if len(hyps_in) > 3:
        notes.append(f"Kept the first 3 of {len(hyps_in)} hypotheses.")
    hypotheses: list[dict[str, Any]] = []
    for h in hyps_in[:3]:
        supporting = []
        for c in h.supporting:
            citations_total += 1
            ev = by_id.get(c.evidence_id)
            if ev is None:
                invalid.append(c.evidence_id)
                continue
            match = None
            if c.quote.strip():
                quotes_checked += 1
                q = _norm(c.quote).strip("\"'")
                match = q in _norm(ev.raw) or q in _norm(ev.message)
                quotes_matched += int(match)
            supporting.append({"evidence_id": c.evidence_id, "why": c.why, "quote_match": match})
            citations.append((c.evidence_id, h.id, "supports"))
        contradicting = []
        for c in h.contradicting:
            citations_total += 1
            if c.evidence_id not in by_id:
                invalid.append(c.evidence_id)
                continue
            contradicting.append({"evidence_id": c.evidence_id, "why": c.why})
            citations.append((c.evidence_id, h.id, "contradicts"))

        ids = [s["evidence_id"] for s in supporting]
        changes = [by_id[i] for i in ids if by_id[i].source_kind == "change_event"]
        cited_failures = {i for i in ids if i in failures}
        caps: list[tuple[str, str, str]] = []
        rejected = not supporting
        if rejected:
            _cap(caps, "low", "R2", "No valid supporting evidence.")
        if any(ch.ts is None or first_ts is None or ch.ts > first_ts for ch in changes):
            _cap(caps, "low", "R3", "A cited change did not happen before the first failure.")
        cited_linked = [ch.evidence_id for ch in changes if ch.evidence_id in linked]
        if changes and not cited_linked:
            _cap(caps, "low", "R4", "The cited change does not touch anything named in the failure.")
        if not (cited_failures & signed) and not cited_linked:
            _cap(caps, "low", "R5", "No cited failure with a specific error, and no linked change.")
        if not any(linked[cid] & cited_failures for cid in cited_linked):
            _cap(caps, "medium", "R6", "High needs a linked change plus the failure it is linked to.")
        elif contradicting:
            _cap(caps, "medium", "R6", "Contradicting evidence is listed.")

        final = h.confidence
        adjustments = []
        for level, rule, reason in caps:
            if RANK[level] < RANK[final]:
                adjustments.append({"from": final, "to": level, "rule": rule, "reason": reason})
                final = level
        hypotheses.append({
            "id": h.id, "statement": h.statement, "category": h.category, "reasoning": h.reasoning,
            "model_confidence": h.confidence, "confidence": final, "adjustments": adjustments,
            "supporting": supporting, "contradicting": contradicting, "rejected": rejected,
        })

    order = {id(h): i for i, h in enumerate(hypotheses)}
    hypotheses.sort(key=lambda h: (h["rejected"], -RANK[h["confidence"]], order[id(h)]))
    top = next((h for h in hypotheses if not h["rejected"]), None)
    verdict = analysis.verdict
    if verdict == "probable_cause" and (top is None or RANK[top["confidence"]] < RANK["medium"]):
        verdict = "insufficient_evidence"
        notes.append("Verdict lowered to insufficient evidence: no hypothesis is supported at medium confidence or above.")

    actions = []
    for a in analysis.recovery_actions[:5]:
        valid_ids = [i for i in a.evidence_ids if i in by_id]
        invalid.extend(i for i in a.evidence_ids if i not in by_id)
        citations.extend((i, a.id, "action") for i in valid_ids)
        flags = [] if valid_ids else ["general"]
        risk = a.risk
        hits = risky_terms(f"{a.action} {a.rationale}")
        if hits:
            flags.append("broad_or_destructive")
            risk = "high"
        actions.append({
            "id": a.id, "action": a.action, "rationale": a.rationale, "evidence_ids": valid_ids,
            "model_risk": a.risk, "risk": risk, "flags": flags, "risky_terms": hits,
            "verify": a.verify, "rollback": a.rollback,
            "requires_human_approval": True, "executed": False,
        })
    if len(analysis.recovery_actions) > 5:
        notes.append(f"Kept the first 5 of {len(analysis.recovery_actions)} actions.")

    free_text = [analysis.summary] + [f"{h.statement} {h.reasoning}" for h in hyps_in[:3]]
    unknown = sorted({i for text in free_text for i in _ID.findall(text) if i not in by_id})

    return {
        "model_verdict": analysis.verdict,
        "verdict": verdict,
        "summary": analysis.summary,
        "hypotheses": hypotheses,
        "missing_evidence": [m.model_dump() for m in analysis.missing_evidence[:5]],
        "recovery_actions": actions,
        "verification": {
            "policy_version": POLICY_VERSION,
            "citations_total": citations_total,
            "citations_invalid": sorted(set(invalid)),
            "free_text_unknown_ids": unknown,
            "quotes_checked": quotes_checked,
            "quotes_matched": quotes_matched,
            "notes": notes,
        },
        "_citations": citations,
    }
