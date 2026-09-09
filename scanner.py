"""扫描器节点：遍历项目目录，收集所有待巡检的 .py 文件。

设计原则：
- scanner 只负责"找到文件"，不解析内容（AST 解析留给规则节点）
- 路径无效/目录为空时不报错，返回空列表，让下游节点自然空转
"""
import logging
from pathlib import Path

from config import SKIP_DIRS
from state import InspectState

logger = logging.getLogger(__name__)


def collect_py_files(root: Path) -> list[str]:
    """递归收集 root 下所有 .py 文件的路径字符串列表。

    用 rglob 而不是 os.walk：一行完成递归匹配，代码更短更直观。
    """
    result = []
    for path in sorted(root.rglob("*.py")):
        # path.parts 形如 ('/', 'Users', 'xx', 'proj', '__pycache__', 'a.py')
        # 只要路径中任何一段命中跳过名单，就整个文件排除
        if any(part in SKIP_DIRS for part in path.parts):
            continue
        result.append(str(path))
    return result


def scan_node(state: InspectState) -> dict:
    """LangGraph 节点：读 project_path，产出 py_files。"""
    project_path = state["project_path"]
    root = Path(project_path).expanduser().resolve()

    if not root.is_dir():
        # 不抛异常：返回空列表，报告阶段会显示"扫描到 0 个文件"
        logger.warning("路径不存在或不是目录 -> %s", project_path)
        return {"py_files": []}

    py_files = collect_py_files(root)
    logger.info("项目: %s | 扫描到 %d 个 .py 文件", root.name, len(py_files))
    return {"py_files": py_files}
