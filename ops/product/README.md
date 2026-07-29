# Step 6 Productization Assets

This package describes the product layer that should sit on top of the Step 4
backend and Step 5 deployment assets.

## Runtime Boundary

| Kind | Items |
|---|---|
| Implemented and testable in runtime | `/console`, immutable RCA feedback API/storage, single-feedback read-only evaluation-candidate export, opt-in ticketing adapters, multichannel Alertmanager **example config**, platform remediation plan API, MiniShop allowlisted remediation sandbox |
| Implemented and testable offline | Explicitly approved candidate curator; static evaluation runner; fail-closed baseline/candidate report gate that only hands a pass to human release review |
| Example / blueprint only | Prompt Registry YAML, Feature Flag YAML, evaluation fixture documents, RAG governance markdown |

Nothing under `src/` loads the example Prompt Registry, Feature Flags, or RAG
documents. Feedback persistence and the explicitly selected, read-only
evaluation-candidate export are real. The separate offline curator can validate
an explicit human review and create the next dataset version. Automatic curation
or anonymization, online approval, dataset publication, scheduled evaluation,
and flag-gated prompt rollout are **not** implemented. Every
exported candidate says `review_required: true`.

These documents remain the governance source of truth for extending those
capabilities.

## Files

- `console-workflows.md`: expected Ops Console pages and workflows.
- `feedback-evaluation-loop.md`: the implemented single-feedback candidate
  export and the human curation required before regression evaluation.
- `prompt-registry.example.yml`: versioned prompt governance example.
  **Not loaded by runtime.**
- `feature-flags.example.yml`: rollout and kill-switch example.
  **Not loaded by runtime.**
- `evaluation-dataset.example.yml`: small non-secret evaluation dataset.
- `evaluation-candidate.example.json`: synthetic candidate matching the runtime
  export contract.
- `evaluation-curation-review.example.yml`: synthetic, explicitly approved
  privacy-review input for the curator.
- `curate_evaluation_candidate.py`: offline fail-closed curator; it requires
  manual rewrites and creates a new dataset file without overwriting.
- `evaluation-responses.example.yml`: hand-written sample responses for the
  offline evaluator.
- `evaluation-responses-candidate.example.yml`: synthetic second run used to
  demonstrate report comparison; its version metadata does not activate a
  runtime prompt.
- `run_evaluation.py`: offline YAML evaluator; does not call an LLM or read DB
  feedback.
- `evaluation-gate-policy.example.yml`: hard-floor policy consumed by the
  offline report gate; it is not runtime configuration.
- `compare_evaluation_reports.py`: validates and compares baseline/candidate
  reports for the exact same ordered dataset. `PASS` only means eligible for
  human release review; it never publishes or rolls out a variant.
- `rag-governance.md`: knowledge base and historical incident retrieval rules.
  **No vector/RAG implementation in `src/`.**
- `remediation-approval.md`: high-risk action approval and rollback boundary
  for the platform API and the implemented MiniShop sandbox executor.

## Interview Boundary

It is safe to discuss the packaged Ops Console, persistent feedback API,
read-only evaluation-candidate export, explicit offline curation and report
comparison gates,
Jira/ServiceNow adapter code, multichannel Alertmanager example, PostgreSQL
remediation plan workflow, fixed HTTP controller adapter, action catalog, and
MiniShop remediation sandbox as locally implemented and tested.

Do **not** claim:

- real provider delivery
- general production remediation
- a wired Prompt Registry / Feature Flag / RAG product
- an automatic feedback→evaluation learning loop
- an online multi-reviewer approval or dataset publishing product
- automatic Prompt/Feature Flag modification or rollout after a gate pass

until target-environment acceptance and runtime wiring have passed.
