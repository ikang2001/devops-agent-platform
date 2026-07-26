# DevOps Controlled RCA Pipeline Platform

This repository is a **production-oriented incident and RCA job orchestration
backend**, not a free-form autonomous troubleshooting agent and not a general
automated remediation engine.

Honest product name for interviews and design reviews:

> Controlled observability collection pipeline + reliable workflow backend
> (Outbox, leases, idempotency, approval gates), with optional LLM report
> packaging and human-reviewed remediation plans.

The default RCA path is a **fixed server-published read-only plan**:
metrics → logs → traces → runbooks. LLM output is a structured candidate for
human review and cannot confirm a root cause. Alert ingestion does not
auto-start RCA unless an explicit operator action (or a future policy switch)
does so.

Deployments may select one of three server-published static plans. The two
trace-free variants omit Tempo but remain fixed metrics → logs → runbooks
workflows; they are not adaptive tool selection. Read-only step-failure
continuation is separately opt-in and defaults to fail-fast. A partial report
must cite at least one collected Evidence item, carries the failed step IDs,
is confidence-capped at `0.4`, and cannot be `CONFIRMED`.

## Step 4 Status

The Step 4 engineering implementation and repeatable acceptance harness are
complete. Automated local acceptance covers the full test suite, Ruff, the
linear PostgreSQL migration chain, monitoring asset contracts, Prometheus rule
validation, input-boundary property tests, production Skeleton isolation, and
source guards against raw exception logging. Real PostgreSQL, Kafka, OIDC,
observability, LLM, ticketing, load, and fault-injection sign-off must still be
executed in the target environment; see
[`STEP4_ACCEPTANCE.md`](STEP4_ACCEPTANCE.md) and
[`ops/acceptance/README.md`](ops/acceptance/README.md).

## Step 5 And Step 6 Status

Step 5 productionization **examples** are present: a multi-stage non-root
`Dockerfile`, production rehearsal Compose stack, Kubernetes skeleton manifests,
CI quality and supply-chain gates, and release, rollback, capacity, backup, and
restore runbooks. These are deployable starting points, not a signed production
topology. See [`STEP5_PRODUCTION.md`](STEP5_PRODUCTION.md) and
[`ops/deploy/README.md`](ops/deploy/README.md).

Step 6 mixes **implemented product surfaces** with **governance blueprints**:

| Status | Capability |
|---|---|
| Implemented locally | Same-origin Ops Console shell, immutable RCA feedback API, opt-in Jira/ServiceNow adapters, Alertmanager multi-channel examples, MiniShop three-scenario E2E gate, approval-first remediation plan API, MiniShop allowlisted remediation sandbox |
| Example / not wired into runtime | Prompt Registry YAML, Feature Flag YAML, offline evaluation dataset/runner, RAG governance markdown |

Do not describe Prompt Registry, Feature Flags, RAG, or the feedback→evaluation
loop as a running product system. Feedback is persisted; automatic sample
export, scheduled multi-variant evaluation, and flag-gated prompt rollout are
not implemented in `src/`. See
[`STEP6_PRODUCTIZATION.md`](STEP6_PRODUCTIZATION.md),
[`ops/product/README.md`](ops/product/README.md), and
[`缺少内容.md`](缺少内容.md).

The remaining boundary is external acceptance plus the incomplete product loop
above. Real vendor credentials, notification endpoints, OIDC, and staging
infrastructure are intentionally absent from the repository. The platform
remediation path has PostgreSQL plan state, tenant policy, ETags, idempotency,
evidence gates, human approval, a fixed HTTP controller adapter, an action
catalog, maintenance windows, and a default-off kill switch. The repository
still has no Kubernetes, cloud, SSH, shell, or arbitrary-command production
write adapter, so it must not be described as a general automated remediation
engine.

The current Step 4 implementation includes:

- `GET /healthz`, `GET /readyz`, and `GET /metrics`
- production startup guard that rejects Skeleton runtime fallback
- bounded, concurrency-safe business API rate limiting with fixed-cardinality logs
- property-based C0/DEL and sensitive-value boundary tests
- structured JSON logs with request trace IDs and bounded access logging
- redacted HTTP error envelopes for application exception messages
- PostgreSQL alert ingestion with idempotency and incident correlation
- tenant-scoped Incident read and cursor-paginated list APIs
- authenticated, version-fenced, idempotent Incident resolution and closure with audit
- authenticated, idempotent RCA scheduling with transactional transitions
- immutable, tenant-scoped RCA reviewer feedback with redaction and audit
- authenticated, version-fenced, idempotent RCA workflow cancellation
- atomic RCA execution claims with expiring worker leases and takeover support
- owner-checked workflow heartbeats that stop stale or expired executors
- fenced workflow completion for successful and failed RCA attempts
- versioned `rca.requested` consumer contracts and canceled-duplicate dispatch
- manual-commit Kafka polling with rewind-on-retry and reliable dead letters
- redacted retry and dead-letter reason summaries for raw message processors
- redacted background Worker health and Outbox retry/failure error summaries
- cooperative RCA consumer runner with backoff, health, and Runtime supervision
- opt-in production RCA consumer assembly with strict configuration validation
- low-cardinality RCA consumer metrics, alerts, and failure runbooks
- bounded Kafka consumer Lag aggregation without partition labels
- Agent execution coordination with heartbeat cancellation and fenced completion
- configurable fixed read-only Agent plans with tool version, permission, risk, and timeout gates
- opt-in partial read-only collection with zero-evidence failure and confidence guardrails
- optional structured LLM RCA reports with timeout, circuit breaker, and fallback
- low-cardinality LLM success, fallback, circuit, and latency metrics
- transactional Outbox persistence, leasing, Kafka publication, and retries
- graceful application lifecycle and Outbox Worker shutdown
- opt-in audit retention Worker with bounded RCA audit cleanup batches
- low-cardinality audit retention metrics, alerts, runbook, and dashboard panels
- opt-in remediation lease reclaim Worker with bounded polling and fenced failure closure
- low-cardinality remediation reclaim health, metrics, alerts, and failure runbook
- bounded Prometheus Metrics, Loki Logs, and Tempo Traces read-only adapters
- tenant-scoped, versioned, bounded read-only Runbook retrieval
- authenticated Runbook draft and publication workflow with aggregate ETags
- RCA-derived, idempotent local Ticket Draft creation and read APIs
- version-fenced, idempotent human approval or rejection of Ticket Drafts
- idempotent external Ticket submission request recording through Outbox
- tenant-scoped Ticket submission status query API with bounded results
- bounded Ticket submission requested-message handling behind a ticketing port
- ticket submission raw-message ACK, retry, and dead-letter classification
- manual-commit Ticket submission Kafka adapter with rewind-on-retry
- opt-in Ticket submission consumer assembly requiring a ticketing gateway
- cooperative Ticket submission consumer runner with backoff and Runtime health
- bounded HTTP JSON TicketingGateway adapter with idempotency and trace headers
- opt-in bounded Jira Cloud and ServiceNow direct TicketingGateway adapters
- low-cardinality HTTP JSON TicketingGateway metrics, alerts, and dashboard panels
- low-cardinality Ticket submission consumer metrics, Lag aggregation, alerts, runbook, and dashboard panels
- idempotent Runbook draft and atomic publication management services
- tenant-scoped Remediation Plan API with evidence gates, action catalog,
  ETags, idempotency, approval separation, maintenance window, and kill switch
- bounded HTTP RemediationExecutor adapter that only sends structured
  pre-registered action keys to a fixed controller origin
- Prometheus recording rules, alerts, and a provisioned Grafana dashboard
- opt-in live PostgreSQL, Kafka, and external HTTP adapter acceptance tests
- k6 latency/capacity thresholds and deployment-neutral fault recovery probes
- same-origin Ops Console for Incident, RCA, feedback, ticket, and remediation workflows
- allowlisted MiniShop SQLite remediation planning, approval, execution, rollback, and audit

## Architecture Boundary

- `interfaces`: HTTP DTOs, routers, middleware, exception handlers.
- `application`: use-case commands and orchestration services.
- `domain`: pure domain models, enums, and application exceptions.
- `ports`: outbound contracts consumed by application services.
- `infrastructure`: config, database models/repositories, logging context,
  observability, notification, ticketing, and remediation adapters.
- `agent`: deterministic Agent workflow planning and execution coordination.
- `tools`: tool definition, registry, permission, read-only execution, and risk gates.

## Current Business Boundary

`POST /api/v1/alerts` uses the real PostgreSQL transaction path when the normal
runtime is enabled. Production requires HMAC-SHA256 Webhook authentication:
send the Unix timestamp in `X-DevOps-Agent-Timestamp` and the lowercase
`sha256=<hex>` signature in `X-DevOps-Agent-Signature`, calculated over
`<timestamp>.<raw-request-body>`. A bounded timestamp window reduces replay
risk, while `external_event_id` preserves idempotency for legitimate retries.
Webhook shared secrets are rejected at Settings and authenticator construction
time if they are too short, surrounded by whitespace, or contain control
characters, including ASCII DEL, that could contaminate request headers or
audit logs.
Its application command validates identity fields and timezone-aware timestamps
independently of the HTTP DTO. Alert summaries are normalized to a single
redacted line before Alert storage or Incident title
derivation. Administrators with `incidents:rca` may start analysis through
`POST /api/v1/admin/tenants/{tenant_id}/incidents/{incident_id}/rca`. The route
requires an `Idempotency-Key`, derives `operator_id` exclusively from the
authenticated administrator, and no longer accepts tenant or operator identity
in a request body. It atomically marks the incident as analyzing, creates a
pending `WorkflowRun`, and appends an `rca.requested` Outbox event. The HTTP
transaction never calls an Agent or another external service directly.
All HTTP write routes share the same `Idempotency-Key` header boundary:
bounded length and no whitespace or ASCII control characters, including DEL,
before any application service can hash or persist the retry key.
Tenant, incident, workflow, operator, and Runbook path identity segments share
the same no-whitespace/no-control-character HTTP boundary before authorization
or application commands run.
Administrators with `rca:read` may retrieve a single workflow result from
`GET /api/v1/admin/tenants/{tenant_id}/workflow-runs/{workflow_run_id}/result`;
the response includes a strong ETag for the WorkflowRun version. Administrators
with `rca:cancel` may cancel a `PENDING` or `RUNNING` workflow through
`POST /api/v1/admin/tenants/{tenant_id}/workflow-runs/{workflow_run_id}/cancellation`.
The cancellation route requires both `If-Match` and `Idempotency-Key`, redacts
the reason before storage, emits a content-free `rca.canceled` Outbox audit
event, and releases the active workflow slot so a later RCA can be scheduled.
If an old `rca.requested` message is delivered after cancellation, the Consumer
observes the terminal `CANCELED` workflow and acknowledges it without starting
Agent execution.
Administrators with `incidents:read` may retrieve a tenant-scoped Incident from
`GET /api/v1/admin/tenants/{tenant_id}/incidents/{incident_id}`. The response
returns the current strong ETag plus redacted title, resolution, and closure
details, but omits idempotency hashes, request fingerprints, and internal
resolution or closure traces.
The matching collection endpoint,
`GET /api/v1/admin/tenants/{tenant_id}/incidents`, accepts repeated `status`
filters, a maximum `limit` of 100, and an opaque `cursor`. It uses descending
`(updated_at, incident_id)` keyset pagination and returns `next_cursor` only
when another page exists; it never uses an unbounded query or offset scan.
Incident cursors are URL-safe, bounded, and rejected if the outer query value
or decoded payload contains ASCII control characters.
Administrators with `incidents:resolve` may resolve an `OPEN` or `ANALYZING`
Incident through
`POST /api/v1/admin/tenants/{tenant_id}/incidents/{incident_id}/resolution`.
The mutation requires a quoted positive `If-Match` version and an
`Idempotency-Key`. Resolution reason text is normalized and redacted before it
is stored or hashed; the Incident update and content-free `incident.resolved`
Outbox audit event commit in one transaction. Same-request retries recover the
stored result, while stale versions, reused keys, cross-tenant access, and
second terminal transitions fail closed. Resolution and closure also reject
incidents that still have a `PENDING` or `RUNNING` RCA workflow, preventing
manual terminal facts from racing with automated Evidence or Report writes.
Administrators with `incidents:close` may close only a `RESOLVED` Incident
through
`POST /api/v1/admin/tenants/{tenant_id}/incidents/{incident_id}/closure`.
Closure has its own `If-Match`, `Idempotency-Key`, redacted reason, and
content-free `incident.closed` Outbox audit event. This keeps technical
recovery (`RESOLVED`) separate from administrative completion (`CLOSED`).

Skeleton-mode tests still return HTTP 501 without external dependencies. The
production RCA Consumer remains disabled by default. When explicitly enabled,
the default policy requires Kafka, Prometheus, Loki, and Tempo; trace-free fixed
policies require Kafka, Prometheus, and Loki only. `build_runtime()` assembles
the Kafka consumer, dead-letter producer, lease-aware coordinator, selected
fixed Agent workflow, SQL-backed permission checker, bounded tool executor, and
only the observability resource shutdown hooks required by that plan. The application
and SQLAlchemy layers provide atomic `PENDING` to `RUNNING`
claims, reject duplicate execution while a lease is active, and allow takeover
after lease expiry. Active owners can renew leases through an atomic heartbeat;
wrong, expired, or stale owners receive a hard lease-loss signal. These
services also fence terminal updates by both worker identity and execution
attempt, so late results cannot overwrite a newer takeover. Explicit Worker IDs
for all background workers are validated and preserved exactly; default Worker
IDs and dead-letter publisher client IDs are derived with deterministic hash
suffixes when needed, instead of silently truncating long identities. Worker
runners, message handlers, workflow execution commands, and the Outbox dispatcher also reject
whitespace- or control-character-contaminated identities at construction time, so direct tests or
alternate containers cannot bypass the deployment Settings guard. These
checks share a domain-level worker identity validator to keep every boundary on
the same rule. These capabilities
now include parsing the published `rca.requested` v1 envelope and translating it
into an atomic workflow claim. A bounded Kafka `run_once()` adapter can ACK
handled or unrelated shared-topic events, rewind retryable records, and publish
non-retryable records to a dead-letter Topic before committing their source
offset. Enabled Consumers reject dead-letter Topics that match the shared source
Topic, so invalid messages cannot be routed back into the business event stream.
When both RCA and Ticket submission Consumers are enabled on that shared Topic,
their Consumer group IDs must stay distinct so one business consumer cannot ACK
the other stream's events as unrelated messages. Their dead-letter Topics must
also stay distinct, keeping replay, retention, and runbook ownership separate
for each business stream. Settings validation also rejects malformed Kafka
Topic names, client IDs, and Consumer group IDs before runtime assembly reaches
the Kafka adapters. Kafka security protocol and SASL credentials are validated
as one configuration unit, so authentication mistakes fail before any Producer
or Consumer opens network resources.
A long-running runner adds backoff, health snapshots, and cooperative
shutdown. Raw message processors keep retry and dead-letter reasons single-line,
bounded, and redacted before those summaries can reach health snapshots, logs,
or dead-letter metadata. The RCA Consumer runner also sanitizes retry and system
error summaries before exposing them through health. RCA Consumer, Ticket
Submission Consumer, and Outbox Worker cycle-failure logs contain only fixed
event text plus validated worker identity and failure-count fields; raw
exceptions and tracebacks are excluded. Their health snapshots retain bounded,
redacted error summaries for diagnosis. Outbox backlog refresh failures use the
same fixed-log boundary while reusing the last successful snapshot as stale
degradation data. `ApplicationRuntime`
supervises the configured RCA Consumer and reports it through readiness;
startup failures and shutdown both close Kafka,
observability, optional LLM, and database resources in dependency order. Kafka
Consumer, business-event Producer, and dead-letter Producer startup rollback
failures emit fixed warning messages without attaching third-party exception
text or tracebacks that could expose broker or SASL details; the original
startup failure remains the application exception cause. The outer
`ApplicationRuntime` applies the same boundary to database readiness failures,
unexpected Worker exits, and reverse-order resource cleanup. Cleanup logs
contain only fixed stage names, all resources still receive a close attempt,
and normal shutdown re-raises the first original failure after recording every
failed stage. HTTP access logs likewise retain only the trace ID, method,
bounded route template, status, and duration for failed requests; they never
attach the application exception or traceback. HTTP metric-observer failures
and partial `/metrics` refresh failures use fixed category messages while
preserving the original business response or scrape availability.
RCA Consumer alerting covers stopped, stalled, no-success, repeated failures,
dead-letter growth, Lag-unavailable, and high-Lag states; the matching Runbook
documents reason-code triage and replay checks without requiring raw message
export. The operations dashboard exposes dedicated RCA dead-letter growth and
retry-rate panels alongside Consumer state, throughput, and Lag panels.
A controlled `AgentWorkflowPort` adapter provides a fixed
metrics/logs/traces plan, explicit tool versions, full-plan permission
preflight, LOW/MEDIUM risk gates, per-tool timeouts, cancellation propagation,
and bounded JSON inputs and outputs. Tool results are recursively sanitized
before Evidence persistence, and public Evidence `source`/`summary` fields are
kept control-character-free and redacted again before they can feed RCA result
APIs, reports, or ticket drafts.
The RCA result query view also sanitizes historical Evidence summaries, tool
invocation summaries, and report text before serializing responses; Evidence and
Tool Invocation domain records also reject control-character-contaminated public
fields before storage, while still
excluding raw Evidence content and tool payloads.
Ticket Drafts keep multiline descriptions and rejection reasons, but reject
control-character-contaminated single-line identifiers, titles, evidence
references, recommendations, and audit identities before storage; repository
reads also reject contaminated lookup keys and map historical dirty draft rows
to persistence-integrity errors.
Alerts and Incidents also reject control-character-contaminated summaries,
titles, service identifiers, and lifecycle audit fields at the domain boundary,
while repositories map historical dirty rows to persistence-integrity errors.
WorkflowRun identities, trace fields, leases, and cancellation audit facts now
share the same control-character boundary across application commands, the
domain model, and repository reads, including historical dirty-row mapping.
Ticket submission request and result fields apply the same boundary to external
ticket identifiers, URLs, failure reasons, worker completion identity, and
bounded status-query lookups, while repository reads map historical dirty rows
to persistence-integrity errors.
The
post-claim coordinator now runs Agent and heartbeat tasks together, cancels
execution when the lease is lost, records Agent errors and timeouts as failed
runs, redacts Agent error summaries before returning them to the message
pipeline, and only allows Kafka acknowledgement after terminal state
persistence.
Outbox event metadata now rejects control-character-contaminated identifiers,
aggregate keys, event types, and trace IDs before persistence. The dispatcher
also stores only single-line retry/failure summaries and maps historical dirty
Outbox rows to persistence-integrity errors before they can be published.
Kafka publisher and consumer configuration now applies the same single-line
boundary to broker addresses, client IDs, Consumer group IDs, SASL identity
fields, dead-letter source coordinates, reason codes, reason summaries, and
header names. Raw dead-letter key/value/header bytes are still preserved as
binary payloads, so bad source messages remain replayable without letting their
metadata contaminate logs, Kafka headers, dashboards, or runbooks.
The `rca.requested` and `ticket_submission.requested` message contracts repeat
that boundary on the consuming side: dirty envelope or payload identifiers are
dead-lettered, and only clean foreign `event_type` values on the shared Topic
are acknowledged as unrelated events.
A version-pinned handler registry and bounded `ToolExecutor` now provide exact
handler routing, contract-drift checks, concurrency limits, timeout
cancellation, and JSON input/output limits. A fail-closed
`ToolPermissionChecker` validates tenant/operator identity, grant expiry, and
all required permission tags while distinguishing source outages from denials.
The SQLAlchemy permission provider stores tenant-scoped grants and normalized
permission tags, excludes revoked grants, and bounds each read. The permission
administration service supports version-fenced set/replace/revoke operations,
persistent idempotency, immutable grant history, and transactional audit events
through Outbox. Permission management commands, queries, runtime grant
snapshots, and SQLAlchemy permission adapters reject control-character
contaminated tenant, operator, requester, idempotency, and trace fields before
they can influence authorization checks or audit writes, including ASCII DEL
and contaminated permission tags. Enabling the Consumer
registers the production-bounded
`metrics.query@v1` implementation: it
resolves the incident service inside the tenant boundary, executes only fixed
Prometheus golden-signal templates, limits range-query responses, and returns
statistical summaries instead of unbounded raw samples. The matching
`logs.query@v1` implementation resolves the same trusted target, executes only
fixed Loki LogQL templates, enforces tenant headers and result limits, and
returns label-whitelisted, redacted, UTF-8-safe truncated log entries. The
`traces.query@v1` implementation searches only fixed error and slow-span
TraceQL templates, returns bounded Trace summaries without Span attributes,
and exposes incomplete-search hints instead of presenting sampled results as
exhaustive. Prometheus, Loki, and Tempo HTTP clients reject control-character
contaminated fixed endpoint URLs, Bearer tokens, tenant IDs, trace IDs, and
final query strings before network I/O; enabling the RCA consumer applies the
same endpoint and token boundary at Settings load time. Tool grants still fail
closed, so operators must receive the required tenant-scoped read tags before a
workflow can query these systems.
The default v2 plan also retrieves only published service-specific or
tenant-generic Runbooks from the relational catalog. Retrieval is bounded,
deterministically ordered, and never executes Runbook steps.
Administrators with `runbooks:write` may create or update a reviewed draft and
publish that exact version through conditional, idempotent HTTP mutations.
Draft title, summary, and step text are redacted before storage and before
content hashes are computed, so an accidental credential paste does not become
searchable Runbook content.
Runbook revisions belong to the logical `(tenant_id, runbook_key)` aggregate,
so every draft or publication request must carry the latest quoted revision in
`If-Match`. Publication archives the previous active version in the same
transaction and emits an Outbox audit event without copying Runbook content
into the event payload.
Audit retention is available as an opt-in background Worker. It purges expired
RCA Evidence and Tool Invocation rows in bounded batches, marks the owning
`workflow_runs.audit_purged_at` watermark in the same transaction, and does not
remove RCA reports, ticket drafts, or external submission records. Its metrics,
alerts, Runbook, and dashboard panels focus on Worker liveness, successful
cycle progress, repeated failures, and total cleaned workflow runs without
using tenant, workflow, or worker identifiers as labels.
Remediation execution and rollback use owner/attempt-fenced leases. An opt-in
Runtime-supervised reclaim Worker scans expired `EXECUTING` and `ROLLING_BACK`
plans in bounded batches and closes them as `FAILED` or `ROLLBACK_FAILED`.
It has cooperative shutdown, capped error backoff, readiness, low-cardinality
metrics, alerts, and a Runbook. It never retries, resumes, or takes over the
external write; an operator must reassess the incident and approve a new plan.
Administrators with `ticket_drafts:write` may generate one local Ticket Draft
from a successful workflow while `ticket_drafts:read` controls later access.
The API accepts no ticket content: title, priority, evidence references, and
recommendations are derived from the persisted incident and RCA report. Draft
creation sanitizes report title, summary, and recommendations again before
storage, so older reports or alternate generators cannot copy obvious
credentials into approval material. Draft
creation and its content-free Outbox audit event commit atomically. This stage
does not submit tickets to Jira, ServiceNow, or any other external system.
Administrators with `ticket_drafts:approve` may move version `1` exactly once
to `APPROVED` or `REJECTED` by sending a strong `If-Match` condition and an
idempotency key. Rejection reasons are sanitized before they remain in the
business database; audit events contain only their SHA-256 digest. Approval
records the approving administrator and decision trace, but does not itself
submit an external ticket or authorize remediation commands.
Administrators with `ticket_drafts:submit` may register an approved draft as
an external submission request for a normalized target such as `jira` or
`servicenow`. This operation requires the approved draft ETag, an idempotency
key, and tenant access. It writes a local `TicketSubmission` plus a
`ticket_submission.requested` Outbox event in the same transaction, but still
does not synchronously call the external ticketing system. The Outbox payload
contains identifiers and routing metadata only; ticket description and
recommendation body remain in the database for the future worker to load under
controlled permissions. The local submission state machine now also supports
idempotent terminal result recording: successful external creation moves the
submission to `SUBMITTED` with a target ticket id, while provider failure moves
it to `FAILED`. Result events include only stable identifiers and failure
digests, not raw provider error text. Administrators with
`ticket_drafts:read` can query a bounded, tenant-scoped list of submission
statuses for the workflow without sending idempotency or conditional headers;
this read path returns persisted local state only and does not contact external
ticketing providers. Provider failure text is sanitized again by the result
recording service before it is stored in the business record for privileged
audit workflows, while the default API response returns only a SHA-256 digest
so external error text is not copied to clients.
The `ticket_submission.requested` message handler is available as an
application-level Worker core: it validates the Outbox envelope, loads the
approved draft and requested submission, calls a `TicketingGatewayPort`, and
records `SUBMITTED` or `FAILED` through the same idempotent result service.
Runtime still does not enable a real Jira or ServiceNow consumer by default;
the concrete adapter remains opt-in deployment work. A raw record processor now
mirrors the RCA consumer boundary for this stream: it limits message size,
rejects invalid JSON or unsupported envelopes to dead letter, acknowledges
unrelated shared-topic events, and retries persistence, runtime, or unexpected
external failures without performing Kafka offset operations itself. The Kafka
adapter adds manual offset commit, rewind-on-retry, and dead-letter-before-commit
semantics for this stream. A cooperative consumer runner wraps that boundary
with start/close lifecycle, single-instance protection, classified backoff, and
health counters. The Application Runtime can supervise an injected runner,
expose its readiness component, and stop it before closing shared resources,
but the default runtime still does not connect to a live queue. Enabling the
Ticket submission consumer requires explicit `Settings` configuration and an
injected `TicketingGatewayPort`; missing gateway configuration fails at startup
rather than letting the worker repeatedly retry the first message. A generic
HTTP JSON `TicketingGatewayPort` adapter is available for controlled internal
ticketing middleware: it sends idempotency and trace headers, bounds response
size, sanitizes HTTP failures and business failure reasons, rejects malformed
or half-success responses, and normalizes valid responses into
`TicketingSubmitOutcome`. Ticket submission requests allow multiline ticket
descriptions but keep tenant, submission, draft, target, title, priority,
evidence, recommendation, idempotency, and trace fields single-line and
control-character-free before they can become HTTP headers or provider routing
metadata. The HTTP JSON adapter and deployment Settings apply the same boundary
to the fixed endpoint URL and Bearer token, while provider business failure
text is converted to a redacted single-line reason. The message handler redacts
failure reasons again before result recording so custom gateway implementations
inherit the same storage boundary. Deployments may now
opt in to this adapter with `DEVOPS_AGENT_TICKETING_HTTP_JSON_ENABLED=true`,
but it still requires the Ticket submission consumer to be enabled and a fixed
HTTP(S) endpoint to be configured. When a custom gateway is injected directly,
the runtime does not own or close that external lifecycle. The Ticket submission
consumer runner sanitizes retry and system error summaries before they reach
health snapshots. The metrics endpoint
now exports low-cardinality Ticket submission Consumer health, processing
totals, recent activity timestamps, consecutive failure counts, and bounded
Kafka Lag summaries without partition labels. Alerting rules detect stopped,
stalled, repeatedly failing, no-success, dead-letter growth, Lag-unavailable,
and high-Lag states without using tenant, trace, target system, partition, or
message identifiers as labels. The provisioned Grafana dashboard includes
matching panels for enablement, running state, consecutive failures, recent
success age, dead-letter growth, retry rate, processing rate, state trends, Lag
trend, and Lag collection coverage.
The HTTP JSON TicketingGateway also records fixed low-cardinality call outcomes
(`SUCCESS`, `BUSINESS_FAILURE`, `GATEWAY_ERROR`, `CANCELLED`) and latency
histograms. These metrics separate external provider instability from Kafka
consumer health without adding tenant, trace, ticket, or target-system labels.
Recording rules, alerts, a Runbook, and Grafana panels expose gateway error
ratio, business failure ratio, call rate, and P95 latency.
Jira Cloud and ServiceNow now also have opt-in direct adapters. The runtime
routes the normalized `jira` and `servicenow` targets to their vendor APIs and
may retain the HTTP JSON gateway as a fallback for other target names. Both
adapters use bounded non-redirecting HTTP requests, secret-backed Basic
authentication, stable correlation fields, sanitized provider failures, and
the same low-cardinality gateway metrics. Production startup rejects non-HTTPS
vendor base URLs. Jira sends an issue property and ServiceNow sends
`correlation_id` for downstream duplicate detection; because neither vendor
guarantees atomic create idempotency, production instances should enforce
uniqueness with a Jira automation rule or ServiceNow business rule keyed by
the supplied idempotency value.
LLM report generation is separately disabled by default. When enabled, operators
can configure an ordered provider chain instead of one hard-coded model vendor.
The recommended default is OpenAI first, DashScope second, and the deterministic
RCA generator as the final fallback. Missing provider credentials are skipped,
so the same artifact can run in different company environments with only
secret-manager values changed. Only bounded Evidence summaries are sent through
the selected provider adapter. The OpenAI provider uses the strict
OpenAI-compatible Responses API; `chat_completions` is also supported for
vendors that expose OpenAI-compatible Chat Completions. LLM request identities, prompt and
generator versions, model names, fixed endpoint URLs, and API keys are rejected
if they contain control-character contamination, including ASCII DEL, before
they can become HTTP headers, model routing metadata, prompt JSON, or audit
identifiers. The
generator redacts Evidence summary
and source fields again at the LLM boundary, so a missed upstream sanitizer does
not copy obvious credentials into the model request. Model response title,
summary, and recommendation text are redacted again before `RCAReport`
persistence; control-contaminated model text is rejected and uses the
deterministic fallback because structured JSON still remains untrusted external
output. Historical incident, RCA, and ticket-draft display text escapes any
remaining ASCII DEL as the visible `\u007f` sequence instead of returning an
invisible response character. The `RCAReport` persistence mapper revalidates
every new write and applies this compatibility only to legacy title, summary,
and recommendation fields during reads. Contaminated tenant, workflow,
Evidence, or generator identity fields remain persistence-integrity failures
instead of being silently rewritten.
Timeout, provider failure, refusal, invalid output, or fabricated Evidence
references move to the next configured provider. If all providers fail, the
report generator falls back to the deterministic `UNDETERMINED` report.

Provider failover order is configuration-only:

```text
DEVOPS_AGENT_LLM_REPORT_ENABLED=true
DEVOPS_AGENT_RCA_CONSUMER_ENABLED=true
DEVOPS_AGENT_LLM_PROVIDER_ORDER=openai,dashscope

DEVOPS_AGENT_LLM_OPENAI_API_STYLE=responses
DEVOPS_AGENT_LLM_OPENAI_MODEL=approved-openai-model
DEVOPS_AGENT_LLM_OPENAI_API_KEY=inject-from-secret-manager

DEVOPS_AGENT_LLM_DASHSCOPE_API_STYLE=chat_completions
DEVOPS_AGENT_LLM_DASHSCOPE_MODEL=qwen3.7-plus
DEVOPS_AGENT_LLM_DASHSCOPE_API_KEY=inject-from-secret-manager
```

Provider names supported in `DEVOPS_AGENT_LLM_PROVIDER_ORDER` are
`openai`, `dashscope`, `openai_compatible`, and `custom`. The built-in base URL
defaults are `https://api.openai.com` for OpenAI and
`https://dashscope.aliyuncs.com/compatible-mode` for DashScope. For DashScope,
an explicit base URL may be either
`https://dashscope.aliyuncs.com/compatible-mode` or
`https://dashscope.aliyuncs.com/compatible-mode/v1`; the adapter avoids
duplicating `/v1`.

Generic model gateways can be added to the same chain:

```text
DEVOPS_AGENT_LLM_PROVIDER_ORDER=openai,dashscope,openai_compatible
DEVOPS_AGENT_LLM_OPENAI_COMPATIBLE_API_STYLE=chat_completions
DEVOPS_AGENT_LLM_OPENAI_COMPATIBLE_BASE_URL=https://model-gateway.example.com
DEVOPS_AGENT_LLM_OPENAI_COMPATIBLE_MODEL=company-approved-model
DEVOPS_AGENT_LLM_OPENAI_COMPATIBLE_API_KEY=inject-from-secret-manager
```

The legacy single-provider variables
`DEVOPS_AGENT_LLM_PROVIDER`, `DEVOPS_AGENT_LLM_API_STYLE`,
`DEVOPS_AGENT_LLM_BASE_URL`, `DEVOPS_AGENT_LLM_MODEL`, and
`DEVOPS_AGENT_LLM_API_KEY` are still supported as a compatibility path.
Administrative HTTP routes now require a pluggable Bearer authenticator,
explicit tenant access, and operation-specific scopes. Administrators with
`tool_permissions:read` may retrieve the current permission snapshot and ETag
from
`GET /api/v1/admin/tenants/{tenant_id}/operators/{operator_id}/tool-permissions`;
inactive or never-created grants return an empty permission set plus the latest
state version. Mutating the same resource still requires
`tool_permissions:write`, `Idempotency-Key`, and a quoted `If-Match` version.
No authenticator is configured by default, so these routes fail closed. When
administrator OIDC is explicitly enabled, the runtime uses asymmetric JWT
verification with strict issuer/audience/time claims, bounded asynchronous JWKS
reads, single-flight cache refresh, key-rotation retry, and unknown-key refresh
throttling. OIDC issuer and JWKS endpoints must be fixed HTTPS URLs without
query strings, fragments, credentials, or control characters; audiences, claim
names, Bearer tokens, JWT header `kid`, and JWKS `kid` values are also kept
control-character-free, including ASCII DEL, before they can affect
authentication or key refresh. Verified administrator claim values for subject,
scope, and tenant identities are also rejected if they contain whitespace or
ASCII control characters before they can become audit or authorization facts.

## MiniShop End-to-End RCA Rehearsal

The repository includes a self-contained MiniShop fault lab that exercises the
real alert-to-RCA path through Prometheus, Alertmanager, PostgreSQL,
Redpanda/Kafka, Loki, Tempo, the four read-only Agent tools, and a deterministic
LLM contract stub.

Run all three Ground Truth scenarios from PowerShell:

```powershell
.\ops\minishop-e2e\run-e2e.ps1
```

The runner rebuilds an isolated Compose stack, grants local rehearsal-only
permissions, publishes the scenario Runbooks, injects each fault, waits for an
Incident and RCA Workflow, evaluates the report against its Manifest, writes
`ops/minishop-e2e/artifacts/results.json`, and removes the stack. Use
`-KeepStack` only when the running containers are needed for diagnosis.

The fixed administrator token used by this stack is disabled by default,
mutually exclusive with OIDC, and rejected in `prod` or `production`. It is not
a production authentication option.

See [`docs/minishop-e2e-rca-code-tour.md`](docs/minishop-e2e-rca-code-tour.md)
for the complete source-level walkthrough and
[`ops/minishop-e2e/artifacts/results.json`](ops/minishop-e2e/artifacts/results.json)
for the latest machine-readable local acceptance result.

## Local Development

Install the pinned package manager and synchronize the committed lock:

```bash
python -m pip install uv==0.11.31
uv sync --locked --extra dev
```

Run tests:

```bash
uv run pytest
```

Run the API locally:

```bash
uv run uvicorn main:app --reload
```

Apply database migrations before accepting traffic:

```bash
uv run alembic upgrade head
```

`uv.lock` is the reproducible platform dependency baseline. The MiniShop
project owns a separate lock because it has an independent package boundary.
After intentionally changing a dependency constraint, regenerate both locks
with `.\scripts\update-lockfiles.ps1`; pass `-Upgrade` only for an intentional
dependency refresh. CI rejects stale lock files through `uv sync --locked`.

The test suite verifies that Alembic has one linear Head and compiles the full
`base -> head` chain with the PostgreSQL dialect in offline mode. SQLite is used
for fast unit-level persistence tests, but it is not the production migration
dialect and cannot execute every historical PostgreSQL `ALTER` operation.

Use `.env.example` as the deployment configuration inventory. Keep the RCA
Consumer disabled until observability tenant routing, Kafka dead-letter
retention, and operator tool grants have been validated in the target
environment. All bearer tokens and LLM API keys must come from the deployment
secret manager.

## Monitoring Assets

- Prometheus example: `ops/prometheus/prometheus.example.yml`
- Recording and alerting rules: `ops/prometheus/rules/`
- Alertmanager routing and templates: `ops/alertmanager/`
- Incident response Runbooks: `ops/runbooks/`
- Grafana provisioning: `ops/grafana/provisioning/`
- Grafana dashboard: `ops/grafana/dashboards/devops-agent-operations.json`

Validate Prometheus rules in an environment containing Prometheus:

```bash
promtool check rules ops/prometheus/rules/*.yml
```

Alertmanager Slack webhook URLs must be mounted as secrets at:

- `/run/secrets/alertmanager-slack-critical-url`
- `/run/secrets/alertmanager-slack-warning-url`
