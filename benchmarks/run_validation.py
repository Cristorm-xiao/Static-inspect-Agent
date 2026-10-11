"""Reproducible offline evaluation of the existing CLI; never imports target code."""
from __future__ import annotations
import argparse
from collections import Counter
import csv
import hashlib
import importlib.metadata
import json
import math
import os
from pathlib import Path
import platform
import shutil
import statistics
import subprocess
import sys
import time
from datetime import datetime, timezone

TOOL = Path(__file__).resolve().parents[1]
SKIP = {".git", "__pycache__", ".venv", "venv", "env", "node_modules", ".idea", ".vscode", "build", "dist"}
sys.path.insert(0, str(TOOL))

def save(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

def digest(data):
    return hashlib.sha256(data).hexdigest()

def inventory(root):
    """Independent os.walk inventory, not the scanner's rglob implementation."""
    entries = []
    for directory, dirs, files in os.walk(root):
        dirs[:] = sorted(d for d in dirs if d not in SKIP)
        for name in sorted(files):
            if not name.endswith(".py"):
                continue
            path = Path(directory) / name
            raw = path.read_bytes()
            entries.append({"path": path.relative_to(root).as_posix(), "bytes": len(raw),
                            "physical_lines": len(raw.splitlines()), "sha256": digest(raw)})
    entries.sort(key=lambda x: x["path"])
    return {"files": len(entries), "physical_lines": sum(x["physical_lines"] for x in entries),
            "bytes": sum(x["bytes"] for x in entries), "entries": entries,
            "tree_sha256": digest(json.dumps(entries, sort_keys=True).encode())}

def git(root, *args):
    return subprocess.check_output(["git", "-C", str(root), *args], text=True, encoding="utf-8").strip()

def tool_revision(root):
    # An extracted evaluated-source.zip has no Git metadata. Do not attribute
    # it to an unrelated parent repository; the per-file hashes remain primary.
    return git(root, "rev-parse", "HEAD") if (root / ".git").exists() else None

def signature(findings, file=True):
    return Counter((x["file"], x["rule_id"], x["line"]) if file else (x["rule_id"], x["line"]) for x in findings)

class Runner:
    def __init__(self, out):
        self.out = out
        self.records = []
    def cli(self, label, target, *args, no_llm=True, fmt="json"):
        cmd = [sys.executable, "-B", "-m", "main", str(target)]
        if no_llm:
            cmd.append("--no-llm")
        cmd.extend(["--format", fmt, *map(str, args)])
        env = os.environ.copy()
        env.update({"PYTHONIOENCODING": "utf-8", "PYTHONDONTWRITEBYTECODE": "1",
                    "DEEPSEEK_API_KEY": ""})
        start = time.perf_counter()
        proc = subprocess.run(cmd, cwd=TOOL, env=env, capture_output=True, timeout=180)
        elapsed = time.perf_counter() - start
        folder = self.out / "raw"
        folder.mkdir(exist_ok=True)
        (folder / (label + ".stdout")).write_bytes(proc.stdout)
        (folder / (label + ".stderr")).write_bytes(proc.stderr)
        record = {"label": label, "command": cmd, "cwd": str(TOOL), "seconds": elapsed,
                  "exit_code": proc.returncode, "stdout_sha256": digest(proc.stdout),
                  "stdout_file": "raw/" + label + ".stdout", "stderr_file": "raw/" + label + ".stderr"}
        self.records.append(record)
        save(self.out / "commands.json", self.records)
        data = json.loads(proc.stdout.decode("utf-8")) if proc.stdout.strip() else None
        print(f"{label}: exit={proc.returncode}, seconds={elapsed:.3f}", flush=True)
        return data, record

def oracle_suite(runner, cases, label, desired_key):
    directory = runner.out / "fixtures" / label
    directory.mkdir(parents=True)
    for case in cases:
        raw = bytes.fromhex(case["source_hex"]) if "source_hex" in case else case["source"].encode()
        (directory / (case["id"] + ".py")).write_bytes(raw)
    data, record = runner.cli(label, directory)
    actual_by_file = {}
    for finding in data["rule_findings"]:
        actual_by_file.setdefault(finding["file"], []).append(finding)
    rows = []
    for case in cases:
        actual = actual_by_file.get(case["id"] + ".py", [])
        expected = case[desired_key]
        a, e = signature(actual, False), signature(expected, False)
        rows.append({"id": case["id"], "expected": expected, "actual": actual,
                     "matches": a == e, "tp": sum((a & e).values()),
                     "fp": sum((a - e).values()), "fn": sum((e - a).values()),
                     "explanation": case.get("note", case.get("meaning", ""))})
    result = {"cases": len(rows), "matched_cases": sum(x["matches"] for x in rows),
              "tp": sum(x["tp"] for x in rows), "fp": sum(x["fp"] for x in rows),
              "fn": sum(x["fn"] for x in rows), "details": rows, "exit_code": record["exit_code"]}
    save(runner.out / (label + ".json"), result)
    return result

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--corpus", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--repeats", type=int, default=5)
    args = parser.parse_args()
    if args.repeats < 3:
        parser.error("at least 3 repeats required")
    args.corpus = args.corpus.resolve()
    args.output = args.output.resolve()
    args.output.mkdir(parents=True, exist_ok=False)
    runner = Runner(args.output)
    corpus = json.loads((Path(__file__).parent / "corpus.json").read_text(encoding="utf-8"))
    code_files = ["main.py", "config.py", "graph.py", "scanner.py", "rules.py", "llm_node.py",
                  "baseline.py", "report.py", "sarif.py", "state.py", "pyproject.toml"]
    tool_hashes = {name: digest((TOOL / name).read_bytes()) for name in code_files}
    env = {"timestamp_utc": datetime.now(timezone.utc).isoformat(), "python": sys.version,
           "python_executable": sys.executable, "platform": platform.platform(),
           "cpu": platform.processor(), "logical_cpus": os.cpu_count(),
           "dependencies": {n: importlib.metadata.version(n) for n in ["langgraph", "openai", "python-dotenv", "pytest"]},
           "tool_git_head": tool_revision(TOOL),
           "tool_source_hashes": tool_hashes, "llm": "disabled; no real-provider quality evaluation",
           "timing": "sequential fresh CLI subprocesses; first observation separate, then repeats; OS caches not flushed",
           "repeats": args.repeats}
    save(args.output / "environment.json", env)
    shutil.copy2(Path(__file__).parent / "cases.json", args.output / "cases.json")
    shutil.copy2(Path(__file__), args.output / "run_validation.py")
    shutil.copy2(Path(__file__).parent / "corpus.json", args.output / "corpus.json")
    (args.output / "tool-source").mkdir()
    for name in code_files:
        shutil.copy2(TOOL / name, args.output / "tool-source" / name)
    summaries = []
    source_findings = []
    checks = []
    for item in corpus:
        name = item["name"]
        repo = args.corpus / name
        target = repo / item["source"]
        actual_commit = git(repo, "rev-parse", "HEAD")
        if actual_commit != item["commit"] or git(repo, "status", "--porcelain"):
            raise RuntimeError("Corpus is not the specified pristine checkout: " + name)
        before = inventory(target)
        save(args.output / "inventories" / (name + "-source.json"), before)
        observations = []
        first_data = None
        for index in range(args.repeats + 1):
            data, record = runner.cli(f"{name}-source-{index}", target)
            if first_data is None:
                first_data = data
            observations.append(record)
            checks.append({"check": f"{name}-repeat-{index}", "passed":
                           data == first_data and data["files_scanned"] == before["files"]
                           and record["exit_code"] == int(bool(data["rule_findings"]))})
        findings = first_data["rule_findings"]
        for finding in findings:
            source_findings.append({"project": name, **finding,
                "source_url": item["repository"] + "/blob/" + item["commit"] + "/" +
                              item["source"] + "/" + finding["file"] + "#L" + str(finding["line"])})
        timings = [r["seconds"] for r in observations[1:]]
        # Candidate counting is outside measured subprocess scans and invokes no provider.
        from llm_node import extract_candidates
        candidates = [c for entry in before["entries"] for c in extract_candidates(str(target / entry["path"]), 30)]
        summary = {**item, "files": before["files"], "physical_lines": before["physical_lines"],
                   "first_seconds": observations[0]["seconds"], "repeat_seconds": timings,
                   "median_seconds": statistics.median(timings), "min_seconds": min(timings), "max_seconds": max(timings),
                   "lines_per_second": before["physical_lines"] / statistics.median(timings),
                   "findings": len(findings), "rule_counts": dict(Counter(x["rule_id"] for x in findings)),
                   "llm_candidate_functions_ge30": len(candidates), "llm_batches_at_5": math.ceil(len(candidates) / 5),
                   "max_candidate_lines": max((c["end_line"] - c["line"] + 1 for c in candidates), default=0)}
        baseline = args.output / "baselines" / (name + ".json")
        baseline.parent.mkdir(exist_ok=True)
        _, baseline_record = runner.cli(name + "-write-baseline", target, "--write-baseline", baseline)
        filtered, filtered_record = runner.cli(name + "-unchanged-baseline", target, "--baseline", baseline)
        checks.append({"check": name + "-unchanged-baseline", "passed":
                       baseline_record["exit_code"] == 0 and filtered_record["exit_code"] == 0
                       and filtered["rule_findings"] == [] and filtered["baseline_suppressed"] == len(findings)})
        sarif, sarif_record = runner.cli(name + "-sarif", target, fmt="sarif")
        projected = []
        for result in sarif["runs"][0]["results"]:
            location = result["locations"][0]["physicalLocation"]
            projected.append({"rule_id": result["ruleId"], "file": location["artifactLocation"]["uri"],
                              "line": location.get("region", {}).get("startLine", 0)})
        checks.append({"check": name + "-sarif-parity", "passed":
                       signature(projected) == signature(findings) and sarif["version"] == "2.1.0"
                       and sarif_record["exit_code"] == int(bool(findings))})
        full_inventory = inventory(repo)
        save(args.output / "inventories" / (name + "-full.json"), full_inventory)
        full, full_record = runner.cli(name + "-full", repo)
        summary["full_repository"] = {"files": full["files_scanned"], "physical_lines": full_inventory["physical_lines"],
                                      "seconds": full_record["seconds"], "findings": len(full["rule_findings"]),
                                      "rule_counts": dict(Counter(x["rule_id"] for x in full["rule_findings"]))}
        checks.append({"check": name + "-full-coverage", "passed":
                       full["files_scanned"] == full_inventory["files"]
                       and full_record["exit_code"] == int(bool(full["rule_findings"]))})
        after = inventory(target)
        summary["source_unchanged"] = before["tree_sha256"] == after["tree_sha256"] and not git(repo, "status", "--porcelain")
        checks.append({"check": name + "-source-unchanged", "passed": summary["source_unchanged"]})
        summaries.append(summary)
        save(args.output / "projects.json", summaries)
        save(args.output / "source-findings.json", source_findings)
        save(args.output / "checks.json", checks)

    # Controlled CI experiment on a derived copy of the project with most findings.
    chosen = max(summaries, key=lambda p: p["findings"])
    original = args.corpus / chosen["name"] / chosen["source"]
    injection = args.output / "scenarios" / "new-findings"
    shutil.copytree(original, injection)
    injected_source = "from math import *\ndef f(items=[]):\n    try:\n        return eval('1')\n    except:\n        return items\n"
    (injection / "benchmark_injected.py").write_text(injected_source, encoding="utf-8")
    baseline = args.output / "baselines" / (chosen["name"] + ".json")
    added, added_record = runner.cli("baseline-injected", injection, "--baseline", baseline)
    expected_new = Counter({("benchmark_injected.py", "R004", 1): 1,
                            ("benchmark_injected.py", "R002", 2): 1,
                            ("benchmark_injected.py", "R003", 4): 1,
                            ("benchmark_injected.py", "R001", 5): 1})
    checks.append({"check": "baseline-injected-four", "passed":
                   signature(added["rule_findings"]) == expected_new
                   and added["baseline_suppressed"] == chosen["findings"] and added_record["exit_code"] == 1})
    shifted = args.output / "scenarios" / "line-shift"
    shutil.copytree(original, shifted)
    touched = {x["file"] for x in source_findings if x["project"] == chosen["name"]}
    for relative in touched:
        path = shifted / relative
        path.write_bytes(b"# benchmark: comment-only line shift\n" + path.read_bytes())
    shifted_data, shifted_record = runner.cli("baseline-line-shift", shifted, "--baseline", baseline)
    save(args.output / "baseline-experiment.json", {
        "project": chosen["name"], "original_findings": chosen["findings"], "new_findings": added["rule_findings"],
        "old_suppressed_after_injection": added["baseline_suppressed"],
        "line_shift_files": sorted(touched), "line_shift_reported_new": len(shifted_data["rule_findings"]),
        "line_shift_suppressed": shifted_data["baseline_suppressed"], "line_shift_exit_code": shifted_record["exit_code"],
        "interpretation": "All originally flagged files have one comment prepended. Re-reported old findings quantify line-based baseline noise, not new defects."})
    missing_key, missing_record = runner.cli("missing-key-fallback", original, no_llm=False)
    checks.append({"check": "missing-key-fallback", "passed":
                   missing_key["llm_findings"] == []
                   and signature(missing_key["rule_findings"]) == signature(
                       [x for x in source_findings if x["project"] == chosen["name"]])
                   and missing_record["exit_code"] == int(bool(chosen["findings"]))})
    cases = json.loads((Path(__file__).parent / "cases.json").read_text(encoding="utf-8"))
    contract = oracle_suite(runner, cases["contract_cases"], "contract", "expected")
    boundary = oracle_suite(runner, cases["boundary_probes"], "boundary", "desired")
    checks.append({"check": "contract-cases", "passed": contract["matched_cases"] == contract["cases"]})
    checks.append({"check": "tool-source-unchanged", "passed":
                   all(digest((TOOL / name).read_bytes()) == sha for name, sha in tool_hashes.items())})
    save(args.output / "checks.json", checks)
    totals = {"projects": len(summaries), "source_files": sum(p["files"] for p in summaries),
              "source_physical_lines": sum(p["physical_lines"] for p in summaries),
              "source_findings": len(source_findings), "source_rule_counts": dict(Counter(x["rule_id"] for x in source_findings)),
              "full_files": sum(p["full_repository"]["files"] for p in summaries),
              "full_physical_lines": sum(p["full_repository"]["physical_lines"] for p in summaries),
              "full_findings": sum(p["full_repository"]["findings"] for p in summaries),
              "checks_passed": sum(c["passed"] for c in checks), "checks_total": len(checks),
              "contract": {k: v for k, v in contract.items() if k != "details"},
              "boundary": {k: v for k, v in boundary.items() if k != "details"},
              "cli_invocations": len(runner.records)}
    save(args.output / "summary.json", totals)
    columns = ["name", "tag", "files", "physical_lines", "findings", "first_seconds",
               "median_seconds", "min_seconds", "max_seconds", "lines_per_second",
               "llm_candidate_functions_ge30", "source_unchanged"]
    with (args.output / "metrics.csv").open("w", newline="", encoding="utf-8-sig") as stream:
        writer = csv.DictWriter(stream, fieldnames=columns, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(summaries)
    print(json.dumps(totals, ensure_ascii=False, indent=2), flush=True)
    return 0 if all(c["passed"] for c in checks) else 1

if __name__ == "__main__":
    raise SystemExit(main())
