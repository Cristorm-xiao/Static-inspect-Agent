"""LLM 语义分析节点：把"可疑片段"交给 DeepSeek 解读代码坏味道。

设计原则：
- 不全量喂源码：只挑"长函数"候选（>30行），省 token、聚焦真正需要语义判断的代码
- 分批并发：候选函数多时分批，用 asyncio.gather 并发请求，防止超上下文 + 加速
- 降级友好：没配 API key 或调用失败就跳过，不阻塞整条巡检流水线
- 配置外置：密钥/模型写在 .env 文件，阈值/批量写在 pyproject.toml
"""
import ast
import asyncio
import json
import logging
import os
from pathlib import Path

from dotenv import load_dotenv
from openai import AsyncOpenAI, APIError

from config import LONG_FUNC_THRESHOLD, LLM_BATCH_SIZE
from state import InspectState

logger = logging.getLogger(__name__)


def extract_candidates(file_path: str) -> list[dict]:
    """从文件中提取"可疑函数"片段：函数体超过阈值行数的。

    返回 [{name, line, end_line, source}]，source 是该函数的源码文本。
    """
    try:
        source = Path(file_path).read_text(encoding="utf-8")
        tree = ast.parse(source, filename=file_path)
    except (SyntaxError, UnicodeDecodeError):
        return []

    lines = source.splitlines()
    candidates = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        func_lines = node.end_lineno - node.lineno + 1
        if func_lines >= LONG_FUNC_THRESHOLD:
            snippet = "\n".join(lines[node.lineno - 1: node.end_lineno])
            candidates.append({
                "name": node.name,
                "line": node.lineno,
                "end_line": node.end_lineno,
                "source": snippet,
            })
    return candidates


def _build_prompt(batch: list[dict]) -> str:
    """为一个批次构造 prompt。batch 里的每个元素有 file/line/source。"""
    snippets = ""
    for i, cand in enumerate(batch):
        snippets += (f"\n### 片段{i} (文件: {cand['file']}, 行: {cand['line']})"
                     f"\n```python\n{cand['source']}\n```\n")
    return f"""你是资深 Python 代码审查员。以下是 {len(batch)} 个较长的函数，请找出代码坏味道
（如命名含糊、职责过多、嵌套过深、重复逻辑、魔法数字等）。

{snippets}

只返回 JSON 数组，每个元素格式：{{"index": 片段编号, "smell": "坏味道名", "reason": "一句话说明"}}
无问题就返回 []。"""


def _strip_fence(raw: str) -> str:
    """模型常把 JSON 包在 ```json ... ``` 围栏里，剥掉它。"""
    if raw.startswith("```"):
        lines = raw.splitlines()
        lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        raw = "\n".join(lines).strip()
    return raw


def _parse_findings(raw: str, batch: list[dict]) -> list[dict]:
    """解析 LLM 返回的 JSON，把 index 映射回 batch 里的候选函数。"""
    raw = _strip_fence(raw)
    findings = []
    try:
        items = json.loads(raw)
        if not isinstance(items, list):
            logger.warning("LLM 返回非数组（%s），跳过解析", type(items).__name__)
            items = []
        for item in items:
            if not isinstance(item, dict):
                continue
            idx = item.get("index", 0)
            if not isinstance(idx, int):
                idx = int(idx)
            if 0 <= idx < len(batch):
                cand = batch[idx]
                findings.append({
                    "file": cand["file"],
                    "line": cand["line"],
                    "smell": item.get("smell", "未知"),
                    "reason": item.get("reason", ""),
                })
    except (json.JSONDecodeError, TypeError, ValueError) as e:
        logger.warning("LLM 返回解析失败（%s），跳过", type(e).__name__)
    return findings


async def _analyze_batch(client: AsyncOpenAI, model: str,
                         batch: list[dict], batch_idx: int) -> list[dict]:
    """分析一批候选函数：构造 prompt → 调 async API → 解析返回。
    单批失败不致命——返回空列表，其他批次的结果不受影响。
    """
    prompt = _build_prompt(batch)
    try:
        resp = await client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.3,
        )
        raw = (resp.choices[0].message.content or "").strip()
        return _parse_findings(raw, batch)
    except APIError as e:
        logger.warning("批次 %d LLM 调用失败（%s），跳过", batch_idx, type(e).__name__)
        return []


async def _analyze_all(api_key: str, base_url: str,
                       model: str, all_candidates: list[dict]) -> list[dict]:
    """分批 + 并发：把候选函数切成小批，所有批次同时发请求。

    用 async with 管理 AsyncOpenAI 生命周期：退出作用域时自动关闭
    HTTP 连接池，防止连接泄漏。
    """
    findings = []
    async with AsyncOpenAI(api_key=api_key, base_url=base_url,
                           timeout=30.0, max_retries=2) as client:
        batches = [all_candidates[i:i + LLM_BATCH_SIZE]
                   for i in range(0, len(all_candidates), LLM_BATCH_SIZE)]
        logger.info("分为 %d 批（每批 ≤%d 个），并发调用 LLM",
                    len(batches), LLM_BATCH_SIZE)

        # asyncio.gather：所有批次同时发起，等全部完成后一起拿结果
        tasks = [_analyze_batch(client, model, batch, i)
                 for i, batch in enumerate(batches)]
        results = await asyncio.gather(*tasks)

        for batch_findings in results:
            findings.extend(batch_findings)
    return findings


def llm_analyze_node(state: InspectState) -> dict:
    """LangGraph 节点：收集可疑片段 → 分批并发调用 LLM → 合并发现。"""
    if not state.get("enable_llm", True):
        logger.info("LLM 分析已禁用（--no-llm），跳过")
        return {"llm_findings": []}

    load_dotenv()
    api_key = os.environ.get("DEEPSEEK_API_KEY", "")
    base_url = os.environ.get("DEEPSEEK_BASE_URL", "https://api.deepseek.com/v1")
    model = os.environ.get("DEEPSEEK_MODEL", "deepseek-chat")

    if not api_key or "在此填入" in api_key:
        logger.warning("未配置 DEEPSEEK_API_KEY（请在 .env 中填写），跳过 LLM 分析")
        return {"llm_findings": []}

    all_candidates = []
    for py_file in state.get("py_files", []):
        for cand in extract_candidates(py_file):
            cand["file"] = py_file
            all_candidates.append(cand)

    if not all_candidates:
        logger.info("无可疑片段（没有超过 %d 行的函数），跳过", LONG_FUNC_THRESHOLD)
        return {"llm_findings": []}

    logger.info("发现 %d 个可疑函数，分批交给 LLM 解读", len(all_candidates))

    # asyncio.run：从同步代码启动异步流程（同步节点调异步函数的桥梁）
    findings = asyncio.run(
        _analyze_all(api_key, base_url, model, all_candidates)
    )

    logger.info("LLM 发现 %d 处坏味道", len(findings))
    return {"llm_findings": findings}
