"""规则引擎：对每个 .py 文件做 AST 级别的确定性检查。

设计原则：
- 装饰器注册表：每条规则独立注册，扩展新规则只需写函数 + 加 @rule 装饰器
- 发现项格式统一：{file, line, rule, rule_id, severity, desc}
- 严重级别：error（必改）/ warning（建议改）/ info（提示）
- 兼容现有测试：rule 字段保留，新增 rule_id 和 severity
"""
import ast
import logging
from pathlib import Path

from state import InspectState

logger = logging.getLogger(__name__)

# 严重级别常量
SEVERITY_ERROR = "error"
SEVERITY_WARNING = "warning"
SEVERITY_INFO = "info"


# ---- 规则注册表 ----
# 每条规则是一个 dict: {rule_id, name, severity, visit}
# visit(node, ctx) → list[dict]  返回该节点命中的发现
_RULES: list[dict] = []


def rule(rule_id: str, name: str, severity: str = SEVERITY_WARNING):
    """规则注册装饰器：标记一个函数为规则并加入注册表。

    用法：
        @rule("R001", "bare_except", SEVERITY_ERROR)
        def _check(node, ctx):
            ...
    """
    def decorator(fn):
        _RULES.append({
            "rule_id": rule_id,
            "name": name,
            "severity": severity,
            "check": fn,
        })
        return fn
    return decorator


def get_rules() -> list[dict]:
    """返回所有已注册规则（供 CLI 打印规则清单）。"""
    return list(_RULES)


# ---- 规则实现 ----

@rule("R001", "bare_except", SEVERITY_ERROR)
def _check_bare_except(node, ctx) -> list[dict]:
    """except: 不带异常类型 → 吞掉 KeyboardInterrupt 等。"""
    if node.type is None:
        return [{
            "file": ctx["file"],
            "line": getattr(node, "lineno", 0),
            "rule": "bare_except",
            "rule_id": "R001",
            "severity": SEVERITY_ERROR,
            "desc": "裸 except 会吞掉所有异常（含 KeyboardInterrupt），建议 except Exception:",
        }]
    return []


@rule("R002", "mutable_default_arg", SEVERITY_ERROR)
def _check_mutable_default(node, ctx) -> list[dict]:
    """list/dict/set 作为函数默认值 → 所有调用共享同一对象。"""
    findings = []
    for default in node.args.defaults + node.args.kw_defaults:
        if isinstance(default, (ast.List, ast.Dict, ast.Set)):
            findings.append({
                "file": ctx["file"],
                "line": getattr(node, "lineno", 0),
                "rule": "mutable_default_arg",
                "rule_id": "R002",
                "severity": SEVERITY_ERROR,
                "desc": f"函数 {node.name} 的默认参数是可变对象，调用间会共享状态",
            })
    return findings


# ---- 注册规则对应的 AST 节点类型 → 规则函数映射 ----
# 遍历一次 AST 时，每个节点类型触发对应的规则
_NODE_TYPE_RULES: dict[type, list] = {}


def _build_dispatch_table():
    """把规则注册表里的规则按它们关心的 AST 节点类型分组。

    当前规则数量少，直接在规则里硬编码关心的节点类型；
    后续规则多了可以在 @rule 里加 ast_type 参数自动生成。
    """
    # 规则1: bare_except → ExceptHandler 节点
    _NODE_TYPE_RULES.setdefault(ast.ExceptHandler, []).append(_check_bare_except)
    # 规则2: mutable_default_arg → FunctionDef/AsyncFunctionDef
    _NODE_TYPE_RULES.setdefault(ast.FunctionDef, []).append(_check_mutable_default)
    _NODE_TYPE_RULES.setdefault(ast.AsyncFunctionDef, []).append(_check_mutable_default)


_build_dispatch_table()


# ---- AST 遍历：一次遍历触发所有注册规则 ----

class RuleVisitor(ast.NodeVisitor):
    def __init__(self, file_path: str):
        self.ctx = {"file": file_path}
        self.findings: list[dict] = []

    def visit(self, node):
        # 对每个访问到的节点，查有没有规则关心它的类型
        for check_fn in _NODE_TYPE_RULES.get(type(node), []):
            self.findings.extend(check_fn(node, self.ctx))
        # 继续遍历子节点（generic_visit 就是干这个的）
        self.generic_visit(node)


# ---- 入口函数 ----

def check_file(file_path: str) -> list[dict]:
    """对单个文件跑所有规则，返回发现列表。"""
    try:
        source = Path(file_path).read_text(encoding="utf-8")
        tree = ast.parse(source, filename=file_path)
    except (SyntaxError, UnicodeDecodeError, PermissionError, OSError) as e:
        return [{
            "file": file_path,
            "line": getattr(e, "lineno", 0),
            "rule": "syntax_error",
            "rule_id": "E000",
            "severity": SEVERITY_ERROR,
            "desc": str(e)[:120],
        }]

    visitor = RuleVisitor(file_path)
    visitor.visit(tree)
    return visitor.findings


def rule_check_node(state: InspectState) -> dict:
    """LangGraph 节点：对所有 py_files 跑规则检查。"""
    findings = []
    for py_file in state.get("py_files", []):
        findings.extend(check_file(py_file))
    logger.info("检查 %d 个文件，发现 %d 处问题",
                len(state.get('py_files', [])), len(findings))
    return {"rule_findings": findings}
