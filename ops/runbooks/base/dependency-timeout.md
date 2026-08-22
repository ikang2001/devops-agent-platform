# Dependency timeout investigation

## Investigation intent

1. Confirm the affected request and time window from the incoming incident.
2. Compare dependency latency, error rate and saturation metrics.
3. Correlate logs and traces using the request or trace identifier.
4. Query topology to distinguish an upstream dependency from a local failure.
5. Search historical knowledge only as reference; current evidence has priority.

## Safety boundary

Use read-only observability tools. Do not execute remediation, shell commands or
write operations during RCA. Stop when evidence is contradictory or insufficient.
