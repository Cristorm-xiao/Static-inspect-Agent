# Inspection Improvements Implementation Plan

**Goal:** Improve candidate quality, evidence validation, bounded LLM execution, incremental reuse and baseline stability; explain and measure the changes.

**Architecture:** Read each changed Python file once using its encoding declaration. Reuse the AST for deterministic rules, function metrics and a bounded source index. Cache versioned deterministic results and validated LLM results. The LLM may request allowlisted symbol definitions for one additional analysis round; local evidence checks remain authoritative for location matching.

**Tech Stack:** Python 3.12+, existing LangGraph/OpenAI SDK, standard-library JSON caches and AST. Implementation proceeds inline with test-first checkpoints. No new third-party runtime dependencies or automatic commits.

## Constraints

- Preserve pre-existing work and historical benchmark artifacts. Never persist credentials.
- Keep rule-based exit codes and SARIF behavior. Expose incomplete LLM analysis separately.
- No execution of inspected code. Context tools resolve only indexed project source symbols, never model-provided paths.
- Keep v1 baseline reading; write structural v2 baselines. Preserve duplicate multiplicities.
- Cache keys include content/configuration/version; bypass failures, allow refresh/disable and expiry.

## Tasks and acceptance checks

- [x] **Source analysis and rule accuracy** — add `source_analysis.py`, extend `rules.py` and `scanner.py`; tests in `test_improvements.py`. Assert docstring-only length does not select a candidate, deeply nested short functions do, nested snippets are not double-sent, Latin-1 declaration reads, ignored directories are pruned, bare-except rethrow is described correctly, local eval binding is not reported. Parse once per uncached file.
- [x] **Structural baseline** — extend `baseline.py`, rule metadata and CLI tests. Write v2 fingerprints based on rule, scope and relevant node; keep v1 read compatibility. Assert a leading comment creates zero new findings while an additional mutable default still creates one.
- [x] **Evidence protocol and bounded context** — add `llm_protocol.py`; strict function IDs, relative evidence line ranges and matching excerpts, impact/suggestion required. Reject cross-function evidence, malformed records and truncated responses, deduplicate equivalent evidence. Context expansion uses a fixed symbol index, one round and bounded characters; unresolved requests remain visible.
- [x] **Bounded execution and incremental cache** — add `inspection_cache.py`, extend `llm_node.py` and `config.py`. Tests simulate delayed API calls and failures, asserting concurrency ceiling, request/input budgets, token-aware batches, independent partial success, explicit status, cache reuse and invalidation by prompt/config/source/context. SDK owns transport retries; do not add a second retry loop.
- [x] **CLI/report integration** — extend `state.py`, `main.py`, `report.py`, wheel include list and docs. Add `--no-cache` / `--refresh-cache`, show rule cache and LLM coverage/status. Test cold/warm runs, failure reporting and packaging.
- [x] **Evaluation and explanation** — compare the five pinned projects with preserved original data, add held-out real functions and deterministic regression probes, measure candidate changes and cold/warm scans, optionally a bounded real LLM smoke under existing authorization. Save results separately; do not claim a quality improvement without adjudicated evidence. Write `docs/improvements-2026-10-10.md` with architecture, options, examples, measured results and limits.
- [x] **Final review** — run complete targeted suite, inspect wheel content, use requesting-code-review read-only reviewer and resolve concrete findings.

## Test commands

Use `D:\python\python.exe -B -m pytest -q -p no:cacheprovider test_improvements.py test_cli.py test_rules.py test_llm_node.py` with plugin autoload disabled. This Windows sandbox previously cannot access pytest 0700 temporary directories; normal-permission execution may be required. Run focused failing checks first, then implement and rerun the affected tests. Fake API clients are used only at the network boundary; paid calls are never part of pytest.

## Metric definitions

Candidate reduction is not recall improvement. Cache speedup compares the same version/configuration and machine. A valid evidence excerpt proves location correspondence, not semantic correctness. Synthetic behavior checks and model availability are distinct from real-project precision. Retain old result directories unchanged.

Completed: 48 regression tests, wheel build/import smoke, two preserved live rounds, offline comparisons and read-only code/report review. Measured limitations are documented; no production accuracy claim.
