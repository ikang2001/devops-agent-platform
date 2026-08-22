# Resource saturation investigation

## Investigation intent

1. Establish the affected service and resource from structured telemetry.
2. Check saturation, queue depth, retry and error metrics over the incident window.
3. Correlate representative logs and traces before selecting a candidate.
4. Query topology for downstream impact and identify the smallest supported blast radius.

## Safety boundary

Treat historical incidents as context, never as current facts. Report an
undetermined result when the resource cannot be identified from live evidence.
