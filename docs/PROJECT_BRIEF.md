# IncidentLens: Project Brief

**AI-Powered Data Pipeline Forensics.** *See beyond the failure.*

This is a solo entry in the Akamai Cloud AI Inference Hackathon. Submissions are due at **3:00 PM Pacific on Friday, November 20, 2026**.

Related documents: [ARCHITECTURE.md](ARCHITECTURE.md) and [IMPLEMENTATION_PLAN.md](IMPLEMENTATION_PLAN.md).

## Problem

When a nightly data pipeline fails, the on-call data engineer has to piece the incident together from evidence spread across several places. Scheduler and task logs are in one system. IAM audit events, deploys, and configuration changes are in others. The first part of the incident goes to lining up timestamps by hand. A rushed investigation often blames the most recent or most visible change, or "fixes" the wrong thing, such as granting broad permissions to make an error go away.

A general-purpose chatbot adds a specific risk. It writes a confident root cause even when the evidence does not support one.

## User and task

- **User:** a data engineer, or an on-call data platform engineer, responding to a failed batch pipeline run.
- **Task:** upload the failed run's pipeline log and the recent operational change events. Get back a rebuilt timeline, ranked hypotheses that cite evidence, a clear statement of what is still unknown, and a short list of safe recovery actions to review.

## Benefit

- **Faster triage.** Logs and changes appear on one timeline in time order. The first failure, and the changes before it that touch the same principals or resources, are highlighted.
- **AI you can check.** Every claim links to the exact log line or change event it relies on. Code rejects claims that cite nothing or cite evidence that does not exist.
- **Honest uncertainty.** When the evidence is thin, IncidentLens says so and lists what to collect next instead of guessing.
- **Safe recovery.** It suggests minimal, reversible, least-privilege actions, and a person approves or rejects each one. IncidentLens never executes anything.
- **A lasting case file.** The hashed raw evidence, the analyses, and the review decisions all remain for the post-incident review.

## MVP scope

**In scope**

1. Upload one pipeline log and an optional change-event file, both in JSON Lines (plain-text logs are P1). Alternatively, load one of two built-in synthetic scenarios with one click.
2. A deterministic step normalizes the evidence, assigns stable evidence IDs (`LOG-0005`, `CHG-0002`), builds the timeline, finds the first failure, matches error signatures, and links changes that came before the failure.
3. A model analysis with `qwen3-8-27b` returns structured JSON: a summary, a verdict, hypotheses with cited evidence, missing evidence, and recovery actions.
4. A deterministic verifier checks the citations, temporal order, and confidence policy, and enforces safety rules on the actions. The verifier can only lower confidence. It never raises it.
5. Raw evidence and reports are kept in Object Storage with SHA-256 hashes. The case file (incidents, evidence, analyses, citations, reviews) is kept in PostgreSQL.
6. A single-page UI shows a verdict banner, timeline, hypothesis cards, a "What we don't know" panel, a recovery checklist, a verification panel, and investigation history. Recording approve or reject decisions on the checklist is P1.
7. The app is deployed on the existing Linode as a separate systemd service, alongside the unchanged workshop demo.

**Out of scope for the MVP:** live connectors to schedulers or cloud audit logs (upload only), automatic remediation of any kind, authentication and multiple tenants (the Cloud Firewall allowlist compensates; see the known limits in the README), Kubernetes, job queues, agent frameworks, and real or employer data.

## Demonstration scenarios

All data is synthetic. It uses fictional systems, `example.com` addresses, and the made-up `*-synth` resource names. [IMPLEMENTATION_PLAN.md](IMPLEMENTATION_PLAN.md#synthetic-scenario-specifications) specifies every line.

### Scenario A: an IAM change comes before authorization errors

The nightly `orders_daily` pipeline fails at `extract_orders` with repeated `403 AccessDenied` errors for principal `svc-etl-loader` on bucket `raw-orders-synth`. The previous night's run succeeded. The change events contain:

- a deploy of `orders_daily` the evening before (a red herring),
- an IAM change at 01:47 UTC, under ticket CR-1042, that removed `storage.objects.get` for `svc-etl-loader` on `raw-orders-synth`,
- a warehouse connection-pool change (a red herring),
- a dashboard permission change after the failure (it cannot be a cause).

**Expected result:** the verdict is *probable cause*. The top hypothesis is the IAM change. It cites `CHG-0002` and the `AccessDenied` lines, and its confidence is high or medium. The timeline marks the first failure. The red herrings are ranked lower or absent. The recovery actions are to confirm intent with the owner of CR-1042, restore only `storage.objects.get` for `svc-etl-loader` on `raw-orders-synth` through the normal reviewed change process, re-run `extract_orders` for the affected partition, and check freshness. None of these grants broad roles or deletes data. Every action requires approval.

### Scenario B: not enough evidence

The `inventory_sync` pipeline fails with `Task exited with non-zero status 1`, after a long heartbeat delay. The driver log upload also failed, so no detailed logs exist. The change events contain an unrelated `customer-portal` deploy, a schedule change for `inventory_sync` the day before (a tempting but unproven lead), and an IAM change after the failure.

**Expected result:** the verdict is *insufficient evidence*. No hypothesis is above low confidence. The app explains why the evidence does not support a cause, and it lists what to collect: driver or executor logs, resource metrics for `etl-worker-1`, supplier-feed availability at 05:00 UTC, and run history before and after the schedule change. The recovery actions are investigative or containment steps, not fixes.

## Akamai Cloud services and why each one is needed

This criterion is worth 40%. Each service backs a user-facing feature that would break or become unsafe without it.

| Service | What IncidentLens uses it for | Why it is needed |
|---|---|---|
| **Object Storage** (private E3 bucket `sea-lawrence-files`, prefix `incidentlens/`) | Holds the original uploaded evidence files unchanged, together with their SHA-256 hashes. Also holds generated incident reports, shared through presigned links that expire. | Forensics depends on chain of custody. The analysis must be traceable to the exact bytes that were uploaded. Before each analysis the app re-checks the hash, and it refuses to analyze evidence that has changed. Raw logs are unstructured files that fit a bucket better than a database. Presigned links let a report be shared with an incident channel while the bucket stays private. |
| **Managed PostgreSQL** (schema `incidentlens`) | Stores the case file: incidents, normalized evidence rows, analyses, citations, and human review decisions. | The relational structure enforces the core rule, *no fabricated evidence*. Each stored citation has a foreign key to a real evidence row, so the database itself rejects a citation to evidence that does not exist. Review decisions form an audit trail. History survives restarts, so past investigations can be reopened, even when inference is unavailable during a demo. |
| **Linode**, with systemd, in Seattle | Runs the FastAPI app around the clock. The credentials stay on the server, and the browser never sees them. | One reliable virtual machine is the simplest host for a single-process app. It sits in the same region as the database, which keeps latency low. The workshop app keeps running beside it as a fallback. |
| **Cloud Firewall** | Lets only allowlisted source IPs reach SSH and the app. | The MVP has no login. The firewall is the compensating access control, and the database allow list adds a second layer. |
| **Akamai Inference** (`qwen3-8-27b`) | Writes evidence-cited hypotheses, explanations, missing-evidence requests, and recovery suggestions as JSON that follows a schema. | See the next section. |

## Inference: model, task, and validation

This criterion is worth 20%.

- **Model:** `qwen3-8-27b`, the event's chat model. The workshop recommends it for JSON output and tool calling, its 131k-token context holds a whole evidence bundle, and the event key covers it.
- **Task:** turn normalized evidence and deterministic facts into ranked hypotheses that cite evidence, an explicit statement of uncertainty, and safe next steps. Code does the parts code is good at: parsing, sorting, matching, and checking. The model does the parts that need judgment: weighing ambiguous evidence, explaining it, and proposing what to look at next.
- **Validation:**
  1. A verifier runs on every response. It checks that cited IDs exist, that cited changes came before the first failure and are linked to it, and that the confidence policy is met. It also enforces action safety: approval is always required, and destructive or broad suggestions are flagged.
  2. Offline unit tests feed the verifier deliberately bad model outputs, such as fabricated IDs, overconfident claims, and causes that come after the failure.
  3. A live evaluation runs both scenarios several times against the real model and compares results with `expected.json`. It reports the model's own verdict separately from the verified verdict, so the verifier's contribution can be measured. Scenario B is the case the model cannot answer, which the judging guide asks for.

## Creativity: what is different

- **Forensics instead of chat.** Evidence has IDs, hashes, and custody. The model works like an analyst who must cite sources, and code acts as the reviewer.
- **The verifier can only lower confidence.** Model confidence is treated as a claim to check, not as a result. The UI shows each adjustment, for example "capped from high to low: the cited change does not touch any resource named in the failure."
- **Uncertainty is a full result.** "Insufficient evidence" gets its own screen that lists what to collect, rather than counting as a failure.
- **Safety is enforced in code.** The model cannot mark an action as executed or exempt from approval, because those fields are not in its output schema.

## Guardrails

- Use synthetic data only. Never handle secrets in code, logs, or commits. Never fabricate evidence. Never claim certainty without support. Never execute remediation automatically.
- Treat uploaded text as untrusted. The model has no tools, evidence goes to it as data rather than instructions, and the UI renders all text with `textContent`.

## Success criteria (definition of done)

1. Both scenarios run end to end on the deployed Linode and show the expected verdicts.
2. The verification panel shows the citation checks and any confidence changes for each run.
3. History persists across a service restart, and the raw evidence hash is verified.
4. The live evaluation results, including pass rates and known misses, are in the README.
5. The public repository has a README in the judging template and contains no secrets.
6. The video is no longer than five minutes, and the HackerSquad submission is final before 3:00 PM PT.

## Assumptions

- The event key permits `qwen3-8-27b` and supports `response_format` with `json_schema`, as the workshop's `01-inference/json_output.py` shows.
- The per-minute token budget is shared across the event account. Small fixtures and cached analyses keep the app within it.
- The database admin user can create the schema `incidentlens` in the existing database, and the existing bucket key can read and write the prefix `incidentlens/`.
- IncidentLens reuses the workshop's environment variable names. **The user creates `incidentlens/.env` themselves.** The assistant never opens it.
- Judges evaluate mainly through the video, the repository, and a live demo at the venue. The firewall allows only the venue's IP.
- HackerSquad is the submission platform. Event staff provide the link and any required fields.
- The workshop repository has no license file. IncidentLens credits it and reimplements its patterns instead of copying large portions verbatim.

## Risks

See the [risk register](IMPLEMENTATION_PLAN.md#risk-register). The main risks are the same-day deadline, the inference rate limit and model variance, a source-IP change that blocks the firewall or database, and the lack of a login.

## Decisions requiring approval

See [Open decisions](IMPLEMENTATION_PLAN.md#open-decisions-requiring-approval).

## Credits

IncidentLens builds on patterns from the Akamai workshop starter, [akamai-developers/akamai-workshop-seattle-hackathon](https://github.com/akamai-developers/akamai-workshop-seattle-hackathon). Those patterns cover the OpenAI-compatible inference client, `psycopg` connection handling, the `boto3` configuration for Akamai Object Storage, `/health` status reporting, and the systemd service layout.
