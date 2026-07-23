# High-Risk Remediation Approval Boundary

The current platform is read-mostly and approval-first. Any future remediation
feature must keep execution behind a separate trust boundary.

## Required Flow

1. RCA report suggests a remediation candidate.
2. System creates a dry-run plan with target, command, expected effect, risk,
   rollback command, and evidence references.
3. Human reviewer approves with `If-Match`, `Idempotency-Key`, and scoped
   permission.
4. Executor checks feature flag, tenant policy, maintenance window, and
   rollback availability.
5. Execution result and rollback material are written to audit storage.

## Hard Blocks

- No execution when Evidence is incomplete.
- No execution when rollback is missing.
- No execution from raw LLM text.
- No execution without tenant-scoped permission.
- No execution if kill switch is disabled.

## Interview Boundary

It is safe to say the project has designed the remediation governance boundary.
Do not say automated remediation execution is implemented unless a real
executor, approval store, rollback runner, and fault tests exist.
