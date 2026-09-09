from typing import TypedDict

class InspectState(TypedDict):
    """整个 Agent 的共享状态：所有节点读写同一份数据"""
    project_path: str          # 输入：待巡检的项目根目录
    enable_llm: bool            # 输入：是否启用 LLM 语义分析（--no-llm 时为 False）
    py_files: list[str]        # 扫描阶段产出：所有 .py 文件绝对路径
    rule_findings: list[dict]  # 规则引擎发现：[{file, line, rule, desc}]
    llm_findings: list[dict]   # LLM 解读发现：[{file, line, smell, reason}]
    report_md: str             # 最终产出：Markdown 报告文本
