# Real Python Project Validation Plan

> **For agentic workers:** Execute this evaluation inline. This is an empirical validation task, not a product implementation change.

**Goal:** Measure the current tool on pinned real Python projects and deliver reproducible metrics with explicit limitations.

**Architecture:** Keep third-party source snapshots outside the tool repository. Drive the unmodified CLI in fresh subprocesses and retain stdout, stderr, exit status, durations, source hashes, and adjudication evidence. Separate real-project performance, rule-pattern correctness, synthetic boundary coverage, and CI baseline behavior.

**Tech Stack:** Python 3.13, existing inspect-agent CLI, Git, JSON/CSV/Markdown.

## Constraints

- Do not execute target projects or install their dependencies.
- Do not change scanner rules to improve measured results.
- Use historical pinned releases, not moving default branches; this is not a latest-version vulnerability audit.
- Run sequential measured scans: one first observation plus five repeated subprocess runs per source tree. Report median and range, not a misleading p95 from six observations.
- Report physical Python lines including blanks/comments; independently inventory input files.
- Distinguish a correctly matched AST pattern from a confirmed actionable defect.
- No real-project recall without independently established complete ground truth.
- No LLM quality or cost claims without real provider calls; no key is configured in this environment.
- Preserve existing user modifications. No commits or publication are requested.

## Deliverables and steps

- [x] Fetch Requests v2.32.5, Flask 3.1.2, Click 8.3.0, HTTPX 0.28.1, Django 5.2.7 into `D:/静态工程巡检Agent/validation-2026-10-09/corpus`; capture resolved Git SHAs and licenses.
- [x] Save reusable evaluation script and fixed labeled examples in `benchmarks/`; execute the existing CLI against each production package directory and once against each full repository.
- [x] Measure input coverage, parse errors, findings per rule, repeated-output stability, process duration, and source immutability. Keep all raw command records.
- [x] Inspect real findings and record source-backed context judgments; report reviewer identity and judgment uncertainty.
- [x] Run independently specified positive/negative AST examples plus out-of-scope boundary probes; never combine their denominators with real-world findings.
- [x] Verify unchanged baseline suppression, detection of injected new findings, line-shift noise, JSON/SARIF consistency, and missing-key degradation.
- [x] Run existing tests with explicit paths and isolated writable temporary directory if permissions permit.
- [x] Produce a Chinese validation report, machine-readable metrics, reproduction instructions, and a truthful resume-ready result sentence.
