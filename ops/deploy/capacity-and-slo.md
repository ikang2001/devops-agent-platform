# Capacity And SLO Gate

## Service Level Objectives

- API availability: target 99.9% monthly for authenticated business APIs.
- Error rate: less than 1% over a 10 minute load window.
- Latency: p95 below 500 ms and p99 below 1 second for API paths exercised by
  `ops/load/step4-acceptance.js`.
- Kafka safety: no unbounded DLQ growth and no unacknowledged terminal message
  after worker recovery.
- Outbox freshness: backlog oldest age must stay within the release-specific
  operational threshold.

## Required Load Runs

Run the same script at 1x, 2x, and expected peak. Do not invent numbers; attach
the raw k6 summaries and dashboard links.

```powershell
$env:BASE_URL = "https://staging-devops-agent.example.com"
$env:ALERT_WEBHOOK_SECRET = "<from-secret-manager>"
$env:TENANT_ID = "step5-capacity"
k6 run .\ops\load\step4-acceptance.js
```

## Evidence To Capture

- k6 p95, p99, failure rate, dropped iterations, and check success.
- PostgreSQL active connections, slow queries, locks, and pool wait time.
- Kafka Consumer lag, DLQ growth, and broker error rate.
- Outbox backlog age and publish success rate.
- CPU, memory, pod restarts, HPA replica count, and readiness transitions.
- External gateway latency for Prometheus, Loki, Tempo, LLM, and ticketing.

## Calibration Rule

Alert thresholds may be tuned only after evidence is attached. A threshold that
is changed without a matching load or fault record is not considered accepted.
