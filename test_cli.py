"""CLI 的端到端行为：真实图执行，只禁用外部 LLM。"""

import json
import subprocess
import sys

from main import main


def test_invalid_project_is_usage_error(tmp_path, capsys):
    code = main([str(tmp_path / "missing"), "--no-llm", "--format", "json"])
    captured = capsys.readouterr()
    assert code == 2
    assert captured.out == ""
    assert "不存在" in captured.err


def test_json_stdout_is_parseable_and_paths_are_relative(tmp_path, capsys):
    (tmp_path / "bad.py").write_text("try:\n    pass\nexcept:\n    pass\n", encoding="utf-8")
    code = main([str(tmp_path), "--no-llm", "--format", "json"])
    data = json.loads(capsys.readouterr().out)
    assert code == 1
    assert data["files_scanned"] == 1
    assert data["rule_findings"][0]["file"] == "bad.py"
    assert data["rule_findings"][0]["rule_id"] == "R001"


def test_target_project_skip_dirs_are_used(tmp_path, capsys):
    (tmp_path / "pyproject.toml").write_text(
        '[tool.inspect-agent]\nskip-dirs = ["generated"]\n', encoding="utf-8"
    )
    generated = tmp_path / "generated"
    generated.mkdir()
    (generated / "bad.py").write_text("try:\n pass\nexcept:\n pass\n", encoding="utf-8")
    (tmp_path / "good.py").write_text("x = 1\n", encoding="utf-8")
    code = main([str(tmp_path), "--no-llm", "--format", "json"])
    data = json.loads(capsys.readouterr().out)
    assert code == 0
    assert data["files_scanned"] == 1


def test_baseline_only_reports_new_deterministic_findings(tmp_path, capsys):
    source = tmp_path / "bad.py"
    source.write_text("try:\n pass\nexcept:\n pass\n", encoding="utf-8")
    baseline = tmp_path / "baseline.json"
    assert main([str(tmp_path), "--no-llm", "--write-baseline", str(baseline)]) == 0
    snapshot = json.loads(baseline.read_text(encoding="utf-8"))
    assert snapshot["version"] == 2
    assert len(snapshot["findings"]) == 1
    capsys.readouterr()

    assert main([str(tmp_path), "--no-llm", "--baseline", str(baseline), "--format", "json"]) == 0
    clean = json.loads(capsys.readouterr().out)
    assert clean["rule_findings"] == []
    assert clean["baseline_suppressed"] == 1

    source.write_text(source.read_text(encoding="utf-8") + "def f(x=[]):\n return x\n", encoding="utf-8")
    assert main([str(tmp_path), "--no-llm", "--baseline", str(baseline), "--format", "json"]) == 1
    updated = json.loads(capsys.readouterr().out)
    assert [item["rule_id"] for item in updated["rule_findings"]] == ["R002"]


def test_sarif_contains_rule_and_source_location(tmp_path, capsys):
    (tmp_path / "bad.py").write_text("try:\n pass\nexcept:\n pass\n", encoding="utf-8")
    assert main([str(tmp_path), "--no-llm", "--format", "sarif"]) == 1
    data = json.loads(capsys.readouterr().out)
    assert data["version"] == "2.1.0"
    run = data["runs"][0]
    assert run["tool"]["driver"]["rules"][0]["id"] == "R001"
    result = run["results"][0]
    assert result["ruleId"] == "R001"
    assert result["locations"][0]["physicalLocation"]["artifactLocation"]["uri"] == "bad.py"
    assert result["locations"][0]["physicalLocation"]["region"]["startLine"] == 3


def test_invalid_baseline_returns_usage_error(tmp_path, capsys, monkeypatch):
    baseline = tmp_path / "bad.json"
    baseline.write_text("{}", encoding="utf-8")
    monkeypatch.setattr("main.build_graph", lambda: (_ for _ in ()).throw(AssertionError("graph should not run")))
    assert main([str(tmp_path), "--no-llm", "--baseline", str(baseline)]) == 2
    assert "baseline" in capsys.readouterr().err.lower()


def test_sarif_does_not_start_llm_even_when_configured(tmp_path, capsys, monkeypatch):
    (tmp_path / "long.py").write_text("def f():\n" + "    pass\n" * 35, encoding="utf-8")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-fake-key")
    monkeypatch.setattr("llm_node.AsyncOpenAI", lambda **kwargs: (_ for _ in ()).throw(AssertionError("LLM called")))
    assert main([str(tmp_path), "--format", "sarif"]) == 0
    assert json.loads(capsys.readouterr().out)["runs"][0]["results"] == []


def test_baseline_preserves_new_duplicate_rule_on_same_line(tmp_path, capsys):
    source = tmp_path / "bad.py"
    source.write_text("def f(a=[]):\n return a\n", encoding="utf-8")
    baseline = tmp_path / "baseline.json"
    assert main([str(tmp_path), "--no-llm", "--write-baseline", str(baseline)]) == 0
    source.write_text("def f(a=[], b={}):\n return a\n", encoding="utf-8")
    assert main([str(tmp_path), "--no-llm", "--baseline", str(baseline), "--format", "json"]) == 1
    data = json.loads(capsys.readouterr().out)
    assert len(data["rule_findings"]) == 1
    assert data["rule_findings"][0]["rule_id"] == "R002"


def test_invalid_tool_table_returns_usage_error(tmp_path, capsys):
    (tmp_path / "pyproject.toml").write_text('tool = "oops"\n', encoding="utf-8")
    assert main([str(tmp_path), "--no-llm"]) == 2
    assert "配置无效" in capsys.readouterr().err


def test_json_subprocess_stdout_is_utf8(tmp_path):
    (tmp_path / "bad.py").write_text("try:\n pass\nexcept:\n pass\n", encoding="utf-8")
    result = subprocess.run(
        [sys.executable, "-m", "main", str(tmp_path), "--no-llm", "--format", "json"],
        cwd=__import__("pathlib").Path(__file__).parent,
        capture_output=True,
    )
    assert result.returncode == 1
    data = json.loads(result.stdout.decode("utf-8"))
    assert "裸 except" in data["rule_findings"][0]["desc"]
