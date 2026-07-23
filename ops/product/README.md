# Step 6 Productization Assets

This package describes the product layer that should sit on top of the Step 4
backend and Step 5 deployment assets.

## Files

- `console-workflows.md`: expected Ops Console pages and workflows.
- `feedback-evaluation-loop.md`: how reviewer feedback becomes regression
  evaluation.
- `prompt-registry.example.yml`: versioned prompt governance example.
- `feature-flags.example.yml`: rollout and kill-switch example.
- `evaluation-dataset.example.yml`: small non-secret evaluation dataset.
- `rag-governance.md`: knowledge base and historical incident retrieval rules.
- `remediation-approval.md`: high-risk action approval and rollback boundary.

## Interview Boundary

These assets are safe to discuss as Step 6 design and governance completion.
Do not say the project already has a full React console, vendor-specific Jira
or ServiceNow connector, or automated remediation executor unless those are
implemented and accepted in a real target environment.
