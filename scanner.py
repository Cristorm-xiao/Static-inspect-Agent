"""扫描器节点：遍历项目目录，收集所有待巡检的 .py 文件。

设计原则：
- scanner 只负责"找到文件"，不解析内容（AST 解析留给规则节点）
- 路径无效/目录为空时不报错，返回空列表，让下游节点自然空转
"""
import logging
import os
from pathlib import Path

from config import SKIP_DIRS
from state import InspectState

logger = logging.getLogger(__name__)


def collect_py_files(root: Path, skip_dirs=None) -> list[str]:
    """递归收集 root 下所有 .py 文件的路径字符串列表。

    在进入忽略目录之前剪枝，不跟随符号链接，保证稳定顺序。
    """
    result = []
    skip_dirs = set(SKIP_DIRS if skip_dirs is None else skip_dirs)
    skip_dirs.add('.inspect-cache')
    def onerror(error):
        raise error
    for folder, dirs, files in os.walk(root, topdown=True, followlinks=False, onerror=onerror):
        dirs[:] = sorted(d for d in dirs if d not in skip_dirs and not (Path(folder)/d).is_symlink())
        for name in sorted(files):
            path=Path(folder)/name
            if name.endswith('.py') and not path.is_symlink(): result.append(str(path))
    return sorted(result)


def scan_node(state: InspectState) -> dict:
    """LangGraph 节点：读 project_path，产出 py_files。"""
    project_path = state["project_path"]
    root = Path(project_path).expanduser().resolve()

    if not root.is_dir():
        # 不抛异常：返回空列表，报告阶段会显示"扫描到 0 个文件"
        logger.warning("路径不存在或不是目录 -> %s", project_path)
        return {"py_files": []}

    py_files = collect_py_files(root, state.get("config", {}).get("skip_dirs"))
    logger.info("项目: %s | 扫描到 %d 个 .py 文件", root.name, len(py_files))
    return {"py_files": py_files}
