# Redaction Certificate

Generated: 2026-05-01 13:33:25

## Scope

This certificate covers files generated under `public_evidence/`.

## Source Restrictions Applied

The public package was generated from `audit_output/` and selected numeric public-safe artifacts only. It does not copy:

- raw `state.json` files
- `users.db`
- conversation stores
- `skuld.log`
- raw LLM responses
- contacts, emails, private prompts
- API keys or private config files
- `brain/sec_matrix.py`
- `brain/belief_graph.py`

## Metric Restrictions Applied

Recursive JSON counters from state snapshots are not exported as canonical public metrics. The Aldebaran state snapshot contributes only its top-level `cycle_count=3542`; the 225-belief count comes only from the earlier dual-instance summary.

## Redaction Tool

All generated public files are scanned with `scripts/redact_secrets.py` after generation. A final regex scan is also run for common API key, token, GitHub token, Slack token, and email patterns.

## Result

PASS. `scripts/redact_secrets.py` was run on every generated public file, followed by a final regex scan. No common API key, token, GitHub token, Slack token, or email patterns were found.

