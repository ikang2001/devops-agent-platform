# Step 6 Productization And Governance Assets

Status: **partial local product surfaces implemented; intelligent-loop governance
remains a blueprint; real target-environment acceptance is still deployment
work.**

Step 6 turns the reliable backend into team-facing operations surfaces and
defines how a future learning loop *should* work. It must not be described as a
fully wired intelligent product loop.

## Implemented In Repository Runtime

These paths exist as code, migrations, routes, or packaged UI:

- Productization package under `ops/product` (docs + offline tools).
- Same-origin zero-build Ops Console for incident triage, Evidence audit, RCA
  review, immutable feedback, ticket approval/submission, and runtime health.
- Persistent tenant-scoped RCA feedback API, PostgreSQL migration, redaction,
  idempotency, permissions, audit event, and console workflow.
- Direct opt-in Jira Cloud and ServiceNow adapters with bounded HTTP contracts.
- Slack, Teams v2, and PagerDuty Alertmanager fan-out **example** using
  file-backed secrets, validated with the pinned Alertmanager binary.
- Platform-level remediation plan API with PostgreSQL state, action catalog,
  evidence gate, ETags, idempotency, author/reviewer separation, maintenance
  window, default-off kill switch, fixed HTTP controller adapter, and Ops
  Console workflow.
- MiniShop allowlisted remediation sandbox with durable SQLite approval state,
  expiry, Manifest digests, rollback, and append-only audit records.
- Deterministic MiniShop three-scenario Ground Truth regression gate for the
  fixed RCA pipeline (not a multi-prompt/model evaluation product).

## Example Or Blueprint Only (Not Loaded By Runtime)

These files are governance examples. Nothing under `src/` loads them as a
registry, flag service, vector index, or scheduled evaluation job:

- `ops/product/prompt-registry.example.yml`
- `ops/product/feature-flags.example.yml`
- `ops/product/evaluation-dataset.example.yml`
- `ops/product/evaluation-responses.example.yml`
- `ops/product/run_evaluation.py` (offline YAML comparator)
- `ops/product/rag-governance.md`

`prompt_version` in the LLM report generator is a config string, not a Prompt
Registry lookup. Feature flags in the example YAML do not gate runtime paths.
RAG is documented; historical-incident vector retrieval is not implemented.
Accepted feedback is stored for humans; it is not automatically converted into
evaluation samples or rollout decisions.

## Product Boundaries

- The Ops Console is intentionally framework-free and packaged with the
  backend. It is an operational interface, not a full tenant/RBAC
  administration product.
- Jira, ServiceNow, Slack, Teams, and PagerDuty code/configuration is locally
  contract-tested. Real provider acceptance still requires organization-owned
  sandbox credentials, duplicate-prevention rules, and notification endpoints.
- Platform remediation accepts only catalog-registered `action_key`, precise
  target, and RCA evidence IDs from HTTP callers; risk, expected effect, and
  rollback action are deployment-controlled catalog fields.
- The repository includes a fixed-origin HTTP remediation controller adapter
  and the MiniShop SQLite sandbox. It accepts no shell, arbitrary URL,
  Kubernetes, cloud, SSH, or raw LLM command input. Real production writes still
  require organization-owned controller credentials, workload identity, staging
  acceptance, and concrete target adapters outside this repository.
- Default RCA investigation is a fixed read-only tool plan, not an adaptive
  multi-step agent planner.

## Intended Intelligent Loop (Target Design)

The intended loop is:

1. Incident and alert data create a workflow run.
2. Tools collect bounded Evidence from metrics, logs, traces, and Runbooks.
3. LLM report generation creates a structured RCA report or deterministic
   fallback.
4. Human reviewer records feedback on root cause, missing evidence, and
   recommendation safety.
5. Feedback becomes evaluation samples and regression gates for Prompt,
   retrieval, and model changes.
6. Feature Flags control staged rollout of new prompts, RAG indexes,
   connectors, and remediation capabilities.

Today, steps 1–4 are largely implemented for the fixed pipeline. Steps 5–6 are
documented and partially offline-scripted, not runtime product features.

## Remaining External Or Broader-Product Gates

1. Run PostgreSQL, Kafka, OIDC, observability, LLM, Jira/ServiceNow, and
   notification acceptance in the organization staging environment.
2. Add scheduled evaluation jobs that compare real prompt/model/retrieval
   variants beyond the deterministic three-scenario E2E gate.
3. Wire Prompt Registry / Feature Flags into runtime only after evaluation
   gates exist; until then keep them as examples.
4. Add tenant management and RBAC/ABAC administration screens if the platform
   becomes multi-team self-service.
5. Validate the platform remediation controller, action catalog, workload
   identity, execution lease recovery, and concrete target adapters in staging
   before production writes.
