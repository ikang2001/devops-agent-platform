# Alertmanager notification profiles

`alertmanager.example.yml` is the Slack-only baseline. It remains the safest
local and first-deployment profile because it requires only the two existing
Slack webhook secrets.

`alertmanager.multichannel.example.yml` is an explicit production profile for
Critical fan-out:

- Slack on-call channel;
- Microsoft Teams Workflows through `msteamsv2_configs`;
- PagerDuty Events API v2 through `pagerduty_configs`.

Alertmanager routes stop after the first matching child unless `continue` is
enabled. The multi-channel profile therefore uses `continue: true` on the
Critical Slack and Teams routes, followed by PagerDuty as the final sibling.
Warning alerts remain Slack-only to avoid paging on lower-severity conditions.

## Required secret files

Mount these files read-only at runtime:

```text
/run/secrets/alertmanager-slack-critical-url
/run/secrets/alertmanager-slack-warning-url
/run/secrets/alertmanager-teams-critical-url
/run/secrets/alertmanager-pagerduty-routing-key
```

Do not commit secret values below `ops/alertmanager/secrets/`; that directory
is ignored by Git. Validate the chosen profile before reload:

```bash
amtool check-config /etc/alertmanager/alertmanager.yml
```

After deploying, send one synthetic Critical alert and verify one notification
and one resolved event in every enabled channel. Provider delivery is not
considered accepted until this target-environment check succeeds.
