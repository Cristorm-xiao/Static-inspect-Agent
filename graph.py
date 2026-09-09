"""构建巡检流程图：scan → rule_check → llm_analyze → report"""
from langgraph.graph import StateGraph, START, END

from scanner import scan_node
from rules import rule_check_node
from llm_node import llm_analyze_node
from report import report_node
from state import InspectState


def build_graph():
    g = StateGraph(InspectState)
    g.add_node("scan", scan_node)
    g.add_node("rule_check", rule_check_node)
    g.add_node("llm_analyze", llm_analyze_node)
    g.add_node("report", report_node)
    # 线性流转
    g.add_edge(START, "scan")
    g.add_edge("scan", "rule_check")
    g.add_edge("rule_check", "llm_analyze")
    g.add_edge("llm_analyze", "report")
    g.add_edge("report", END)
    return g.compile()
