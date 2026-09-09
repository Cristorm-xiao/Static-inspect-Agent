"""报告渲染节点：把规则发现 + LLM 发现汇总成结构化 Markdown。

设计原则：
- 纯函数：输入 state，输出 report_md 字符串，无副作用
- 分区清晰：摘要 → 规则发现 → LLM 发现，面试官一眼能看懂架构
- 按文件分组：同一个文件的问题聚一起，定位方便
"""
import logging
from collections import defaultdict

from state import InspectState

logger = logging.getLogger(__name__)


def _by_file(findings: list[dict], key: str) -> dict[str, list[dict]]:
    """把发现按文件路径分组。"""
    grouped: dict[str, list[dict]] = defaultdict(list)
    for f in findings:
        grouped[f.get("file", "未知文件")].append(f)
    return grouped


def _render_rules(findings: list[dict]) -> str:
    """渲染规则引擎发现区。"""
    if not findings:
        return "## 规则引擎发现\n\n无问题。\n"

    sections = ["## 规则引擎发现（AST 确定性检查）\n"]
    for file, items in _by_file(findings, "file").items():
        sections.append(f"### `{file}`\n")
        sections.append("| 行号 | 级别 | 规则 | 说明 |")
        sections.append("|------|------|------|------|")
        for item in sorted(items, key=lambda x: x.get("line", 0)):
            sev = item.get("severity", "-")
            sections.append(
                f"| {item['line']} | {sev} | {item['rule']} | {item['desc']} |"
            )
        sections.append("")  # 空行分隔
    return "\n".join(sections)


def _render_llm(findings: list[dict]) -> str:
    """渲染 LLM 语义分析发现区。"""
    if not findings:
        return "## LLM 语义分析\n\n无发现（或未启用 LLM 分析）。\n"

    sections = ["## LLM 语义分析（代码坏味道解读）\n"]
    for file, items in _by_file(findings, "file").items():
        sections.append(f"### `{file}`\n")
        for item in sorted(items, key=lambda x: x.get("line", 0)):
            sections.append(
                f"- **行 {item['line']}** — {item['smell']}：{item['reason']}"
            )
        sections.append("")
    return "\n".join(sections)


def report_node(state: InspectState) -> dict:
    """LangGraph 节点：汇总所有发现，渲染 Markdown 报告。"""
    rules = state.get("rule_findings", [])
    llm = state.get("llm_findings", [])
    py_files = state.get("py_files", [])

    # 摘要
    md = f"# 工程静态巡检报告\n\n"
    md += f"## 摘要\n\n"
    md += f"- 扫描文件：{len(py_files)} 个\n"
    md += f"- 规则引擎发现：{len(rules)} 处\n"
    md += f"- LLM 语义发现：{len(llm)} 处\n\n"
    md += "---\n\n"

    # 两个发现区
    md += _render_rules(rules) + "\n"
    md += _render_llm(llm)

    logger.info("报告生成完毕，共 %d 处发现", len(rules) + len(llm))
    return {"report_md": md}
