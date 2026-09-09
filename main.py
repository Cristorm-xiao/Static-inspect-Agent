"""工程静态巡检 Agent — 命令行入口

用法：
    python main.py <项目路径> [选项]

示例：
    python main.py .
    python main.py ./my-proj --output report.md
    python main.py . --no-llm --format json
"""
import argparse
import json
import logging
import sys

from graph import build_graph


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="inspect-agent",
        description="Python 工程静态巡检：规则引擎 + LLM 语义分析",
    )
    p.add_argument("project_path", help="待巡检的项目根目录")
    p.add_argument("--output", "-o", metavar="FILE",
                   help="将报告写入文件（默认只打印到控制台）")
    p.add_argument("--no-llm", action="store_true",
                   help="禁用 LLM 语义分析，只跑规则引擎")
    p.add_argument("--format", choices=["markdown", "json"], default="markdown",
                   help="输出格式：markdown（默认）或 json")
    p.add_argument("--verbose", "-v", action="store_true",
                   help="显示详细进度日志（默认只显示警告和报告）")
    return p


def main():
    args = build_parser().parse_args()

    # 初始化日志：--verbose 显示 INFO 级别进度，否则只显示 WARNING+
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.WARNING,
        format="%(name)s: %(message)s",
    )

    app = build_graph()
    result = app.invoke({
        "project_path": args.project_path,
        "enable_llm": not args.no_llm,
    })

    # 根据格式输出
    if args.format == "json":
        output = json.dumps({
            "project": args.project_path,
            "files_scanned": len(result.get("py_files", [])),
            "rule_findings": result.get("rule_findings", []),
            "llm_findings": result.get("llm_findings", []),
        }, ensure_ascii=False, indent=2)
    else:
        output = result["report_md"]

    # 控制台
    print("\n" + "=" * 60)
    print(output)
    print("=" * 60)

    # 写文件
    if args.output:
        from pathlib import Path
        Path(args.output).write_text(output, encoding="utf-8")
        print(f"\n报告已写入 {args.output}")

    # 退出码：发现问题时返回 1，让 CI 能据此判断成败
    total = len(result.get("rule_findings", [])) + len(result.get("llm_findings", []))
    if total > 0:
        sys.exit(1)


if __name__ == "__main__":
    main()
