"""LLM 节点容错测试：用"假客户端"模拟 API 失败，验证节点降级而不崩溃。

运行：
    ~/miniconda3/envs/ESI/bin/python -m pytest test_llm_node.py -v

核心思想（mock / 替身）：
- 测试不能真的调 DeepSeek API（花钱、靠网络、结果不可控）
- 造一个假客户端，它的 create() 是 async 方法（因为被测代码用 await 调）
- monkeypatch 替换的是 openai.AsyncOpenAI（不是 OpenAI）
"""
import openai

import llm_node
from llm_node import llm_analyze_node


# --- 假客户端（async 版）：create() 抛超时 ---

class _FakeTimeoutCompletions:
    async def create(self, **kwargs):
        raise openai.APITimeoutError(request=None)


class _FakeTimeoutChat:
    completions = _FakeTimeoutCompletions()


class _FakeTimeoutClient:
    chat = _FakeTimeoutChat()
    def __init__(self, **kwargs):
        pass
    async def __aenter__(self):
        return self
    async def __aexit__(self, *args):
        pass


def _make_fake_client(raw_content):
    """工厂函数：创建假客户端，create() 返回 content = raw_content。"""

    class _FakeMessage:
        content = raw_content

    class _FakeChoice:
        message = _FakeMessage()

    class _FakeResponse:
        choices = [_FakeChoice()]

    class _FakeCompletions:
        async def create(self, **kwargs):
            if raw_content == 'VALID':
                import json
                from types import SimpleNamespace
                c=json.loads(kwargs['messages'][-1]['content'])['functions'][0]
                text=json.dumps({'findings':[{'function_id':c['function_id'],'category':'函数过长',
                    'evidence_lines':[2,2],'evidence':c['source'].splitlines()[1].split(' | ',1)[1],
                    'impact':'重复赋值影响维护','suggestion':'审查冗余赋值','needs_context':False}], 'context_requests':[]})
                return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=text),finish_reason='stop')])
            return _FakeResponse()

    class _FakeChat:
        completions = _FakeCompletions()

    class _FakeClient:
        chat = _FakeChat()
        def __init__(self, **kwargs):
            pass
        async def __aenter__(self):
            return self
        async def __aexit__(self, *args):
            pass

    return _FakeClient


def _setup(monkeypatch, tmp_path, raw_content):
    """共用 Arrange：设假 key + 换假 AsyncOpenAI + 造长函数文件。"""
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-fake-key-for-test")
    monkeypatch.setattr(llm_node, "AsyncOpenAI", _make_fake_client(raw_content))
    return _make_long_func_file(tmp_path)


def _make_long_func_file(tmp_path):
    """造一个含 35 行长函数的文件（>30 行阈值），让流程走到 LLM 调用。"""
    f = tmp_path / "long.py"
    body = "\n".join(f"    x = {i}" for i in range(35))
    f.write_text(f"def long_func():\n{body}\n", encoding="utf-8")
    return str(f)


def test_llm_timeout_degrades_gracefully(tmp_path, monkeypatch):
    # API 超时 → 降级为空发现，不崩溃
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-fake-key-for-test")
    monkeypatch.setattr(llm_node, "AsyncOpenAI", _FakeTimeoutClient)
    py_file = _make_long_func_file(tmp_path)

    result = llm_analyze_node(
        {"project_path": str(tmp_path), "py_files": [py_file]}
    )
    assert result['llm_findings'] == []
    assert result['llm_outcomes'][0]['status'] == 'failed'


def test_llm_returns_dict_not_list(tmp_path, monkeypatch):
    # 模型返回 dict 而非 list → 跳过解析，不崩溃
    py_file = _setup(monkeypatch, tmp_path, '{"result": []}')

    result = llm_analyze_node(
        {"project_path": str(tmp_path), "py_files": [py_file]}
    )
    assert result['llm_findings'] == []
    assert result['llm_outcomes'][0]['status'] == 'invalid_response'


def test_llm_returns_valid_findings(tmp_path, monkeypatch):
    # 正常路径：模型返回合法 JSON 数组，应正确提取发现
    py_file = _setup(monkeypatch, tmp_path,
                     'VALID')

    result = llm_analyze_node(
        {"project_path": str(tmp_path), "py_files": [py_file]}
    )
    assert len(result["llm_findings"]) == 1
    assert result["llm_findings"][0]["smell"] == "函数过长"


def test_llm_batches_multiple_functions(tmp_path, monkeypatch):
    # 3 个长函数 → LLM_BATCH_SIZE=2 → 分 2 批（2+1）→ 并发 → 合并
    import llm_node
    monkeypatch.setattr(llm_node, "LLM_BATCH_SIZE", 2)

    # 造 3 个含长函数的文件
    py_files = []
    for i in range(3):
        f = tmp_path / f"long_{i}.py"
        body = "\n".join(f"    x = {j}" for j in range(35))
        f.write_text(f"def func_{i}():\n{body}\n", encoding="utf-8")
        py_files.append(str(f))

    # 假客户端：每批都返回 index 0 的发现
    # 批 0 有 2 个候选 → index 0 映射到 func_0
    # 批 1 有 1 个候选 → index 0 映射到 func_2
    # 预期：2 个发现（每批各 1 个）
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-fake-key-for-test")
    monkeypatch.setattr(llm_node, "AsyncOpenAI",
                        _make_fake_client('VALID'))

    result = llm_analyze_node(
        {"project_path": str(tmp_path), "py_files": py_files}
    )
    assert len(result["llm_findings"]) == 2


def test_bad_llm_item_does_not_discard_later_valid_item():
    import json
    batch = [{'function_id':'f','file':'demo.py','line':1,'source':'def f():\n    pass'}]
    raw = json.dumps({'findings':[{'function_id':'oops'}, {'function_id':'f','category':'example',
        'evidence_lines':[2,2],'evidence':'pass','impact':'example','suggestion':'review','needs_context':False}], 'context_requests':[]})
    parsed = llm_node.parse_response(raw,batch)
    assert len(parsed['findings'])==1 and parsed['rejected']==1


def test_candidate_read_error_is_skipped(tmp_path, monkeypatch):
    source = tmp_path / "gone.py"
    source.write_text("def f():\n    pass\n", encoding="utf-8")
    original = __import__("pathlib").Path.read_bytes

    def fail_read(path, *args, **kwargs):
        if path == source:
            raise PermissionError("access denied")
        return original(path, *args, **kwargs)

    monkeypatch.setattr(__import__("pathlib").Path, "read_bytes", fail_read)
    assert llm_node.extract_candidates(str(source)) == []


def test_empty_llm_choices_degrades_gracefully(tmp_path, monkeypatch):
    class EmptyCompletions:
        async def create(self, **kwargs):
            return type("Response", (), {"choices": []})()

    class EmptyClient:
        chat = type("Chat", (), {"completions": EmptyCompletions()})()

        def __init__(self, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-fake-key-for-test")
    monkeypatch.setattr(llm_node, "AsyncOpenAI", EmptyClient)
    source = _make_long_func_file(tmp_path)
    result=llm_analyze_node({"project_path": str(tmp_path), "py_files": [source]})
    assert result['llm_findings']==[] and result['llm_outcomes'][0]['status']=='failed'
