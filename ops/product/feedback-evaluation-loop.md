# RCA Feedback And Evaluation Loop

## Runtime Status

- **Implemented:** immutable RCA feedback API and PostgreSQL storage.
- **Not implemented:** automatic export of feedback into evaluation datasets,
  scheduled multi-prompt/model comparison jobs, or Feature-Flag-gated prompt
  rollout driven by those results.
- Offline helper: `ops/product/run_evaluation.py` compares hand-written YAML
  responses to a static dataset. It does not call the LLM gateway and does not
  read the feedback table.

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

## Evaluation Dataset

Accepted feedback becomes curated samples only after human review. Each sample
should include:

- anonymized incident context
- expected root cause category
- required Evidence anchors
- forbidden claims
- expected ticket priority or no-ticket decision
- prompt/model/retrieval versions used during baseline evaluation

## Regression Gate

Before changing a prompt, model, retrieval index, or tool plan:

1. Run the evaluation dataset.
2. Compare root-cause hit rate, evidence completeness, hallucinated claims,
   fallback rate, and latency.
3. Require manual review for any regression in safety-critical samples.
4. Roll out through Feature Flags, not direct global replacement.

The current deterministic regression gate is implemented by
`ops/minishop-e2e/run_e2e.py`: it evaluates the three scenario Manifests,
required raw signals, four platform Evidence types, root-cause candidates, and
forbidden claims. Scheduled comparison of multiple real prompt/model/retrieval
variants remains a target-environment job because it requires real provider
credentials and versioned candidate outputs.

## Metrics

- reviewed RCA count
- accepted report ratio
- corrected root cause ratio
- missing Evidence ratio
- unsafe recommendation ratio
- evaluation pass ratio by prompt version
- fallback ratio by model version
