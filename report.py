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
            if item.get('evidence'):
                sections.append('\n    证据：\n\n' + '\n'.join('    '+line for line in item['evidence'].splitlines()))
            if item.get('suggestion'): sections.append('\n    建议：'+item['suggestion'])
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
    scan=state.get('scan_stats',{})
    if scan: md += f"- 文件解析：{scan.get('parsed_files',0)}；源码缓存命中：{scan.get('cache_hits',0)}\n"
    stats=state.get('llm_stats',{})
    if stats:
        md += f"- LLM 状态：{stats.get('status')}；完成函数：{stats.get('completed_functions',0)}/{stats.get('candidates',0)}\n"
        md += f"- 本次模型逻辑调用（不含 SDK 重试次数）：{stats.get('requests',0)}；LLM 缓存命中函数：{stats.get('cache_hits',0)}\n"
        md += f"- 实际 token：输入 {stats.get('prompt_tokens',0)} / 输出 {stats.get('completion_tokens',0)}；用量缺失调用：{stats.get('usage_missing_calls',0)}\n\n"
        incomplete=[o for o in state.get('llm_outcomes',[]) if o['status'] not in ('completed','cache_hit')]
        if incomplete:
            md += '未完成的候选（不等于无问题）：\n\n'
            md += '\n'.join(f"- `{o['function_id']}`：{o['status']}" for o in incomplete)+'\n\n'
    if state.get("baseline_suppressed") is not None:
        md += f"- 基线已忽略：{state['baseline_suppressed']} 处规则发现\n\n"
    md += "---\n\n"

    # 两个发现区
    md += _render_rules(rules) + "\n"
    md += _render_llm(llm) if llm else '## LLM 语义分析\n\n本次没有已通过证据校验的建议；分析是否完成请查看上方状态。\n'

    logger.info("报告生成完毕，共 %d 处发现", len(rules) + len(llm))
    return {"report_md": md}
