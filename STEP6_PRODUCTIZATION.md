# Step 6 Productization And Intelligent Loop

Status: **productization and governance blueprint added; full UI and provider
connectors remain target implementation work.**

Step 6 is about turning the reliable backend into a team-facing product that
can learn from feedback, compare model/prompt versions, and keep risky actions
under human control.

## Completed In Repository

- Productization package under `ops/product`.
- Ops Console workflow map for incident triage, Evidence audit, RCA report
  review, ticket approval, and operational health views.
- Human feedback and evaluation-loop specification.
- Prompt Registry example with version, owner, rollout, and evaluation gates.
- Feature Flag example for staged enablement and kill switches.
- Evaluation dataset example with expected root-cause and evidence anchors.
- RAG governance specification covering knowledge versioning, indexing,
  permissions, evaluation, and rollback.
- High-risk remediation approval boundary.

## Product Boundaries

- The current repository still ships a backend-first platform. The Step 6 UI
  documents define the expected console behavior and API boundaries, but do
  not claim a completed web frontend.
- Jira, ServiceNow, Slack, Teams, and PagerDuty are represented as integration
  boundaries. Real vendor-specific connectors require sandbox credentials and
  target-environment acceptance.
- Automated remediation remains approval-first. The platform should recommend,
  explain, and prepare rollback steps before any write action is enabled.

## Intelligent Loop

The loop is:

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

## Required Future Implementation Gates

1. Build the Ops Console against the existing admin APIs.
2. Add persistent RCA feedback records and report revision history.
3. Add evaluation runner jobs that compare prompt/model/retrieval versions.
4. Add vendor connectors after sandbox contract tests exist.
5. Add tenant management and RBAC/ABAC administration screens.
6. Add remediation execution only after approval, dry-run, audit, rollback, and
   kill-switch controls are implemented.
