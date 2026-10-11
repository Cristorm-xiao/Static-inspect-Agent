"""规则引擎的第一组单元测试。

运行方式（在项目根目录，用 ESI 环境）：
    ~/miniconda3/envs/ESI/bin/python -m pytest test_rules.py -v

测试思路：
- 不手工构造 AST 节点，而是把"故意写坏的代码"写成临时 .py 文件，
  调用 rules.check_file() 走完整真实路径（读文件 → ast.parse → visitor）
- tmp_path 是 pytest 内置 fixture：每个测试拿到独立临时目录，用完自动清理
- 每个测试遵循 AAA 结构：Arrange（造文件）→ Act（调函数）→ Assert（断言）
"""
from rules import check_file


def test_bare_except_detected(tmp_path):
    # Arrange：写一个含裸 except 的文件
    bad = tmp_path / "bad.py"
    bad.write_text(
        "try:\n"
        "    x = 1\n"
        "except:\n"      # 第 3 行：裸 except，应命中规则1
        "    pass\n",
        encoding="utf-8",
    )

    # Act
    findings = check_file(str(bad))

    # Assert：恰好 1 个发现，且规则名、行号都对
    assert len(findings) == 1
    assert findings[0]["rule"] == "bare_except"
    assert findings[0]["line"] == 3


def test_correct_except_not_flagged(tmp_path):
    # 反向测试：except Exception 是正确写法，绝不能误报
    good = tmp_path / "good.py"
    good.write_text(
        "try:\n"
        "    x = 1\n"
        "except Exception:\n"
        "    pass\n",
        encoding="utf-8",
    )

    assert check_file(str(good)) == []


def test_mutable_default_arg_detected(tmp_path):
    # Arrange：list 字面量作为函数默认值，应命中规则2
    bad = tmp_path / "bad.py"
    bad.write_text(
        "def add_item(item, bucket=[]):\n"
        "    bucket.append(item)\n"
        "    return bucket\n",
        encoding="utf-8",
    )

    # Act
    findings = check_file(str(bad))

    # Assert
    assert len(findings) == 1
    assert findings[0]["rule"] == "mutable_default_arg"

def test_syntax_error(tmp_path):
    #写一个含语法错误的文件
    bad = tmp_path / "bad.py"
    bad.write_text(
        "def add_item(item, bucket=[])\n"
        "    bucket.append(item)\n"
        "    return bucket\n",
        encoding="utf-8",
    )
    
    # Act
    findings = check_file(str(bad))
    
    # Assert
    assert len(findings) == 1
    assert findings[0]["rule"] == "syntax_error"
    assert findings[0]["line"] == 1


def test_nonexistent_file_does_not_crash(tmp_path):
    # 文件不存在 → FileNotFoundError（OSError 子类）→ 应被兜底捕获，不崩溃
    ghost = str(tmp_path / "ghost.py")
    findings = check_file(ghost)

    assert len(findings) == 1
    assert findings[0]["rule"] == "syntax_error"


def test_unreadable_file_does_not_crash(tmp_path, monkeypatch):
    # Windows 的 chmod(0) 不保证拒绝读取，直接模拟读取失败。
    locked = tmp_path / "locked.py"
    locked.write_text("x = 1\n", encoding="utf-8")
    original = __import__("pathlib").Path.read_bytes

    def fail_read(path, *args, **kwargs):
        if path == locked:
            raise PermissionError("access denied")
        return original(path, *args, **kwargs)

    monkeypatch.setattr(__import__("pathlib").Path, "read_bytes", fail_read)

    findings = check_file(str(locked))

    assert len(findings) == 1
    assert findings[0]["rule"] == "syntax_error"


def test_eval_and_wildcard_import_are_reported(tmp_path):
    source = tmp_path / "unsafe.py"
    source.write_text("from math import *\nvalue = eval('1 + 2')\n", encoding="utf-8")
    findings = check_file(str(source))
    assert [(item["rule_id"], item["line"]) for item in findings] == [
        ("R004", 1), ("R003", 2),
    ]


def test_method_named_eval_is_not_reported(tmp_path):
    source = tmp_path / "safe.py"
    source.write_text("result = evaluator.eval(expression)\n", encoding="utf-8")
    assert check_file(str(source)) == []
