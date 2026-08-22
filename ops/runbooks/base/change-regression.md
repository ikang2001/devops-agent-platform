# Change regression investigation

## Investigation intent

1. Query recent deployment or configuration changes in the incident window.
2. Require corroborating metric, log or trace evidence before attributing a failure
   to a change.
3. Compare the affected version with the previous healthy version.
4. Record evidence IDs for the change and all causal claims.

## Safety boundary

Change evidence alone never confirms a root cause. Remediation requires a separate
human-approved workflow and is outside the RCA investigation boundary.
