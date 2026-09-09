"""读取 pyproject.toml 中 [tool.inspect-agent] 配置段。

用户可在 pyproject.toml 里自定义阈值和跳过目录，不用改源码。
找不到配置文件或字段时用内置默认值，保证开箱即用。
"""
import tomllib
from pathlib import Path

# ---- 默认值（pyproject.toml 里没配时用这些） ----
_DEFAULT_LONG_FUNC_THRESHOLD = 30
_DEFAULT_LLM_BATCH_SIZE = 5
_DEFAULT_SKIP_DIRS = {
    ".git", "__pycache__", ".venv", "venv", "env",
    "node_modules", ".idea", ".vscode", "build", "dist",
}


def _load_config() -> dict:
    """从项目根目录的 pyproject.toml 读取 [tool.inspect-agent] 段。
    找不到文件或段落时返回空 dict，由调用方用默认值兜底。
    """
    # config.py 在项目根目录，pyproject.toml 也在根目录
    toml_path = Path(__file__).parent / "pyproject.toml"
    if not toml_path.exists():
        return {}
    with open(toml_path, "rb") as f:
        data = tomllib.load(f)
    return data.get("tool", {}).get("inspect-agent", {})


_cfg = _load_config()

# 导出配置值：优先用 pyproject.toml 里的，没有就用默认值
LONG_FUNC_THRESHOLD = int(_cfg.get("long-func-threshold", _DEFAULT_LONG_FUNC_THRESHOLD))
LLM_BATCH_SIZE = int(_cfg.get("llm-batch-size", _DEFAULT_LLM_BATCH_SIZE))
SKIP_DIRS = set(_cfg.get("skip-dirs", list(_DEFAULT_SKIP_DIRS)))
