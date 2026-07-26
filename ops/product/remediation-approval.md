# High-Risk Remediation Approval Boundary

The platform remains read-mostly and approval-first. The repository now
contains two deliberately narrow remediation paths:

- Platform remediation plan API backed by PostgreSQL, scoped permissions,
  ETags, idempotency, RCA evidence gates, action catalog, maintenance window,
  default-off kill switch, and a fixed-origin HTTP controller adapter.
- Local MiniShop sandbox executor:
  `ops/remediation/minishop-remediation.py`.

The MiniShop executor only accepts the three checked-in scenario IDs. It derives
both cleanup and rollback from the Manifest, binds them to a digest, persists
approval and append-only audit state in SQLite, separates author from approver,
expires plans, and keeps execution behind an explicit kill switch. It has no
raw command, shell, arbitrary URL, cluster, cloud, or LLM-text execution path.

The platform API only accepts `action_key`, precise `target`, and RCA
`evidence_ids` when creating a plan. Risk, expected effect, rollback action, and
target allowlists come from the deployment-controlled JSON action catalog and
are stored with the plan for audit and rollback stability.

## Required Flow

1. RCA report suggests a remediation candidate.
2. System creates a dry-run plan from `action_key`, target, and evidence
   references; catalog-derived risk, expected effect, and rollback action are
   persisted with the plan.
3. Human reviewer approves with `If-Match`, `Idempotency-Key`, and scoped
   permission.
4. Executor checks kill switch, tenant policy, action catalog drift,
   maintenance window, current audit evidence availability, and rollback
   availability.
5. Execution claims a lease (`attempt` + `owner` + `lease_expires_at`), calls
   the fixed-origin controller under a request timeout shorter than the lease,
   then finishes only when owner/attempt fences match.
6. Stale `EXECUTING` / `ROLLING_BACK` plans are reclaimed to
   `FAILED` / `ROLLBACK_FAILED` via `reclaim_stale` (no automatic retry).
   See `docs/remediation-execution-lease.md`.

## Hard Blocks

- No execution when Evidence is incomplete.
- No execution when rollback is missing or when the catalog definition drifts.
- No execution from raw LLM text.
- No execution without tenant-scoped permission.
- No execution if kill switch is disabled.
- No self-approval by the plan author.
- No caller-supplied risk, rollback action, expected effect, arbitrary URL, or
  command text.

## Production Extension Gates

- Validate the tenant-scoped PostgreSQL plan store, action catalog, controller
  credentials, workload identity, and environment-specific targets in staging.
- Add dry-run and rollback verification for each concrete write adapter.
- Run failure, cancellation, partial-success, and duplicate-delivery tests
  against the real staging target.
- Keep provider credentials in the deployment secret manager.

## Interview Boundary

It is safe to say the platform has a governed remediation plan workflow,
fixed-origin HTTP executor adapter, action catalog, evidence gate, approval
separation, and Ops Console controls, and that the MiniShop sandbox has a real
executor, approval store, rollback runner, audit trail, and fault tests. Do not
call the repository a general production automated-remediation engine.
