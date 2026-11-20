# IncidentLens: Implementation Plan

**Status:** planning only. No application code exists yet, and nothing has been deployed.
**Hard deadline:** final submission by **3:00 PM Pacific on November 20, 2026**.

Related documents: [PROJECT_BRIEF.md](PROJECT_BRIEF.md) and [ARCHITECTURE.md](ARCHITECTURE.md).

## Principles

1. **Build a vertical slice first.** Get one scenario working end to end locally against the real Akamai services before polishing anything.
2. **Gates, not hopes.** Each phase ends at a gate where the app runs and the work is committed locally. If a gate slips, apply its cut line instead of pushing on.
3. **Take the riskiest unknown first.** Model behavior is the biggest unknown, so the live inference check comes before persistence and UI work.
4. **Approval before anything shared changes.** Pushes, Linode changes, firewall rules, and changes to database access or buckets each need explicit approval in the conversation.
5. **Feature freeze.** Choose a clock time ([decision D9](#open-decisions-requiring-approval)). After it, only bug fixes, the README, the video, and the submission are allowed.

## Priorities

**P0: must ship. The demo fails without these.**

1. Synthetic scenarios A and B, each with `expected.json`
2. Ingest of JSON Lines files: normalization, evidence IDs, and SHA-256 hashes
3. Deterministic signals: timeline, first failure, error signatures, linked changes, and after-failure changes
4. A model call with `response_format` set to a JSON schema, plus defensive parsing
5. Verifier policy v1 (rules R0 to R9, A1 to A3, and the Q1 metric)
6. The PostgreSQL case file: `incidents`, `artifacts`, `evidence`, `analyses`, and `citations`
7. Object Storage for raw evidence, with a hash re-check before each analysis
8. API endpoints: `/`, `/health`, `/api/scenarios`, the incident create, list, and get routes, and `analyze`
9. UI: badges, start panel, verdict, timeline, hypotheses, the "What we don't know" panel, a read-only recovery checklist, the verification panel, and history
10. Offline tests for ingest, signals, and the verifier, plus an API smoke test that uses fakes
11. The live evaluation script, with its results in the README
12. Deployment on the Linode beside the workshop app (needs approval)
13. The README, the video, and the final submission

**P1: should ship. High demo value, low risk. Add these in this order once P0 is deployed.**

1. Approve, Reject, and Needs-info buttons for actions, stored in an `action_reviews` table (makes the human review concrete)
2. A deterministic Markdown report in Object Storage, shared through a presigned link
3. An evidence drawer that shows the object key and file SHA-256
4. Clickable valid evidence IDs in the summary and reasoning text
5. Upload of plain-text logs (`<ts> <LEVEL> [component] message`)

**P2: could ship, only if P0 and P1 finish before feature freeze.**

1. A "similar past investigations" list using `qwen3-embedding-8b` and pgvector
2. An offline in-memory mode as a demo fallback, reusing the test fakes
3. Optional shared-password basic authentication
4. Scenario C, a prompt-injection showcase
5. A dedicated non-root service user and systemd hardening

**Not doing:** authentication or SSO beyond P2, live connectors, automatic remediation, Kubernetes, queues, agent frameworks, incidents with several files, or streaming responses.

## Phases and gates

### Phase 0: Repository bootstrap (local only)

- Create `pyproject.toml` with name `incidentlens` and `requires-python = ">=3.11"`, using the workshop's dependency set (see [ARCHITECTURE.md §2](ARCHITECTURE.md#2-repository-layout-planned)). Add `pytest` and `httpx` as development dependencies, plus `[tool.pytest.ini_options] pythonpath = ["."]`.
- Add `.python-version` (`3.11`), then run `uv lock` and `uv sync`.
- Add `.env.example` with variable names and placeholders only. Public values, such as the base URL and model names, may be filled in.
- Add the `incidentlens/` package skeleton: `__main__.py`, which binds `0.0.0.0` only when `PORT` is set, and `app.py` with `/health`.
- **User action:** create `incidentlens/.env`, for example by copying your workshop `.env` yourself. The assistant never opens, prints, or copies it.
- **Gate 0:** `uv run python -m incidentlens` serves `/health` on the Mac with `database: connected` and `object_storage: connected`. Then make the first local commit after reviewing `git status` (**approval D6**). Do not push.

### Phase 1: Scenarios and the deterministic core (offline)

- Write `scenarios/a_iam_change/*` and `scenarios/b_insufficient_evidence/*` exactly as [specified below](#synthetic-scenario-specifications).
- Write `models.py`, `ingest.py`, and `signals.py`, with tests in `tests/test_ingest.py` and `tests/test_signals.py`.
- **Gate 1:** `uv run pytest` passes. The signals for A and B match the deterministic fields in each `expected.json`: `first_failure`, `linked_changes`, and `after_failure_changes`.

### Phase 2: Inference and the verifier

- Write `inference.py`: the prompt, the inline JSON schema, `finish_reason` handling, stripping of a `<think>` block, and one repair retry.
- Write `verify.py` with policy v1, and `tests/test_verify.py` with canned adversarial outputs (see [Validation](#validation-approach)).
- Write a first version of `scripts/eval_scenarios.py` and **run it once per scenario on purpose**. This spends shared tokens.
- **Gate 2:** the live Scenario A run returns parseable JSON, and its top hypothesis cites `CHG-0002`. The live Scenario B run ends with the final verdict `insufficient_evidence`.
- **Cut line:** if the model struggles, reduce the schema (at most 2 hypotheses and 3 actions), strengthen the hints in the signals, and change the prompt only a few times. Keep the verifier unchanged.

### Phase 3: Persistence (PostgreSQL and Object Storage)

- Write `store.py`: the schema DDL from [ARCHITECTURE.md §8](ARCHITECTURE.md#8-postgresql-schema), plus the queries. Write `blobs.py`: put, get, the hash check, and presigning.
- Ingest writes the objects first, then the database rows. Analysis checks the hash, then writes the analysis and the verified citations in one transaction.
- **Gate 3:** a scenario run from the Mac creates rows in `incidentlens.*` and objects under `incidentlens/`. After the app restarts, History still lists the run.

### Phase 4: API and UI (local demo-ready)

- Build the routes from [ARCHITECTURE.md §10](ARCHITECTURE.md#10-http-api) and the P0 UI components from [§11](ARCHITECTURE.md#11-ui-components). Use external JS and CSS and add the security headers.
- Write `tests/test_api.py` with fakes.
- **Gate 4: minimum viable demo.** Both scenarios run end to end in the browser on the Mac against Akamai Inference, PostgreSQL, and Object Storage. Commit. Everything after this point is deployment and polish.
- **Cut line:** if the UI is behind, ship the verdict, the timeline, the hypotheses, the "What we don't know" panel, and the verification panel. Make the recovery checklist plain text.

### Phase 5: Deploy to the Linode (needs approval)

- Follow the [deployment runbook](#deployment-runbook-requires-approval).
- **Gate 5:** both scenarios pass at the deployed URL, History persists after `systemctl restart incidentlens`, and the workshop app still answers on port 80.
- **Cut line:** if deployment is blocked, give the demo from the Mac, which still uses Akamai Inference, PostgreSQL, and Object Storage. Record the hosting gap in the README's known limits.

### Phase 6: Evaluation and README

- Run `uv run python scripts/eval_scenarios.py --runs 3` and save the JSON output in `artifacts/`, which is gitignored. Copy the summary table into the README.
- Write the README using the judging template: User and task, Benefit, Services, Inference, Technical choice, Our idea, Demo, and Known limits. Add the architecture diagram, how to run, how to deploy, the evaluation results including misses, and credits to the Akamai workshop starter. Add `LICENSE` (**decision D7**).
- **Gate 6:** the README is complete, and a secret scan of the tracked files is clean.

### Phase 7: P1 polish (only before feature freeze)

- Add the P1 items in priority order. After each item, run the tests, commit, redeploy (`git archive` and restart), and run both scenarios again.

### Phase 8: Video and submission

- Record the video from the [outline](#demo-video-outline-five-minutes-or-less), push the repository (**approval D6**), and complete the [submission checklist](#hackathon-submission-checklist).

## Synthetic scenario specifications

All data is synthetic. It uses `example.com` actors, fictional `*-synth` resources, and timestamps in UTC. Each file is JSON Lines, and line *n* becomes evidence ID `LOG-000n` or `CHG-000n`.

### Scenario A: `scenarios/a_iam_change/`

`pipeline.jsonl` (fields `ts`, `level`, `component`, `message`):

| ID | ts | level | component | message |
|---|---|---|---|---|
| LOG-0001 | 2026-11-20T03:00:00Z | INFO | scheduler | DAG run orders_daily started (run_id=scheduled__2026-11-20T03:00). Previous run 2026-11-19T03:00Z succeeded. |
| LOG-0002 | 2026-11-20T03:00:02Z | INFO | extract_orders | Task started on etl-worker-2 as principal svc-etl-loader |
| LOG-0003 | 2026-11-20T03:00:03Z | INFO | extract_orders | Listing objects in raw-orders-synth/orders/dt=2026-11-19/ |
| LOG-0004 | 2026-11-20T03:00:04Z | INFO | extract_orders | Listed 52 objects (31.4 MB) in raw-orders-synth/orders/dt=2026-11-19/ |
| LOG-0005 | 2026-11-20T03:00:05Z | ERROR | extract_orders | 403 AccessDenied: principal svc-etl-loader lacks storage.objects.get on raw-orders-synth/orders/dt=2026-11-19/part-0001.json |
| LOG-0006 | 2026-11-20T03:00:05Z | WARN | extract_orders | Attempt 1/3 failed; retrying in 30s |
| LOG-0007 | 2026-11-20T03:00:35Z | ERROR | extract_orders | 403 AccessDenied: principal svc-etl-loader lacks storage.objects.get on raw-orders-synth/orders/dt=2026-11-19/part-0001.json |
| LOG-0008 | 2026-11-20T03:00:35Z | WARN | extract_orders | Attempt 2/3 failed; retrying in 60s |
| LOG-0009 | 2026-11-20T03:01:35Z | ERROR | extract_orders | 403 AccessDenied: principal svc-etl-loader lacks storage.objects.get on raw-orders-synth/orders/dt=2026-11-19/part-0001.json |
| LOG-0010 | 2026-11-20T03:01:35Z | ERROR | extract_orders | Task failed after 3 attempts (last error: AccessDenied) |
| LOG-0011 | 2026-11-20T03:01:36Z | WARN | load_to_warehouse | Skipped: upstream_failed (extract_orders) |
| LOG-0012 | 2026-11-20T03:01:36Z | WARN | build_revenue_mart | Skipped: upstream_failed (load_to_warehouse) |
| LOG-0013 | 2026-11-20T03:01:37Z | ERROR | scheduler | DAG run orders_daily failed |
| LOG-0014 | 2026-11-20T03:15:00Z | WARN | freshness_monitor | analytics.revenue_daily is stale: last loaded 2026-11-19T03:12Z; SLA 06:00Z |

`change_events.jsonl` (fields `ts`, `event_type`, `actor`, `target`, optional `principal` and `permission`, `ticket`, `summary`):

| ID | ts | event_type | actor | target | principal / permission | ticket | summary | Role in the test |
|---|---|---|---|---|---|---|---|---|
| CHG-0001 | 2026-11-19T21:05:00Z | deploy | ci-bot@example.com | dag/orders_daily | | CR-1038 | Deployed orders_daily v1.8.2: reformatted SQL in build_revenue_mart; extract_orders unchanged. | Red herring. It comes before the failure but is not linked |
| CHG-0002 | 2026-11-20T01:47:12Z | iam.binding_removed | alice@example.com | bucket/raw-orders-synth | svc-etl-loader / storage.objects.get | CR-1042 | Least-privilege cleanup: removed storage.objects.get for principal svc-etl-loader on raw-orders-synth. | **Cause.** It is linked through `svc-etl-loader`, `raw-orders-synth`, and `storage.objects.get` |
| CHG-0003 | 2026-11-20T02:10:00Z | config_change | bob@example.com | warehouse/wh-analytics-synth | | CR-1047 | Reduced connection pool max_size from 20 to 16. | Red herring. The warehouse step never ran |
| CHG-0004 | 2026-11-20T04:30:00Z | iam.binding_added | alice@example.com | dashboard/revenue-overview | analysts-synth / dashboards.view | CR-1049 | Granted dashboard viewer to group analysts-synth. | It comes after the failure, so it can never be a cause |

The listing succeeds (LOG-0004) but the get calls fail (LOG-0005), which matches the removal of only `storage.objects.get`. A careful analysis can point this out.

`expected.json`:

```json
{
  "scenario": "a_iam_change",
  "deterministic": {"first_failure": "LOG-0005", "linked_changes": ["CHG-0002"], "after_failure_changes": ["CHG-0004"]},
  "verdict": "probable_cause",
  "top_hypothesis": {"category": "permissions", "must_cite": ["CHG-0002"],
                     "must_cite_one_of": ["LOG-0005", "LOG-0007", "LOG-0009", "LOG-0010"], "min_confidence": "medium"},
  "not_top_cause": ["CHG-0001", "CHG-0003", "CHG-0004"],
  "max_flagged_actions": 0
}
```

### Scenario B: `scenarios/b_insufficient_evidence/`

`pipeline.jsonl`:

| ID | ts | level | component | message |
|---|---|---|---|---|
| LOG-0001 | 2026-11-20T05:00:00Z | INFO | scheduler | DAG run inventory_sync started (run_id=scheduled__2026-11-20T05:00). Previous run 2026-11-19T04:00Z succeeded. |
| LOG-0002 | 2026-11-20T05:00:02Z | INFO | sync_inventory | Task started on etl-worker-1 |
| LOG-0003 | 2026-11-20T05:00:03Z | INFO | sync_inventory | Reading inventory snapshot from supplier-feed-synth |
| LOG-0004 | 2026-11-20T05:07:41Z | WARN | sync_inventory | Heartbeat delayed by 95s |
| LOG-0005 | 2026-11-20T05:09:12Z | ERROR | sync_inventory | Task exited with non-zero status 1 |
| LOG-0006 | 2026-11-20T05:09:12Z | WARN | sync_inventory | Driver log upload failed; detailed logs are unavailable for this attempt |
| LOG-0007 | 2026-11-20T05:09:13Z | ERROR | scheduler | DAG run inventory_sync failed |
| LOG-0008 | 2026-11-20T05:20:00Z | WARN | freshness_monitor | analytics.inventory_levels is stale: last loaded 2026-11-19T04:31Z; SLA 08:00Z |

`change_events.jsonl`:

| ID | ts | event_type | actor | target | principal / permission | ticket | summary | Role in the test |
|---|---|---|---|---|---|---|---|---|
| CHG-0001 | 2026-11-19T16:20:00Z | deploy | ci-bot@example.com | service/customer-portal | | CR-1050 | Deployed customer-portal v3.2.0 (frontend copy changes). | Unrelated service |
| CHG-0002 | 2026-11-19T18:45:00Z | schedule_change | bob@example.com | dag/inventory_sync | | CR-1051 | Moved inventory_sync schedule from 04:00Z to 05:00Z. | **A tempting lead without enough proof.** It names the DAG, but no failure has an error signature to link it to |
| CHG-0003 | 2026-11-20T07:30:00Z | iam.binding_added | alice@example.com | bucket/reports-synth | svc-reporting / storage.objects.get | CR-1053 | Granted read on reports-synth to svc-reporting. | It comes after the failure |

None of the messages may contain words from the signature catalog, such as "timeout", "denied", or "137". That is what keeps Scenario B honest. A unit test checks it.

`expected.json`:

```json
{
  "scenario": "b_insufficient_evidence",
  "deterministic": {"first_failure": "LOG-0005", "linked_changes": [], "after_failure_changes": ["CHG-0003"]},
  "verdict": "insufficient_evidence",
  "max_confidence": "low",
  "missing_evidence_mentions_any": ["driver", "executor", "stack trace", "memory", "metric", "supplier", "feed", "schedule"],
  "max_flagged_actions": 0
}
```

Given this data, verifier rules R4, R5, and R8 guarantee B's *final* verdict. The evaluation therefore reports the **model's own verdict** separately, so it measures the model's calibration and not only the verifier.

## Validation approach

### Offline tests (`uv run pytest`, no network)

| File | Covers |
|---|---|
| `test_ingest.py` | JSON Lines parsing. Malformed lines become warnings with `ts=null`. Stable, zero-padded IDs. Byte, line, and line-length limits. Non-UTF-8 input is rejected. SHA-256 is computed on the original bytes |
| `test_signals.py` | Timeline order and ties. First failure for A and B. Signature catalog positives, plus negatives such as "failed" and "exited with non-zero status 1". Entity tokens exclude dates. Linked changes: A gives `[CHG-0002]`, B gives `[]`. After-failure changes. B contains no signature words |
| `test_verify.py` | Fabricated `LOG-0999` is dropped and listed. A hypothesis whose citations are all fabricated is rejected. A cause after the effect is capped low. An unlinked change is capped low. A failure with only a signature reaches at most medium. A linked change with its participating failure allows high. Contradicting evidence caps at medium. Confidence is never raised. A model's "insufficient" is never upgraded. The verdict drops when the top hypothesis is below medium. Ranking. Quote metrics. Unknown IDs in free text. Approval flags are forced on every action. Risky wording is flagged high. Item limits |
| `test_inference.py` | A `<think>` block is stripped. `finish_reason == "length"` raises an error. The repair retry runs against a fake client |
| `test_api.py` | Using fakes: `/health`; creating a scenario incident; a multipart upload; `analyze` with a canned Scenario A output; 409 while an analysis is running; a review records the decision while the action shows `executed: false`; the CSP and `nosniff` headers are present |

### Live model evaluation (`scripts/eval_scenarios.py`)

The script runs each scenario N times (3 by default) and checks the result against `expected.json`.

| Metric | Scenario A target | Scenario B target |
|---|---|---|
| Final verdict matches | At least 2 of 3 | 3 of 3 |
| Model's own verdict matches (before the verifier) | Reported | Reported. This is the calibration measure |
| Top hypothesis cites `CHG-0002` and a 403 line, with confidence at least medium | At least 2 of 3 | Not applicable |
| Top hypothesis is not a red herring | At least 2 of 3 | Not applicable |
| Missing evidence mentions an expected need | Reported | At least 2 of 3 |
| Invalid citations per run | Reported (target 0) | Reported (target 0) |
| Quote match rate | Reported | Reported |
| Actions flagged broad or destructive | 0 | 0 |
| Latency and tokens | Reported | Reported |

The README reports the results honestly, including any misses.

### Manual checks on the deployed app

- [ ] Scenarios A and B in the browser at the deployed URL
- [ ] `systemctl restart incidentlens`, then History still lists earlier investigations and can reopen them without a model call
- [ ] `/health` shows `database: connected` and `object_storage: connected`
- [ ] Objects appear under `incidentlens/` in Cloud Manager. With P1 done, the report link opens, and its URL shows `X-Amz-Expires=3600`
- [ ] An uploaded file containing `<script>alert(1)</script>` displays as text
- [ ] The workshop app still answers at `http://172.234.238.116/`

## Deployment runbook (requires approval)

Do not run any step below until **D1 to D3** are approved. Step 9 also needs **D2**.

```bash
# Local pre-flight
uv run pytest
git status                 # clean; .env does not appear
git ls-files | grep -E '(^|/)\.env($|\.)' | grep -v '\.env\.example$'   # expect no output
IP=172.234.238.116

# 1. Code: only committed files go to the server, so ignored files cannot leak
ssh root@$IP 'mkdir -p /root/incidentlens'
git archive --format=tar HEAD | ssh root@$IP 'tar -x -C /root/incidentlens'

# 2. Settings: copied directly, readable by root only
scp .env root@$IP:/root/incidentlens/.env
ssh root@$IP 'chmod 600 /root/incidentlens/.env'

# 3. Service: a new unit beside the untouched workshop app.service
scp deploy/incidentlens.service root@$IP:/etc/systemd/system/incidentlens.service
ssh root@$IP 'systemctl daemon-reload && systemctl enable --now incidentlens'

# 4. Logs: wait for "Uvicorn running on http://0.0.0.0:8080"
ssh root@$IP 'journalctl -u incidentlens -n 30 --no-pager'

# 5. Both services are active
ssh root@$IP 'systemctl is-active app incidentlens'

# 6. Smoke test through an SSH tunnel, with no firewall change (keep it running in a second terminal)
ssh -N -L 8081:127.0.0.1:8080 root@$IP
# 7. In another terminal, check /health, then open http://127.0.0.1:8081 and run Scenarios A and B
curl -s http://127.0.0.1:8081/health
```

8. Check Gate 5's persistence: run `ssh root@$IP 'systemctl restart incidentlens'`, then reload the page and open History.
9. **(D2) Firewall:** in Cloud Manager, open **Firewalls**, then the Linode's firewall, then **Add an Inbound Rule**. Set label `incidentlens-web`, TCP, port `8080`, and the same source `/32` as the existing rule, with action Accept. Click **Save Changes**. Then check `curl -s http://$IP:8080/health` and `http://172.234.238.116:8080`.

**Update:** repeat step 1, then run `ssh root@$IP 'systemctl restart incidentlens'`. Files deleted from the repository stay on the server. That is harmless for the MVP.

**Rollback:** run `ssh root@$IP 'systemctl disable --now incidentlens'` and remove the `incidentlens-web` firewall rule. The workshop app on port 80 is never touched. Removing `/root/incidentlens`, dropping the schema `incidentlens`, and deleting the `incidentlens/` objects are destructive, so they happen only with explicit approval, after judging.

## Demo video outline (five minutes or less)

Aim for about 4:45 to leave a margin.

| Segment | Target | Content |
|---|---|---|
| Hook and user | 0:25 | A pipeline failed overnight. The logs are in one place and the IAM audit is in another, and a rushed fix risks granting broad access |
| Scenario A | 1:35 | Load the sample. Show the timeline with the first failure, the linked change, and the grayed after-failure change. Run Analyze. Show the verdict and H1 with its cited evidence, and click a chip to see the raw line. Walk through the recovery checklist: least privilege, approval required, approve one action (P1). Show the verification panel |
| Scenario B | 1:00 | The verdict is "Insufficient evidence". Show the "What we don't know" panel, and compare the model's own verdict with the final verdict |
| Akamai services | 1:10 | The architecture diagram. Object Storage objects with SHA-256 and the presigned report. The PostgreSQL case file and the foreign-key-enforced citations. The Linode systemd service and firewall. Why this model |
| Validation and limits | 0:35 | The evaluation table and the known limits |
| Close | 0:15 | "IncidentLens. See beyond the failure." |

Record as soon as Gate 5 passes, using saved analyses from History if inference is rate-limited. Re-record later only if time allows.

## Hackathon submission checklist

- [ ] Get the submission link and required fields from event staff (`docs/judging.md`: "Get the submission link from event staff")
- [ ] **Public GitHub repository** `lawrence-anthony/incidentlens`: pushed after approval (D6), default branch `main`, and visible from a private browser window
- [ ] No secrets in the tracked files or the history: `.env` is untracked, and a `git grep` for key prefixes and private-key headers finds only placeholders
- [ ] **README**: the judging template sections, architecture, the services and why each is needed, the inference model and evaluation results, how to run and deploy, known limits, credits to the Akamai workshop starter, and the license
- [ ] **Tested demo**: both scenarios pass on the deployed URL, and persistence after a restart is confirmed
- [ ] **Video no longer than 5:00**: uploaded where the rules require, and the link tested in a private window
- [ ] **HackerSquad project details**: the name, subtitle, tagline, and description; the user and task; the Akamai services and why each is needed; the model; the repository, video, and demo URL (state that the firewall restricts the demo URL); team: solo; screenshots
- [ ] **Explicit final submission** made before 3:00 PM PT, not left as a draft. Save the confirmation
- [ ] Keep the Linode, database, and bucket running through judging, then clean up following the workshop's `docs/cleanup.md` (with approval)

## Risk register

| # | Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|---|
| 1 | The same-day deadline invites scope creep | High | High | P0 list, gates with cut lines, feature freeze (D9), record the video early |
| 2 | Inference returns 429 under the shared per-minute budget, or is slow or unavailable | Medium | High | Small inputs, single-flight lock, saved analyses that reopen from History, retry, record with stored results |
| 3 | The model's JSON breaks: `<think>` text, truncation, or ignored `maxItems` | Medium | Medium | `json_schema`, `finish_reason` check, repair retry, limits enforced in code |
| 4 | Model reasoning is wrong in A or overconfident in B | Medium | High | Deterministic hints, verifier caps, a few prompt iterations, honest evaluation reporting |
| 5 | A venue IP change breaks firewall or Mac database access | Medium | High | Record early. Know the fix, which needs approval. The Linode's database access is unaffected |
| 6 | Deployment breaks the workshop fallback | Low | High | Side-by-side service, no edits to `app.service`, rollback steps |
| 7 | Secrets leak through git | Low | Critical | `.gitignore` already checked, staged-file review, a scan before push, deployment with `git archive` |
| 8 | Prompt injection in uploaded logs | Medium | Low | No tools, verifier, `textContent`, people approve actions. Stated as a known limit |
| 9 | No authentication | Certain | Medium | Firewall allowlist, synthetic data only. Stated as a known limit. P2 basic authentication |
| 10 | Verifier rules overfit the two scenarios | Medium | Medium | The rules are generic (signatures, linkage, time order). Tests include variants. The limits are stated |
| 11 | The 1 GB Nanode runs low on memory with two apps | Low | Medium | Check `free -m` after deploying. Stop the workshop app only with approval |
| 12 | Coupling with the workshop through the shared bucket and database | Low | Low | The `incidentlens/` prefix and the `incidentlens` schema |

## Open decisions requiring approval

| # | Decision | Recommendation |
|---|---|---|
| D1 | Deployment topology | **Beside the existing app on the same Linode, port 8080.** The alternatives are taking over port 80, which changes the workshop service, or a new Linode, which adds cost and allow-list changes |
| D2 | Firewall change | Add rule `incidentlens-web`: TCP 8080 from the same `/32`, and only after the SSH-tunnel smoke test passes |
| D3 | Linode changes | Create `/root/incidentlens`, copy `.env` with mode 600, and install and enable `incidentlens.service` |
| D4 | Data stores | Schema `incidentlens` in the existing database and the prefix `incidentlens/` in `sea-lawrence-files`. The alternative is a dedicated bucket and limited key |
| D5 | The IncidentLens `.env` | **You create it.** The assistant never reads or copies it |
| D6 | Git | Approve the first local commit's file list, then the push to the public repository |
| D7 | License | MIT, with credit to the Akamai workshop starter in the README |
| D8 | Scope | Ship P0, add P1 in the listed order, and skip P2 unless time remains |
| D9 | Feature-freeze clock time | You choose. Leave enough margin for the video, HackerSquad details, and the final submission before 3:00 PM PT |
