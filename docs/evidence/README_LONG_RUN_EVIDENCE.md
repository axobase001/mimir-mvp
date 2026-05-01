# Long-Run Local Evidence

Generated: 2026-05-01 13:33:25

This folder contains a redacted, public-safe evidence package for local Skuld/Mimir long-run tests. It is derived from `audit_output/` plus selected numeric artifacts that were identified as public-safe. It does not copy raw state snapshots, private conversation logs, credentials, contacts, emails, private prompts, or proprietary SEC/belief-graph implementation files.

## Conservative Public Claims

| Claim | Source file | Confidence | Caveat |
| --- | --- | --- | --- |
| Current Aldebaran local state snapshot reports 3,542 cycles. | data/brains/b4cd7a4c-8bde-4551-afd5-b336ad191ce1/state.json | high | Only top-level cycle_count is exported. Raw state is not copied. |
| Earlier dual-instance summary independently records 2,167 cycles and 225 beliefs for Aldebaran. | paper_data/dual_instance_evidence.json | high | Do not state 3,542 cycles with 225 beliefs; those numbers come from different sources. |
| Antares dev and beta instance records exist. | paper_data/dual_instance_evidence.json; paper_data/antares_state_*.json | high | Public evidence stays at summary level. |
| Early learning curves show belief and SEC cluster evolution. | mimir_learning_curve.csv; skuld_first_breath/cycle_history.csv | high | Early-run trend evidence, not a standardized benchmark. |
| One SEC analysis artifact shows positive/negative C-value differentiation and association with belief formation. | paper_data/reviewer_response/sec_statistics.json | high | Single local analysis artifact. |
| One cost artifact records 354 LLM calls and 143,075 tokens. | paper_data/reviewer_response/cost_verification.json | medium | Single audited run; not a general cost estimate. |

## Public Files

- `cycle_metrics_public.csv` and `cycle_metrics_public.json`: canonical public metrics only.
- `sec_statistics_public.json`: numeric SEC summary from one analysis artifact.
- `first_breath_cost_public.json`: numeric token/cost summary from one audited run.
- `evidence_manifest_public.json`: public-safe source map and exclusion list.
- `REDACTION_CERTIFICATE.md`: redaction and exclusion record.

## Important Metric Handling Note

The original audit identified recursive JSON counter fields in state snapshots. Some of these counters are structural artifacts of the serialized state, not canonical cognitive metrics. For example, the Aldebaran state snapshot can report a top-level `cycle_count=3542`, but recursive fields such as structural belief/container/goal counters must not be presented as verified public cognitive metrics. This public package therefore exports the 3,542 cycle count only from that state snapshot and relies on the earlier dual-instance summary for the 2,167-cycle / 225-belief Aldebaran record.

## What this does not claim

- It does not claim AGI.
- It does not claim consciousness.
- It does not claim first-in-the-world status.
- It does not claim LLM swap survival has been verified.
- It does not claim token usage definitely decreases with repeated tasks.
- It does not claim 3,542 cycles with 225 beliefs, because those values come from different source records.
- It does not publish raw private state or proprietary SEC/belief-graph implementation details.
