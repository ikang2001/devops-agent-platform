# Step 4 Acceptance

Status: **Engineering and disposable local acceptance complete;
target-environment OIDC, load, and fault evidence required before traffic.**

Repository-local validation is complete.

Validated on 2026-07-04 and revalidated on 2026-07-05 from the `yolo`
Conda environment:

- Ruff production and test source checks: passed
- complete local suite: 1465 passed, 9 explicitly gated live tests skipped
- one linear Alembic migration chain with PostgreSQL offline compilation
- direct `alembic upgrade head --sql` generation: passed
- Prometheus `promtool` 3.13.0: 33 alert rules and 9 recording rules passed
- source guard against raw exception logging and stale stage placeholders
- production startup rejection of disabled runtime/Skeleton fallback
- bounded HTTP API rate limiting with concurrency and memory-capacity tests
- Hypothesis coverage for all ASCII C0 characters, DEL, message trust zones,
  and generated sensitive values
- static audit of all 28 text-bearing domain, command, and message dataclasses
- opt-in PostgreSQL migration, duplicate write, `SKIP LOCKED`, restart,
  takeover, and stale-owner fencing acceptance
- opt-in Kafka publish, duplicate delivery, manual commit, same-group restart,
  and dead-letter acceptance
- opt-in Prometheus, Loki, Tempo, OIDC, LLM, and Ticketing adapter contracts
- k6 p95/p99/error/drop thresholds and fault degradation/recovery probe
- disposable local Podman acceptance stack with PostgreSQL, Redpanda Kafka,
  and HTTP sandbox contracts

2026-07-05 revalidation evidence:

- `python -m pip install -e ".[dev]"`: completed in `yolo`
- `python -m ruff check src tests`: passed
- targeted Step 4 guard, rate-limit, security fuzz, and live-default gate:
  17 passed, 9 live tests skipped, 1 dependency warning
- complete local suite: 1465 passed, 9 live tests skipped, 1 dependency warning
- direct `alembic upgrade head --sql` generation: passed, generated SQL
  contains revision `20260703_0026`
- Prometheus `promtool` 3.13.0: 33 alert rules and 9 recording rules passed
- live acceptance hard-fail guard: with `DEVOPS_AGENT_RUN_LIVE_TESTS=1` and
  no target environment variables, all 9 live checks failed on missing
  dependency configuration as intended
- `ops/acceptance/run-step4-live.ps1`: refused to run without
  `DEVOPS_AGENT_TEST_POSTGRES_URL`, as intended
- local disposable acceptance stack configuration added under
  `ops/acceptance/docker-compose.step4.yml`; it supports Docker Compose or
  Podman Compose
- Podman Desktop 1.28.2 installed with `podman-machine-default` running on
  WSL2, 4 CPU, 4 GiB memory, 30 GiB disk, and D: drive mounted
- Podman engine verified with `podman info`: linux amd64, Podman 5.8.4
- `podman run --rm hello-world`: passed
- Windows proxy `127.0.0.1:7897` bridged into the Podman machine through
  `172.29.208.1:7897` for image pulls
- Step 4 local acceptance run from `yolo`:
  `.\ops\acceptance\run-step4-local.ps1 -Python python -ContainerCli podman -DataRoot 'D:\devops-agent-step4-data'`
- local acceptance result: 8 passed, 1 OIDC contract intentionally deselected,
  stack ready and `Local Step 4 acceptance passed.`

The only test warning is emitted by the installed FastAPI/Starlette TestClient
compatibility layer and does not affect application behavior.

The default repository test suite uses SQLite for fast persistence tests. The
real dependency tests live under `tests/live` and require
`DEVOPS_AGENT_RUN_LIVE_TESTS=1`; in acceptance mode a missing dependency
configuration is a hard failure rather than a silent skip. Run
`ops/acceptance/run-step4-live.ps1` with the variables documented in
`ops/acceptance/README.md`.

This report does not claim production sign-off for a managed PostgreSQL
cluster, managed Kafka broker, real OIDC provider, production LLM provider,
observability stack, ticketing system, k6 capacity run, or deployment-native
fault injection. The local Podman stack is a disposable engineering sandbox.
OIDC remains deselected locally because the production authenticator requires
HTTPS JWKS and a trusted certificate chain. These checks therefore remain
deployment sign-off items rather than simulated passes.

## Target Environment Sign-Off

Complete these checks before enabling production traffic or background
consumers.

### PostgreSQL

1. Back up the target database and record the current Alembic revision.
2. Run `python -m alembic upgrade head`.
3. Verify revision `20260703_0026`, foreign keys, unique indexes, and partial
   indexes.
4. Start the API and verify `/readyz` reports the database as `up`.
5. Exercise one rollback rehearsal against a disposable copy of production
   schema and data.

### Kafka

1. Create the business event Topic and separate RCA and ticket-submission DLQ
   Topics with the approved retention policies.
2. Use distinct Consumer groups for RCA and ticket submission.
3. Publish one valid event and verify manual offset commit after terminal
   persistence.
4. Inject one retryable failure and verify rewind without premature commit.
5. Inject one invalid contract and verify DLQ publication before source commit.
6. Trigger a rebalance or lease takeover and verify stale execution fencing.

### External Adapters

1. Verify TLS, timeout, tenant routing, and bounded responses for Prometheus,
   Loki, and Tempo.
2. Verify OIDC issuer, JWKS rotation, audience, scope, and tenant claims.
3. Verify LLM timeout, refusal, malformed response, circuit breaker, and
   deterministic fallback.
4. Verify ticketing idempotency, business failure, retry, and terminal replay.
5. Confirm deployment secrets never appear in HTTP responses, health snapshots,
   logs, metrics labels, Outbox payloads, or DLQ metadata.

### Operations

1. Run `promtool check rules ops/prometheus/rules/*.yml`.
2. Import the provisioned Grafana dashboard and verify every panel has data.
3. Load Alertmanager configuration with secret-mounted webhook URLs.
4. Fire one synthetic alert for every severity route.
5. Walk through the RCA Consumer, ticket submission, Outbox, audit retention,
   and external gateway Runbooks with the on-call owner.

Keep RCA and ticket-submission consumers disabled until all applicable checks
above are signed off in the target environment.

## Load And Fault Sign-Off

1. Run `ops/load/step4-acceptance.js` at 1x, 2x, and expected peak traffic.
2. Require error rate below 1%, p95 below 500 ms, p99 below 1 second, and zero
   dropped iterations.
3. Use `ops/acceptance/invoke-step4-fault.ps1` for each deployment-native fault.
4. Record degradation detection time, recovery time, data duplication or loss,
   Outbox backlog age, Consumer lag, database pool use, CPU, and memory.
5. Attach dashboard and log evidence here before changing this document's
   status to target-environment passed.
