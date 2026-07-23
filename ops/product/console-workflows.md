# Ops Console Workflow Map

## Personas

- On-call engineer: triages incidents, reviews Evidence, starts RCA, approves
  ticket drafts.
- Platform SRE: watches health, lag, backlog, and release safety.
- Admin: manages Runbooks, tool permissions, feature flags, and tenant access.

## Primary Views

### Incident Workbench

- Incident list with status, service, severity, updated time, and RCA state.
- Incident detail with alert facts, lifecycle transitions, ETag version, and
  allowed actions.
- RCA action buttons only when permission and state allow the transition.

### Timeline And Evidence

- Timeline rows for alert ingestion, workflow claims, tool calls, Evidence,
  report generation, ticket draft decisions, and submission results.
- Evidence cards show source, summary, collection time, tool version, and
  incomplete-result hints.
- Raw tool payloads stay behind privileged audit access and are not shown by
  default.

### RCA Report Review

- Report title, summary, probable cause, evidence references, and
  recommendations.
- Reviewer feedback fields: accepted root cause, missing evidence, unsafe
  recommendation, and follow-up label.
- Report revision history once persistent feedback is implemented.

### Ticket Approval

- Derived ticket draft with title, priority, evidence references, and
  recommendations.
- Approve/reject with `If-Match` and `Idempotency-Key` boundaries.
- External submission status and provider failure digest.

### Operations

- `/readyz` components, Outbox backlog, Consumer lag, DLQ growth, LLM fallback
  rate, ticketing gateway error ratio, and audit retention progress.
- Links to Grafana panels and runbooks.

## API Contract Rule

The console must use existing admin APIs first. New APIs should be added only
for product state that is not already represented: RCA feedback, evaluation
runs, feature flags, tenant governance, and remediation approvals.
