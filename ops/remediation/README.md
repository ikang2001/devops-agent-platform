# Governed Remediation Assets

This directory contains both the platform action-catalog example and the local
MiniShop exercise executor.

`actions.example.json` is the deployment-controlled catalog used by the
platform remediation API. HTTP callers choose only an `action_key`, precise
target, and RCA evidence IDs. Risk, expected effect, rollback action, and
target allowlists are loaded from this catalog and persisted with the
PostgreSQL remediation plan.

## MiniShop Allowlisted Remediation

This local exercise executor closes the remediation governance loop without
turning model output into arbitrary infrastructure access.

It supports only the three versioned MiniShop scenario IDs. A plan is derived
from the checked-in Manifest, stores a digest, expires within one hour, and
must be approved by an operator other than its creator. Execution is disabled
by default. When explicitly enabled, remediation can only call the Manifest's
fixed `POST /faults/reset` action; rollback can only reapply that same
scenario's fixed injection action. Every state change is persisted in SQLite
with an append-only digest-only audit event.

This is a MiniShop sandbox capability, not a general production remediation
engine. It intentionally has no shell, Kubernetes, SSH, cloud API, arbitrary
URL, or raw LLM command input. The platform-level PostgreSQL plan workflow,
tenant policy, ETag/idempotency, evidence gate, maintenance-window checks, and
action catalog live in the backend. Production use still requires
organization-owned controller credentials, workload identity, concrete target
adapters, and target-environment fault acceptance.

## Example

Create a plan while execution remains disabled:

```powershell
uv run python ops/remediation/minishop-remediation.py `
  --allow-insecure-http `
  plan inventory-db-timeout `
  --created-by operator_author `
  --trace-id trc_plan_001
```

Approve it with the returned plan ID and version:

```powershell
uv run python ops/remediation/minishop-remediation.py `
  --allow-insecure-http `
  approve rmp_REPLACE_ME `
  --expected-version 1 `
  --approved-by operator_reviewer `
  --idempotency-key approve_001 `
  --trace-id trc_approve_001
```

Execute only against a running local MiniShop:

```powershell
uv run python ops/remediation/minishop-remediation.py `
  --allow-insecure-http `
  --enable-execution `
  execute rmp_REPLACE_ME `
  --expected-version 2 `
  --actor operator_executor `
  --idempotency-key execute_001 `
  --trace-id trc_execute_001
```

Use `show PLAN_ID` and `audit PLAN_ID` to inspect state without contacting the
target. Do not put credentials in actor, trace, or idempotency values.
