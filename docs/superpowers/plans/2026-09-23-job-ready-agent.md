# Job Ready Static Inspection Agent Implementation Plan

> **For agentic workers:** Execute each checked task in order; use test driven development for behavior changes.

**Goal:** Deliver a reproducible Python static inspection CLI that demonstrates backend engineering and an optional LLM review workflow.

**Architecture:** Keep the existing LangGraph scan → rules → LLM → report pipeline. Make deterministic findings the CI gate, add baseline filtering and SARIF export at the CLI boundary, and keep LLM suggestions visibly separate.

**Tech Stack:** Python 3.12+, ast, LangGraph, OpenAI compatible API, pytest, GitHub Actions.

## Global Constraints

- Offline mode must never call an LLM.
- Scanned source must never be executed.
- Exit status must distinguish findings from invalid invocation.
- Output formats must be machine readable when selected.

---

### Task 1: Portable foundation

**Files:** `test_rules.py`, `test_cli.py`, `main.py`, `config.py`, `scanner.py`.

**Interfaces:** CLI `main(argv=None) -> int`; project config loaded from the scanned root; sorted relative paths in findings.

- [x] Replace the chmod based permission test with a mocked read failure and verify it passes on Windows.
- [x] Add failing tests for nonexistent roots, target project configuration, and clean JSON stdout.
- [x] Implement validation, configuration propagation, and output behavior; run the full suite.

### Task 2: Deterministic workflow

**Files:** `baseline.py`, `sarif.py`, `test_workflow.py`, `main.py`, `report.py`.

**Interfaces:** `--baseline FILE`, `--write-baseline FILE`, and `--format sarif`; baseline identity is rule ID, relative file, and line.

- [x] Write failing tests for baseline creation/filtering and SARIF structure.
- [x] Implement both exports and gate on new deterministic findings.
- [x] Run unit and CLI integration tests.

### Task 3: Credible delivery

**Files:** `README.md`, `docs/architecture.md`, `examples/`, `.github/workflows/ci.yml`, `pyproject.toml`.

**Interfaces:** One documented offline demo command, one documented optional LLM command, CI that installs and tests the package.

- [x] Add a small intentionally flawed fixture and a checked demo report.
- [x] Document design decisions, limitations, and measurable verification commands.
- [x] Run tests, package build, installed CLI smoke test, and inspect the resulting changes.
