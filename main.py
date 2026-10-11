"""Command line interface for the static inspection graph."""

import argparse
import io
import json
import logging
import sys
import tomllib
from pathlib import Path

from baseline import filter_baseline, load_baseline, write_baseline
from config import load_config
from graph import build_graph
from report import report_node
from sarif import to_sarif


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="inspect-agent", description="Python 工程静态巡检")
    parser.add_argument("project_path", help="待巡检项目根目录")
    parser.add_argument("--output", "-o", metavar="FILE", help="将报告写入文件")
    parser.add_argument("--no-llm", action="store_true", help="只运行离线规则引擎")
    parser.add_argument("--format", choices=["markdown", "json", "sarif"], default="markdown")
    baseline_group = parser.add_mutually_exclusive_group()
    baseline_group.add_argument("--baseline", metavar="FILE", help="只报告基线之后新增的规则问题")
    baseline_group.add_argument("--write-baseline", metavar="FILE", help="保存当前规则发现作为基线")
    parser.add_argument("--verbose", "-v", action="store_true")
    parser.add_argument('--no-cache', action='store_true', help='不读写分析缓存')
    parser.add_argument('--refresh-cache', action='store_true', help='忽略旧缓存并保存新结果')
    parser.add_argument('--cache-dir', help='缓存目录，默认项目内 .inspect-cache')
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.WARNING,
                        format="%(name)s: %(message)s")
    root = Path(args.project_path).expanduser().resolve()
    if not root.is_dir():
        print(f"项目路径不存在或不是目录：{args.project_path}", file=sys.stderr)
        return 2
    try:
        config = load_config(root)
        if args.no_cache: config['cache_enabled']=False
        config['refresh_cache']=args.refresh_cache
        if args.cache_dir: config['cache_dir']=str(Path(args.cache_dir).expanduser().resolve())
    except (OSError, tomllib.TOMLDecodeError, ValueError) as exc:
        print(f"项目配置无效：{exc}", file=sys.stderr)
        return 2
    known = None
    if args.baseline:
        try:
            known = load_baseline(Path(args.baseline))
        except ValueError as exc:
            print(str(exc), file=sys.stderr)
            return 2

    result = build_graph().invoke({
        "project_path": str(root),
        "enable_llm": not (args.no_llm or args.write_baseline or args.format == "sarif"),
        "config": config,
    })
    rules = result.get("rule_findings", [])
    suppressed = 0
    if args.write_baseline:
        try:
            write_baseline(Path(args.write_baseline), rules)
        except OSError as exc:
            print(f"无法写入 baseline：{exc}", file=sys.stderr)
            return 2
        return 0
    if known is not None:
        rules, suppressed = filter_baseline(rules, known)
        result["rule_findings"] = rules
        result["report_md"] = report_node({**result, "baseline_suppressed": suppressed})["report_md"]

    if args.format == "json":
        output = json.dumps({
            "project": str(root),
            "files_scanned": len(result.get("py_files", [])),
            "rule_findings": result.get("rule_findings", []),
            "llm_findings": result.get("llm_findings", []),
            "baseline_suppressed": suppressed,
            'scan_stats':result.get('scan_stats',{}),
            'llm_stats':result.get('llm_stats',{}),
            'llm_outcomes':result.get('llm_outcomes',[]),
        }, ensure_ascii=False, indent=2)
    elif args.format == "sarif":
        output = json.dumps(to_sarif(rules), ensure_ascii=False, indent=2)
    else:
        output = result["report_md"]

    if args.output:
        try:
            Path(args.output).write_text(output + "\n", encoding="utf-8")
        except OSError as exc:
            print(f"无法写入报告：{exc}", file=sys.stderr)
            return 2
    else:
        if args.format in {"json", "sarif"} and isinstance(sys.stdout, io.TextIOWrapper):
            sys.stdout.reconfigure(encoding="utf-8")
        print(output)
    return int(bool(rules))


if __name__ == "__main__":
    sys.exit(main())
