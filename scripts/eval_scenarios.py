"""Live evaluation: run each scenario against the real model and check it against expected.json.

    uv run python scripts/eval_scenarios.py --runs 3

This spends shared inference tokens. It does not touch PostgreSQL or Object Storage.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from incidentlens.config import production_dependencies  # noqa: E402
from incidentlens.inference import InferenceError, build_payload  # noqa: E402
from incidentlens.ingest import parse_change_events, parse_pipeline_log  # noqa: E402
from incidentlens.signals import compute_signals  # noqa: E402
from incidentlens.verify import RANK, verify  # noqa: E402


def check(expected: dict, out: dict) -> dict[str, bool]:
    top = next((h for h in out["hypotheses"] if not h["rejected"]), None)
    cited = {s["evidence_id"] for s in top["supporting"]} if top else set()
    flagged = sum("broad_or_destructive" in a["flags"] for a in out["recovery_actions"])
    results = {
        "final_verdict": out["verdict"] == expected["verdict"],
        "model_verdict": out["model_verdict"] == expected["verdict"],
        "no_invalid_citations": not out["verification"]["citations_invalid"],
        "no_flagged_actions": flagged <= expected["max_flagged_actions"],
    }
    if "top_hypothesis" in expected:
        th = expected["top_hypothesis"]
        results["top_cites_cause"] = bool(top) and set(th["must_cite"]) <= cited and bool(cited & set(th["must_cite_one_of"]))
        results["top_confidence"] = bool(top) and RANK[top["confidence"]] >= RANK[th["min_confidence"]]
        results["top_not_red_herring"] = not (cited & set(expected["not_top_cause"]) - set(th["must_cite"]))
    if "max_confidence" in expected:
        results["confidence_capped"] = all(RANK[h["confidence"]] <= RANK[expected["max_confidence"]]
                                           for h in out["hypotheses"])
        text = " ".join(f"{m['need']} {m['why']}" for m in out["missing_evidence"]).lower()
        results["names_missing_evidence"] = any(k in text for k in expected["missing_evidence_mentions_any"])
    return results


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--runs", type=int, default=3)
    parser.add_argument("--scenario", action="append")
    args = parser.parse_args()
    load_dotenv(ROOT / ".env")
    llm = production_dependencies()["llm"]
    if llm is None:
        sys.exit("Inference is not configured. Set AKAMAI_BASE_URL, AKAMAI_API_KEY, and CHAT_MODEL in .env.")
    slugs = args.scenario or sorted(p.name for p in (ROOT / "scenarios").iterdir() if p.is_dir())
    report = []
    for slug in slugs:
        folder = ROOT / "scenarios" / slug
        expected = json.loads((folder / "expected.json").read_text())
        evidence = (parse_pipeline_log((folder / "pipeline.jsonl").read_bytes()).evidence
                    + parse_change_events((folder / "change_events.jsonl").read_bytes()).evidence)
        signals = compute_signals(evidence)
        for run in range(1, args.runs + 1):
            try:
                result = llm.analyze(build_payload(expected["title"], evidence, signals))
            except InferenceError as error:
                print(f"{slug} run {run}: inference error: {error}")
                report.append({"scenario": slug, "run": run, "error": str(error)})
                continue
            out = verify(result.analysis, evidence, signals)
            out.pop("_citations")
            checks = check(expected, out)
            top = next((h for h in out["hypotheses"] if not h["rejected"]), None)
            print(f"{slug} run {run}: model={out['model_verdict']} final={out['verdict']} "
                  f"top={top['id'] + ' ' + top['confidence'] if top else '-'} "
                  f"latency={result.latency_ms}ms tokens={result.prompt_tokens}/{result.completion_tokens}")
            for name, ok in checks.items():
                print(f"    {'PASS' if ok else 'FAIL'}  {name}")
            report.append({"scenario": slug, "run": run, "checks": checks, "latency_ms": result.latency_ms,
                           "prompt_tokens": result.prompt_tokens, "completion_tokens": result.completion_tokens,
                           "result": out})
    print("\nSummary")
    for slug in slugs:
        rows = [r for r in report if r["scenario"] == slug and "checks" in r]
        names = rows[0]["checks"].keys() if rows else []
        for name in names:
            print(f"  {slug:28} {name:24} {sum(r['checks'][name] for r in rows)}/{len(rows)}")
    out_dir = ROOT / "artifacts"
    out_dir.mkdir(exist_ok=True)
    path = out_dir / f"eval-{time.strftime('%Y%m%d-%H%M%S')}.json"
    path.write_text(json.dumps({"model": os.environ.get("CHAT_MODEL"), "runs": report}, indent=2, default=str))
    print(f"\nSaved {path.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
