# Live LLM Validation Plan

**Goal:** Extend the real-project report with actual provider responses, measured usage and latency, source-grounded suggestion review, a small labeled control set, and repeatability evidence.

**Execution:** Continue inline under the existing user authorization. Credentials remain in .env and are never copied into artifacts or command output. Production files and prompt remain unchanged.

- [x] Verify authentication/model availability; record requested and returned model identities.
- [x] Freeze 5 long functions per pinned project using deterministic SHA-256 ranking (seed 20261010), plus 8 labeled control functions before inference. Preserve source paths, commits, hashes and prompts.
- [x] Use the original _analyze_all/_analyze_batch/parser, observing SDK calls without changing request parameters. Process one project group at a time (5 snippets/request). Abort further paid calls after 100,000 observed completion tokens or repeated transport failures.
- [x] Repeat the Requests batch twice (three observations total); compare both exact findings and function-level coverage, with scope limitations.
- [x] Run the 8 controls in two original-size batches; retain outcome labels and source-grounded review separately from synthetic target-smell detection.
- [x] Run one end-to-end main.main CLI invocation against a copy of one original source file with few candidates; preserve mapping to its upstream source.
- [x] Review every real-project suggestion into supported/useful, supported/low-priority, or unsupported/context-insufficient. These are Codex judgments, not human gold labels or confirmed bugs.
- [x] Save actual provider usage, raw output, parse validity, end-to-end timings, failures and immutable evidence hashes; do not count preflight as evaluation coverage.
- [x] Integrate results into the existing report and README, preserve the prior offline findings, audit metrics and secret exclusion, and request a read-only review.

Preflight on 2026-10-10: models.list returned deepseek-flash and deepseek-v4-pro; a minimal deepseek-chat request succeeded and returned deepseek-flash (7 input / 1 output tokens). Sandbox network failed; approved normal-network execution succeeded. No product configuration change was necessary.
