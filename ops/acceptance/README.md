# Step 4 Live Acceptance

These checks are release gates. Run them against disposable PostgreSQL, Kafka,
OIDC, observability, LLM, and ticketing sandbox dependencies. Never point the
suite at an unapproved production tenant.

## Required environment

- `DEVOPS_AGENT_TEST_POSTGRES_URL`
- `DEVOPS_AGENT_TEST_KAFKA_BOOTSTRAP_SERVERS`
- `DEVOPS_AGENT_TEST_TENANT_ID`
- `DEVOPS_AGENT_TEST_PROMETHEUS_URL`
- `DEVOPS_AGENT_TEST_LOKI_URL`
- `DEVOPS_AGENT_TEST_TEMPO_URL`
- `DEVOPS_AGENT_TEST_OBSERVABILITY_TOKEN` when required
- `DEVOPS_AGENT_TEST_OIDC_ISSUER`
- `DEVOPS_AGENT_TEST_OIDC_AUDIENCE`
- `DEVOPS_AGENT_TEST_OIDC_JWKS_URL`
- `DEVOPS_AGENT_TEST_OIDC_TOKEN`
- `DEVOPS_AGENT_TEST_LLM_BASE_URL`
- `DEVOPS_AGENT_TEST_LLM_API_KEY`
- `DEVOPS_AGENT_TEST_LLM_MODEL`
- `DEVOPS_AGENT_TEST_TICKETING_ENDPOINT_URL`
- `DEVOPS_AGENT_TEST_TICKETING_TOKEN` when required

Run dependency contracts:

```powershell
.\ops\acceptance\run-step4-live.ps1 -Python python
```

The PostgreSQL test performs an online Alembic upgrade and uses randomly named
acceptance records. The Kafka test creates and deletes randomly named Topics.
The ticketing test creates one synthetic sandbox ticket and submits it twice to
verify provider idempotency.

## Disposable local stack

For a one-command sandbox, install Docker Desktop, Podman Desktop, or another
compatible container CLI with Compose support, activate the Python environment,
and run:

```powershell
conda activate yolo
.\ops\acceptance\run-step4-local.ps1 -Python python
```

This starts `ops/acceptance/docker-compose.step4.yml` with:

- PostgreSQL on `localhost:15432`
- Redpanda Kafka API on `localhost:19092`
- a bounded HTTP sandbox on `localhost:18080` for Prometheus, Loki, Tempo, LLM,
  and ticketing contracts

PostgreSQL uses a disposable tmpfs data directory so Windows/Podman bind-mount
permissions do not break `initdb`. Redpanda data uses the configured data root.

`run-step4-local.ps1` prefers `docker compose` when Docker is installed and
falls back to `podman compose` when only Podman is available. On Windows Home
with limited C: drive space, Podman Desktop is a good fit as long as WSL2 is
enabled and the Podman machine/container storage is placed on a larger drive.
If Docker CLI is installed but Docker Desktop is not running, force Podman with:

```powershell
.\ops\acceptance\run-step4-local.ps1 -Python python -ContainerCli podman
```

To keep Redpanda runtime data off C:, pass a data directory on a larger drive:

```powershell
.\ops\acceptance\run-step4-local.ps1 `
  -Python python `
  -ContainerCli podman `
  -DataRoot "D:\devops-agent-step4-data"
```

The local runner automatically exports the matching `DEVOPS_AGENT_TEST_*`
variables and runs the live suite except the OIDC contract. OIDC is not faked
by default because the production authenticator requires HTTPS JWKS and a
trusted certificate chain. To include OIDC, point these variables at a real
OIDC sandbox and pass `-IncludeOidc`:

```powershell
$env:DEVOPS_AGENT_TEST_OIDC_ISSUER = "https://id.sandbox.example/"
$env:DEVOPS_AGENT_TEST_OIDC_AUDIENCE = "devops-agent"
$env:DEVOPS_AGENT_TEST_OIDC_JWKS_URL = "https://id.sandbox.example/.well-known/jwks.json"
$env:DEVOPS_AGENT_TEST_OIDC_TOKEN = "<sandbox-admin-token>"
.\ops\acceptance\run-step4-local.ps1 -Python python -IncludeOidc
```

Stop and remove the local sandbox with:

```powershell
.\ops\acceptance\run-step4-local.ps1 -Down
```

This local stack is useful for repeatable engineering acceptance. It is not a
substitute for the target-environment sign-off in `STEP4_ACCEPTANCE.md`.

If image pulls time out while Windows is using a local proxy, configure the
Podman machine to reach the Windows proxy through the VM gateway address rather
than `127.0.0.1`. If Redpanda's primary registry is rate-limited, pre-pull
`docker.io/redpandadata/redpanda:v24.3.18` and tag it as
`docker.redpanda.com/redpandadata/redpanda:v24.3.18`.

## Load and capacity

Run the app with production assembly and a rate limit large enough for the load
generator's single source address. Keep gateway-level protection enabled.

```powershell
$env:BASE_URL = "https://step4.example.test"
$env:ALERT_WEBHOOK_SECRET = "<sandbox-secret>"
$env:TENANT_ID = "step4-acceptance"
$env:DURATION = "10m"
$env:READ_VUS = "50"
$env:ALERT_RATE = "50"
k6 run .\ops\load\step4-acceptance.js
```

Acceptance thresholds are less than 1% failed requests, p95 below 500 ms, p99
below 1 second, no dropped iterations, and more than 99% successful checks.
Repeat at 1x, 2x, and expected peak rate while recording PostgreSQL pool usage,
Kafka lag, Outbox backlog age, CPU, memory, and external gateway latency. The
highest stage passing every threshold is the signed capacity, not an estimate.

## Fault injection

Use deployment-native commands as script blocks. This example verifies database
degradation and recovery:

```powershell
.\ops\acceptance\invoke-step4-fault.ps1 `
  -ReadinessUrl "https://step4.example.test/readyz" `
  -Inject { kubectl scale statefulset step4-postgresql --replicas=0 } `
  -Recover { kubectl scale statefulset step4-postgresql --replicas=1 }
```

Repeat for Kafka, OIDC/JWKS, Prometheus, Loki, Tempo, LLM, and ticketing. For
dependencies that are not readiness-critical, verify bounded timeout, retry or
fallback metrics instead of expecting `/readyz` to fail. During each run,
confirm that secrets do not appear in API responses, JSON logs, metric labels,
Outbox rows, or DLQ records.

Required failure scenarios:

1. API termination during an open request, followed by graceful restart.
2. Outbox worker termination after publish and before state completion.
3. Consumer termination before and after manual offset commit.
4. Expired workflow and Outbox lease takeover by a new worker.
5. Duplicate HTTP idempotency keys and duplicate Kafka deliveries.
6. Slow, oversized, malformed, 429, and 5xx external HTTP responses.
7. PostgreSQL connection loss and Kafka broker unavailability.
8. OIDC key rotation and unknown `kid`.

Record commands, timestamps, dashboard links, observed thresholds, and recovery
time in `STEP4_ACCEPTANCE.md`. A harness being present is not a production pass;
the target environment must supply the evidence.
