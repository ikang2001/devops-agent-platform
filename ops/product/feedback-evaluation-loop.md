# RCA Feedback And Evaluation Loop

## Runtime Status

- **Implemented:** immutable RCA feedback API and PostgreSQL storage, plus a
  tenant-scoped read-only export for one explicitly selected feedback record as
  a machine-readable evaluation candidate.
- **Not implemented:** automatic curation or anonymization, automatic writes to
  a versioned evaluation dataset, scheduled real prompt/model variant
  generation/evaluation, or Feature-Flag-gated rollout driven by those results.
- **Implemented offline:** an explicitly approved curation CLI validates a
  human-written privacy review, maps every internal Evidence reference to a new
  public ID, and creates (never overwrites) the next dataset version.
- Offline helper: `ops/product/run_evaluation.py` compares hand-written YAML
  responses to a static dataset. It does not call the LLM gateway and does not
  read the feedback table.
- Offline gate: `ops/product/compare_evaluation_reports.py` compares two
  generated reports for the same ordered dataset and either rejects the
  candidate or hands it to a separate human release review.

Treat sections below as the target operating model, except where a path is
explicitly marked implemented.

## Feedback Record

The implemented feedback endpoint is:

`/api/v1/admin/tenants/{tenant_id}/workflow-runs/{workflow_run_id}/feedback`

Each immutable record captures:

- `tenant_id`
- `workflow_run_id`
- linked `report_id` (the workflow provides the incident relationship)
- reviewer identity
- `ACCEPTED`, `PARTIAL`, or `REJECTED` verdict
- corrected root cause
- missing Evidence types
- unsafe recommendation indexes
- follow-up label and notes
- timestamp and trace ID

Feedback text must pass the same control-character and redaction boundary as
Runbook and ticket content. Creation is idempotent, tenant-scoped, limited to
successful workflows with an available report, and emits a content-free audit
event in the same transaction.

## Evaluation Candidate Export

The implemented read-only endpoint is:

`GET /api/v1/admin/tenants/{tenant_id}/workflow-runs/{workflow_run_id}/feedback/{feedback_id}/evaluation-candidate`

It requires the dedicated `rca_feedback:export` scope. Read or write feedback
permission does not imply export permission. The caller must select one feedback
record; the service never silently merges conclusions from multiple reviewers.

The export fails closed unless the Workflow succeeded, its audit Evidence has
not been purged, the feedback still points to the persisted report, and every
report-referenced Evidence item belongs to the same Workflow execution attempt.
The response preserves the report Evidence reference order and always sets
`review_required: true`. It contains the baseline report projection, the human
verdict and correction, missing/unsafe annotations, and bounded Evidence
summaries. Report, recommendation, correction, and Evidence summary text is
redacted again at this boundary to protect exports of historical data.

The candidate payload intentionally omits raw Evidence `content_json`, feedback
notes, reviewer identity, the stored feedback trace ID, and idempotency material.
The standard HTTP envelope still carries the current request trace ID for
operations diagnostics. The candidate also omits the tenant ID, but retains
internal Workflow, Incident, report, feedback, and Evidence IDs; therefore it is
not an anonymized or share-ready dataset row.

Reading this endpoint does **not** write a file, append to
`evaluation-dataset.example.yml`, call an LLM, start an evaluation job, modify a
Prompt Registry or Feature Flag, or add content to a training set. A human must
curate, anonymize, version, and approve the candidate before dataset inclusion.
The Ops Console can download this same candidate JSON for that separate offline
review; downloading is not approval.

## Explicit Offline Curation

`ops/product/curate_evaluation_candidate.py` implements the narrow, manual
boundary between a downloaded candidate and a new dataset version. It requires:

- `approved: true` and `privacy_reviewed: true` in a separate review document;
- exact candidate case/feedback binding;
- `vN -> vN+1` version progression;
- a new public case, tenant, service, Evidence IDs, summaries, root-cause
  category, forbidden claims, and ticket priority supplied by the reviewer;
- one-to-one mapping of every candidate Evidence item; and
- no internal candidate IDs, obvious sensitive text, or `[REDACTED]` placeholders
  in the curated sample.

Run the checked-in example from the repository root:

```powershell
uv run python ops/product/curate_evaluation_candidate.py `
  --dataset ops/product/evaluation-dataset.example.yml `
  --candidate ops/product/evaluation-candidate.example.json `
  --review ops/product/evaluation-curation-review.example.yml `
  --output artifacts/evaluation-dataset-v2.yml
```

The output path must not already exist. The tool does not read PostgreSQL, call
an LLM, infer anonymized values, approve its own review, publish a dataset, run
evaluation, or alter Prompt/Feature Flag state. Repository review and an
organization-owned storage/publishing process remain required.

## Evaluation Dataset

An exported candidate becomes a curated sample only after a separate human
review. Each approved sample should include:

- anonymized incident context
- expected root cause category
- required Evidence anchors
- forbidden claims
- expected ticket priority or no-ticket decision
- prompt/model/retrieval versions used during baseline evaluation

## Regression Gate

The implemented offline sequence for a proposed prompt, model, retrieval index,
or tool-plan change is:

1. Run `run_evaluation.py` separately for the baseline and candidate responses.
2. Compare both JSON reports with `compare_evaluation_reports.py` and
   `evaluation-gate-policy.example.yml`.
3. Reject a dataset ID/version, sample count, ordered case list, or run metadata
   mismatch before making a decision. The gate also recomputes every summary
   rate from the sample results instead of trusting mutable aggregate fields.
4. Require candidate overall pass rate of at least 95%, forbidden-claim-free
   rate of exactly 100%, and no regression in any reported rate.
5. Treat `PASS` / `HUMAN_RELEASE_REVIEW` as permission to start a separate
   manual release review, never as a publish, Feature Flags change, or rollout
   action.

Create two reports from the synthetic baseline and candidate response fixtures,
then compare them:

```powershell
uv run python ops/product/run_evaluation.py `
  --dataset ops/product/evaluation-dataset.example.yml `
  --responses ops/product/evaluation-responses.example.yml `
  --output artifacts/evaluation-baseline.json

uv run python ops/product/run_evaluation.py `
  --dataset ops/product/evaluation-dataset.example.yml `
  --responses ops/product/evaluation-responses-candidate.example.yml `
  --output artifacts/evaluation-candidate.json

uv run python ops/product/compare_evaluation_reports.py `
  --baseline artifacts/evaluation-baseline.json `
  --candidate artifacts/evaluation-candidate.json `
  --policy ops/product/evaluation-gate-policy.example.yml `
  --output artifacts/evaluation-decision.json
```

The fixtures are hand-written synthetic examples. A real candidate report must
come from separately generated, version-attributed responses; the comparison
tool itself never calls a model.

The output path must not already exist. Invalid or tampered reports fail before
an output file is created. A valid quality regression writes a `FAIL` decision
and exits with status 1, so CI can stop while preserving a machine-readable
explanation. The current report contract has no latency or standalone unsafe
recommendation counter, so this gate deliberately does not invent either
metric; forbidden-claim-free rate is its safety-critical signal.

The current deterministic regression gate is implemented by
`ops/minishop-e2e/run_e2e.py`: it evaluates the three scenario Manifests,
required raw signals, four platform Evidence types, root-cause candidates, and
forbidden claims. Scheduled comparison of multiple real prompt/model/retrieval
variants remains a target-environment job because it requires real provider
credentials and versioned candidate outputs. The offline comparator does not
load the Prompt Registry or Feature Flags examples and cannot perform rollout.

## Metrics

- reviewed RCA count
- accepted report ratio
- corrected root cause ratio
- missing Evidence ratio
- unsafe recommendation ratio
- evaluation pass ratio by prompt version
- fallback ratio by model version
