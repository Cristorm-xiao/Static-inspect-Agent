"""Live provider evaluation. No credentials, headers, or reasoning text are persisted."""
from __future__ import annotations
import argparse
import asyncio
from contextvars import ContextVar
from datetime import datetime, timezone
import hashlib
import io
import json
import logging
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time
from contextlib import redirect_stdout, redirect_stderr

TOOL = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOL))
import llm_node
from dotenv import dotenv_values
from openai import AsyncOpenAI
from scanner import collect_py_files

def sha(raw):
    return hashlib.sha256(raw).hexdigest()

def save(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

def read(path):
    return json.loads(path.read_text(encoding="utf-8"))

def now():
    return datetime.now(timezone.utc).isoformat()

def git(root, *args):
    return subprocess.check_output(["git", "-C", str(root), *args], encoding="utf-8").strip()

def prepare(corpus, output):
    output.mkdir(parents=True, exist_ok=False)
    manifest = read(TOOL / "benchmarks/corpus.json")
    samples = []
    cli_options = []
    for project in manifest:
        repo = corpus / project["name"]
        if git(repo, "rev-parse", "HEAD") != project["commit"] or git(repo, "status", "--porcelain"):
            raise RuntimeError("Corpus provenance mismatch: " + project["name"])
        root = repo / project["source"]
        candidates = []
        for filename in collect_py_files(root):
            found = llm_node.extract_candidates(filename, 30)
            if project["name"] == "requests" and found:
                cli_options.append((len(found), sum(len(x["source"]) for x in found), filename))
            for candidate in found:
                relative = Path(filename).relative_to(root).as_posix()
                identity = f"{project['name']}:{relative}:{candidate['line']}:{candidate['name']}"
                candidates.append({
                    **candidate, "sample_id": identity, "project": project["name"],
                    "file": project["name"] + "/" + relative, "relative_file": relative,
                    "commit": project["commit"],
                    "source_url": project["repository"] + "/blob/" + project["commit"] + "/" + project["source"] + "/" + relative + "#L" + str(candidate["line"]),
                    "source_sha256": sha(candidate["source"].encode()),
                    "file_sha256": sha(Path(filename).read_bytes()),
                    "selection_key": sha(("20261010|" + identity).encode()),
                })
        samples.extend(sorted(candidates, key=lambda c: c["selection_key"])[:5])
    controls_spec = read(TOOL / "benchmarks/llm_controls.json")
    control_dir = output / "control-source"
    control_dir.mkdir()
    controls = []
    for item in controls_spec["cases"]:
        file = control_dir / (item["id"] + ".py")
        file.write_text(item["source"], encoding="utf-8")
        extracted = llm_node.extract_candidates(str(file), 30)
        assert len(extracted) == 1, item["id"]
        candidate = extracted[0]
        controls.append({**candidate, "sample_id": item["id"], "file": "controls/" + item["id"] + ".py",
                         "project": "controls", "source_sha256": sha(candidate["source"].encode())})
    controls.sort(key=lambda x: sha(("20261010|" + x["sample_id"]).encode()))
    save(output / "samples.json", samples)
    save(output / "controls.json", controls)
    save(output / "control-labels.json", controls_spec)
    phases = [{"id": "primary-" + p["name"], "kind": "primary",
               "samples": [c for c in samples if c["project"] == p["name"]]} for p in manifest]
    requests_samples = [c for c in samples if c["project"] == "requests"]
    phases += [{"id": "repeat-requests-" + str(i), "kind": "repeat", "samples": requests_samples} for i in (2, 3)]
    phases.append({"id": "controls", "kind": "controls", "samples": controls})
    chosen = min(cli_options)
    cli_dir = output / "cli-project"
    cli_dir.mkdir()
    original = Path(chosen[2])
    shutil.copy2(original, cli_dir / original.name)
    cli_candidates = llm_node.extract_candidates(str(original), 30)
    for c in cli_candidates:
        c.update({"file": original.name, "sample_id": "cli:" + original.name + ":" + str(c["line"])})
    phases.append({"id": "cli-smoke", "kind": "cli", "samples": cli_candidates,
                   "target": str(cli_dir), "original_file": str(original),
                   "original_sha256": sha(original.read_bytes())})
    save(output / "phases.json", phases)
    product_hashes = {name: sha((TOOL / name).read_bytes()) for name in
                      ["llm_node.py", "main.py", "graph.py", "scanner.py", "config.py", "rules.py", "report.py"]}
    save(output / "provenance.json", {"date": "2026-10-10", "prepared_at": now(), "seed": 20261010,
        "selection": "5 lowest SHA256(seed|project:path:line:name) ranks per project, all functions >=30 physical lines",
        "candidate_population": 1144, "primary_sample_functions": len(samples), "control_functions": len(controls),
        "product_sha256": product_hashes, "corpus": manifest, "max_completion_token_budget": 100000,
        "request_policy": "Original temperature=0.3, timeout=30s, max_retries=2, batch size=5. No prompt or output-token override.",
        "review_policy": "All primary suggestions reviewed by Codex for source support and usefulness; not human ground truth."})
    save(output / "preflight.json", {"models": ["deepseek-flash", "deepseek-v4-pro"],
        "requested_model": "deepseek-chat", "returned_model": "deepseek-flash",
        "usage": {"prompt_tokens": 7, "completion_tokens": 1, "total_tokens": 8},
        "note": "Connectivity probe Return only []; excluded from evaluation denominators."})
    print(json.dumps({"prepared_functions": len(samples), "controls": len(controls),
                      "planned_model_batches": sum((len(p["samples"])+4)//5 for p in phases),
                      "cli_file": original.name, "cli_candidates": len(cli_candidates)}, ensure_ascii=False), flush=True)

def strict_response(content, size):
    try:
        items = json.loads(llm_node._strip_fence(content.strip()))
    except (ValueError, TypeError):
        return {"json_array": False, "strict_items": False, "items": 0}
    if not isinstance(items, list):
        return {"json_array": False, "strict_items": False, "items": 0}
    valid = all(isinstance(x, dict) and type(x.get("index")) is int and 0 <= x["index"] < size
                and isinstance(x.get("smell"), str) and bool(x["smell"].strip())
                and isinstance(x.get("reason"), str) and bool(x["reason"].strip()) for x in items)
    return {"json_array": True, "strict_items": valid, "items": len(items)}

def execute(output, only):
    config = dotenv_values(TOOL / ".env")
    api_key = config.get("DEEPSEEK_API_KEY")
    base_url = config.get("DEEPSEEK_BASE_URL", "https://api.deepseek.com/v1")
    model = config.get("DEEPSEEK_MODEL", "deepseek-chat")
    if not api_key:
        raise RuntimeError("Missing API key")
    if base_url.rstrip("/") not in {"https://api.deepseek.com/v1", "https://api.deepseek.com"}:
        raise RuntimeError("This evaluation only allows the configured official DeepSeek endpoint.")
    expected = read(output / "provenance.json")
    assert all(sha((TOOL / name).read_bytes()) == value for name, value in expected["product_sha256"].items())
    record_dir = output / "calls"
    record_dir.mkdir(exist_ok=True)
    phases = read(output / "phases.json")
    selected = phases if only == "all" else [p for p in phases if p["id"] == only]
    if not selected:
        raise ValueError("Unknown phase")
    save(output / "runtime.json", {"started_at": now(), "requested_model": model, "base_url": base_url,
                                  "python": sys.version, "python_executable": sys.executable})
    for phase in selected:
        result_path = output / "phase-results" / (phase["id"] + ".json")
        if result_path.exists():
            print("skip completed phase " + phase["id"], flush=True)
            continue
        prior_calls = [read(p) for p in record_dir.glob("*.json")]
        if sum((c.get("usage") or {}).get("completion_tokens", 0) for c in prior_calls) >= 100000:
            raise RuntimeError("Evaluation completion-token budget reached")
        if sum(c["status"] == "error" for c in prior_calls) >= 2:
            raise RuntimeError("Two failed logical requests: investigate before further paid calls")
        call_index = 0
        context = ContextVar("llm_evaluation_call", default=None)
        phase_records = []
        def recording_client(**kwargs):
            nonlocal call_index
            client = AsyncOpenAI(**kwargs)
            actual_create = client.chat.completions.create
            async def request_hook(request):
                rec = context.get()
                if rec is not None:
                    rec["http_attempts"] += 1
            async def response_hook(response):
                rec = context.get()
                if rec is not None:
                    rec["http_statuses"].append(response.status_code)
            client._client.event_hooks["request"].append(request_hook)
            client._client.event_hooks["response"].append(response_hook)
            async def create(**request):
                nonlocal call_index
                index = call_index
                call_index += 1
                batch = phase["samples"][index*5:(index+1)*5]
                assert request["messages"][0]["content"] == llm_node._build_prompt(batch)
                rec = {"call_id": phase["id"] + "-" + str(index), "phase": phase["id"], "kind": phase["kind"],
                       "sample_ids": [c["sample_id"] for c in batch], "started_at": now(),
                       "request": request, "http_attempts": 0, "http_statuses": []}
                token = context.set(rec)
                start = time.perf_counter()
                try:
                    response = await actual_create(**request)
                    content = response.choices[0].message.content or ""
                    rec.update({"status": "ok", "returned_model": response.model, "response_id": response.id,
                        "finish_reason": response.choices[0].finish_reason,
                        "content": content, "usage": response.usage.model_dump() if response.usage else None,
                        "format": strict_response(content, len(batch)),
                        "parsed_findings": llm_node._parse_findings(content.strip(), batch)})
                    return response
                except Exception as exc:
                    rec.update({"status": "error", "error_type": type(exc).__name__,
                                "status_code": getattr(exc, "status_code", None)})
                    raise
                finally:
                    rec["seconds"] = time.perf_counter() - start
                    rec["finished_at"] = now()
                    context.reset(token)
                    # A final check ensures a credential can never enter the artifact.
                    encoded = json.dumps(rec, ensure_ascii=False)
                    if api_key in encoded:
                        raise RuntimeError("Credential detected in output; refused to persist")
                    save(record_dir / (rec["call_id"] + ".json"), rec)
                    phase_records.append(rec)
                    print(json.dumps({"call": rec["call_id"], "status": rec["status"],
                                      "seconds": round(rec["seconds"], 3), "usage": rec.get("usage")}, ensure_ascii=False), flush=True)
            client.chat.completions.create = create
            return client
        llm_node.AsyncOpenAI = recording_client
        start = time.perf_counter()
        if phase["kind"] == "cli":
            import main as cli
            previous = {k: os.environ.get(k) for k in ["DEEPSEEK_API_KEY", "DEEPSEEK_BASE_URL", "DEEPSEEK_MODEL"]}
            os.environ.update({"DEEPSEEK_API_KEY": api_key, "DEEPSEEK_BASE_URL": base_url, "DEEPSEEK_MODEL": model})
            out = io.StringIO()
            err = io.StringIO()
            try:
                with redirect_stdout(out), redirect_stderr(err):
                    exit_code = cli.main([phase["target"], "--format", "json"])
            finally:
                for key,value in previous.items():
                    if value is None: os.environ.pop(key, None)
                    else: os.environ[key] = value
            # The observer's progress output shares stdout; obtain the final JSON object by its known opening field.
            text = out.getvalue()
            marker = '{\n  "project":'
            start_json = text.rfind(marker)
            if start_json < 0: raise RuntimeError("CLI JSON report not found")
            data = json.loads(text[start_json:])
            (output / "cli-stdout.json").write_text(text[start_json:], encoding="utf-8")
            (output / "cli-stderr.txt").write_text(err.getvalue(), encoding="utf-8")
            findings = data["llm_findings"]
            extra = {"exit_code": exit_code, "files_scanned": data["files_scanned"],
                     "rule_findings": data["rule_findings"], "process_startup_included": False}
        else:
            findings = asyncio.run(llm_node._analyze_all(api_key, base_url, model, phase["samples"], batch_size=5))
            extra = {}
        result = {"phase": phase["id"], "kind": phase["kind"], "seconds": time.perf_counter()-start,
                  "samples": len(phase["samples"]), "findings": findings,
                  "logical_calls": len(phase_records), "successful_calls": sum(r["status"]=="ok" for r in phase_records),
                  **extra}
        save(result_path, result)
        print(json.dumps({"phase_complete": phase["id"], "findings": len(findings)},ensure_ascii=False),flush=True)
        if any(r["status"]=="error" for r in phase_records):
            raise RuntimeError("Phase contained a provider error. Saved evidence; investigate before resuming.")
    llm_node.AsyncOpenAI = AsyncOpenAI
    print("Selected phases completed.", flush=True)

def main():
    parser=argparse.ArgumentParser()
    parser.add_argument("--corpus",type=Path,default=TOOL.parent/"validation-2026-10-09/corpus")
    parser.add_argument("--output",type=Path,required=True)
    parser.add_argument("--prepare",action="store_true")
    parser.add_argument("--phase",default="all")
    args=parser.parse_args()
    logging.basicConfig(level=logging.WARNING)
    if args.prepare: prepare(args.corpus.resolve(),args.output.resolve())
    else: execute(args.output.resolve(),args.phase)

if __name__=="__main__":
    main()

