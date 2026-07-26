# Step 6 Productization Assets

This package describes the product layer that should sit on top of the Step 4
backend and Step 5 deployment assets.

## Runtime Boundary

| Kind | Items |
|---|---|
| Implemented and testable in runtime | `/console`, immutable RCA feedback API/storage, opt-in ticketing adapters, multichannel Alertmanager **example config**, platform remediation plan API, MiniShop allowlisted remediation sandbox |
| Example / blueprint only | Prompt Registry YAML, Feature Flag YAML, evaluation dataset/responses, offline `run_evaluation.py`, RAG governance markdown |

Nothing under `src/` loads the example Prompt Registry, Feature Flags, or RAG
documents. Feedback persistence is real; automatic conversion of feedback into
evaluation samples and flag-gated prompt rollout is **not** implemented.

These documents remain the governance source of truth for extending those
capabilities.

## Files

- `console-workflows.md`: expected Ops Console pages and workflows.
- `feedback-evaluation-loop.md`: how reviewer feedback *should* become
  regression evaluation; current runtime only stores feedback.
- `prompt-registry.example.yml`: versioned prompt governance example.
  **Not loaded by runtime.**
- `feature-flags.example.yml`: rollout and kill-switch example.
  **Not loaded by runtime.**
- `evaluation-dataset.example.yml`: small non-secret evaluation dataset.
- `evaluation-responses.example.yml`: hand-written sample responses for the
  offline comparator.
- `run_evaluation.py`: offline YAML evaluator; does not call LLM or read DB
  feedback.
- `rag-governance.md`: knowledge base and historical incident retrieval rules.
  **No vector/RAG implementation in `src/`.**
- `remediation-approval.md`: high-risk action approval and rollback boundary
  for the platform API and the implemented MiniShop sandbox executor.

## Interview Boundary

It is safe to discuss the packaged Ops Console, persistent feedback API,
Jira/ServiceNow adapter code, multichannel Alertmanager example, PostgreSQL
remediation plan workflow, fixed HTTP controller adapter, action catalog, and
MiniShop remediation sandbox as locally implemented and tested.

Do **not** claim:

- real provider delivery
- general production remediation
- a wired Prompt Registry / Feature Flag / RAG product
- an automatic feedback→evaluation learning loop

until target-environment acceptance and runtime wiring have passed.
