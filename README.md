# IncidentLens

**AI-Powered Data Pipeline Forensics.** *See beyond the failure.*

IncidentLens helps a data engineer investigate a failed pipeline run. You upload the pipeline log and recent change events. IncidentLens rebuilds the timeline, proposes root causes that cite specific evidence, says plainly when the evidence is not enough, and recommends recovery steps for a person to approve. It never executes anything.

Built for the Akamai Cloud AI Inference Hackathon. All data is synthetic.

## How it works

1. **Ingest (code):** each line gets a stable evidence ID (`LOG-0005`, `CHG-0002`). Raw files are hashed (SHA-256) and stored in Akamai Object Storage. Normalized evidence is stored in Akamai Managed PostgreSQL.
2. **Signals (code):** finds the timeline, the first failure, and the error signatures. It also finds the changes made before the failure that share a principal, resource, or permission with it.
3. **Model (`qwen3-8-27b` on Akamai Inference):** writes hypotheses, missing evidence, and recovery actions as JSON that follows a fixed schema, citing evidence IDs.
4. **Verifier (code):**
   - Drops citations to evidence that does not exist.
   - Caps confidence when a cited change came after the failure or does not touch anything the failure names.
   - Lowers the verdict to "insufficient evidence" when nothing reaches medium confidence.
   - Forces every action to require human approval.

   It can lower the model's confidence, but never raise it. Before each analysis, the app downloads the raw evidence again and re-checks its hash.

## Run locally

```bash
uv sync
cp .env.example .env      # then fill in your own values; never commit .env
uv run python -m incidentlens      # http://127.0.0.1:8000
uv run pytest                      # offline tests, no network
uv run python scripts/eval_scenarios.py --runs 3   # live model evaluation; spends inference tokens
```

## Status

- Working locally, tested against Akamai Inference, PostgreSQL, and Object Storage: Scenario A and Scenario B both run end to end, and saved analyses reopen after a restart without calling the model again.
- Not yet deployed. Deployment steps are in [docs/IMPLEMENTATION_PLAN.md](docs/IMPLEMENTATION_PLAN.md).

See [docs/PROJECT_BRIEF.md](docs/PROJECT_BRIEF.md) and [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) for the design.

## Credits

Builds on patterns from the Akamai workshop starter, [akamai-developers/akamai-workshop-seattle-hackathon](https://github.com/akamai-developers/akamai-workshop-seattle-hackathon).
