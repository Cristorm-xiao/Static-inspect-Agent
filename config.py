"""Per-project inspection settings loaded from the scanned project's pyproject.toml."""

import tomllib
from pathlib import Path

DEFAULT_SKIP_DIRS = frozenset({
    ".git", "__pycache__", ".venv", "venv", "env", "node_modules",
    ".idea", ".vscode", "build", "dist",
})
LONG_FUNC_THRESHOLD = 30
LLM_BATCH_SIZE = 5
SKIP_DIRS = DEFAULT_SKIP_DIRS  # compatibility for direct scanner calls
DEFAULTS = {
    'long_func_threshold':30, 'llm_batch_size':5, 'llm_concurrency':3,
    'llm_batch_tokens':16000, 'llm_max_requests':32, 'llm_input_budget':200000,
    'llm_output_tokens':1500, 'llm_context_chars':6000, 'cache_ttl_seconds':86400,
    'cache_enabled':True, 'ignore_package_reexports':False,
}


def load_config(project_root: Path) -> dict:
    """Return validated settings; invalid project configuration is a usage error."""
    settings = {}
    config_path = project_root / "pyproject.toml"
    if config_path.is_file():
        with config_path.open("rb") as stream:
            data = tomllib.load(stream)
        tool = data.get("tool", {})
        if not isinstance(tool, dict):
            raise ValueError("[tool] 必须是 TOML 表")
        settings = tool.get("inspect-agent", {})
    if not isinstance(settings, dict):
        raise ValueError("[tool.inspect-agent] 必须是 TOML 表")

    threshold = settings.get("long-func-threshold", LONG_FUNC_THRESHOLD)
    batch_size = settings.get("llm-batch-size", LLM_BATCH_SIZE)
    skip_dirs = settings.get("skip-dirs", sorted(DEFAULT_SKIP_DIRS))
    if type(threshold) is not int or threshold < 1:
        raise ValueError("long-func-threshold 必须是正整数")
    if type(batch_size) is not int or batch_size < 1:
        raise ValueError("llm-batch-size 必须是正整数")
    if not isinstance(skip_dirs, list) or any(not isinstance(name, str) or not name for name in skip_dirs):
        raise ValueError("skip-dirs 必须是非空字符串数组")
    result = {
        **DEFAULTS,
        "long_func_threshold": threshold,
        "llm_batch_size": batch_size,
        "skip_dirs": sorted(set(skip_dirs)),
    }
    for key, default in DEFAULTS.items():
        value=settings.get(key.replace('_','-'),default)
        if type(default) is bool:
            if type(value) is not bool: raise ValueError(f'{key} 必须是布尔值')
        elif type(value) is not int or value<1:
            raise ValueError(f'{key} 必须是正整数')
        result[key]=value
    return result
