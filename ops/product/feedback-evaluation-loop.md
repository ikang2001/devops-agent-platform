# RCA Feedback And Evaluation Loop

## Feedback Record

Each reviewed RCA should capture:

- `tenant_id`
- `incident_id`
- `workflow_run_id`
- report version
- reviewer identity
- accepted or corrected root cause
- missing Evidence references
- unsafe or unhelpful recommendation notes
- final usefulness rating
- timestamp and trace ID

Feedback text must pass the same control-character and redaction boundary as
Runbook and ticket content.

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

## Metrics

- reviewed RCA count
- accepted report ratio
- corrected root cause ratio
- missing Evidence ratio
- unsafe recommendation ratio
- evaluation pass ratio by prompt version
- fallback ratio by model version
