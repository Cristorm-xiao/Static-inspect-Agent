"""Behavior regressions for the measured limitations; no network access."""
import json
from pathlib import Path

import llm_node
from main import main
from rules import check_file


def test_documentation_does_not_make_function_complex(tmp_path):
    p = tmp_path / 'doc.py'
    p.write_text('def simple(x):\n    """\n' + '    useful documentation\n'*40 + '    """\n    return x\n', encoding='utf-8')
    assert llm_node.extract_candidates(str(p)) == []


def test_short_deep_function_is_selected(tmp_path):
    p = tmp_path / 'deep.py'
    p.write_text('def f(xs):\n    for x in xs:\n        if x:\n            for y in x:\n                if y:\n                    print(y)\n', encoding='utf-8')
    assert len(llm_node.extract_candidates(str(p))) == 1


def test_declared_source_encoding_is_supported(tmp_path):
    p = tmp_path / 'latin.py'
    p.write_bytes('# coding: latin-1\nname = "caf\xe9"\n'.encode('latin-1'))
    assert check_file(str(p)) == []


def test_rethrow_is_not_described_as_swallowing(tmp_path):
    p = tmp_path / 'rethrow.py'
    p.write_text('try:\n    work()\nexcept:\n    cleanup()\n    raise\n', encoding='utf-8')
    found = check_file(str(p))
    assert len(found) == 1
    assert '吞掉' not in found[0]['desc']


def test_shadowed_eval_is_not_builtin(tmp_path):
    p = tmp_path / 'shadow.py'
    p.write_text('def f(eval, text):\n    return eval(text)\n\ndef g(text):\n    return eval(text)\n', encoding='utf-8')
    found = [x for x in check_file(str(p)) if x['rule_id'] == 'R003']
    assert [x['line'] for x in found] == [5]


def test_baseline_survives_comment_insertion(tmp_path, capsys):
    p = tmp_path / 'bad.py'; baseline = tmp_path / 'base.json'
    source = 'def f(a=[]):\n    return a\n'
    p.write_text(source, encoding='utf-8')
    assert main([str(tmp_path), '--write-baseline', str(baseline)]) == 0
    p.write_text('# comment\n' + source, encoding='utf-8')
    assert main([str(tmp_path), '--no-llm', '--baseline', str(baseline), '--format', 'json']) == 0
    data = json.loads(capsys.readouterr().out)
    assert data['baseline_suppressed'] == 1
    assert json.loads(baseline.read_text(encoding='utf-8'))['version'] == 2


def test_nested_candidates_are_not_sent_twice(tmp_path):
    p = tmp_path / 'nested.py'
    p.write_text('def outer():\n    def inner():\n' + '        print(1)\n'*35 + '    return inner()\n', encoding='utf-8')
    found = llm_node.extract_candidates(str(p))
    assert len(found) == 1


def test_cache_reuses_only_unchanged_files(tmp_path, capsys):
    p = tmp_path / 'a.py'; p.write_text('x=1\n', encoding='utf-8')
    args = [str(tmp_path), '--no-llm', '--format', 'json']
    assert main(args) == 0
    first = json.loads(capsys.readouterr().out)
    assert first.get('scan_stats', {}).get('parsed_files') == 1
    assert main(args) == 0
    second = json.loads(capsys.readouterr().out)
    assert second['scan_stats']['cache_hits'] == 1
    p.write_text('eval("1")\n', encoding='utf-8')
    assert main(args) == 1
    assert json.loads(capsys.readouterr().out)['scan_stats']['parsed_files'] == 1


def test_evidence_must_match_its_function():
    import importlib.util
    assert importlib.util.find_spec('llm_protocol') is not None
    from llm_protocol import parse_response
    batch = [{'function_id':'a:f#1','file':'a.py','line':10,'source':'def f():\n    return 1'}]
    good = {'function_id':'a:f#1','category':'example','evidence_lines':[2,2],
            'evidence':'return 1','impact':'explain impact','suggestion':'specific change','needs_context':False}
    bad = {**good,'evidence':'dt = tempfile.mkdtemp()'}
    data = parse_response(json.dumps({'findings':[bad,good,good],'context_requests':[]}),batch)
    assert data['valid'] and len(data['findings']) == 1
    assert data['rejected'] == 1
    assert data['findings'][0]['line'] == 11


def test_context_resolver_rejects_arbitrary_paths():
    import importlib.util
    assert importlib.util.find_spec('llm_protocol') is not None
    from llm_protocol import resolve_context
    batch=[{'function_id':'f','file':'a.py','source':'def f():\n    return helper()'}]
    index={'a.py':[{'name':'helper','qualname':'helper','source':'def helper():\n    return 1','line':1}]}
    text, resolved = resolve_context([{'function_id':'f','symbols':['../../.env','helper']}],batch,index,1000)
    assert len(resolved) == 1
    assert 'def helper' in text and '.env' not in text


def test_runtime_bounds_concurrency_and_exposes_failure():
    import asyncio
    import importlib.util
    from types import SimpleNamespace
    assert importlib.util.find_spec('llm_runtime') is not None
    from llm_runtime import analyze
    active = peak = 0
    class Completions:
        async def create(self, **kwargs):
            nonlocal active, peak
            active+=1; peak=max(peak,active)
            await asyncio.sleep(0.01)
            active-=1
            if 'f0#1' in kwargs['messages'][-1]['content']:
                import openai
                raise openai.APITimeoutError(request=None)
            return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content='{"findings":[],"context_requests":[]}'),finish_reason='stop')],usage=None,model='fake')
    class Client:
        def __init__(self, **kwargs): self.chat=SimpleNamespace(completions=Completions())
        async def __aenter__(self): return self
        async def __aexit__(self,*args): pass
    batch=[{'function_id':f'a:f{i}#1','file':'a.py','line':i*3+1,'source':f'def f{i}():\n    return 1'} for i in range(6)]
    result=asyncio.run(analyze('test','https://example.invalid','fake',batch,{},
        {'llm_batch_size':1,'llm_concurrency':2,'llm_max_requests':4},None,Client))
    assert peak<=2
    assert result['stats']['requests']==4
    states=[x['status'] for x in result['outcomes']]
    assert states.count('failed')==1 and states.count('skipped_budget')==2


def test_ignored_directory_is_pruned(tmp_path, monkeypatch):
    import scanner
    import os
    (tmp_path/'keep.py').write_text('x=1',encoding='utf-8')
    (tmp_path/'.venv').mkdir()
    (tmp_path/'.venv'/'bad.py').write_text('x=2',encoding='utf-8')
    original=os.scandir
    def guard(path):
        assert Path(path).name!='.venv', 'Excluded directory was traversed'
        return original(path)
    monkeypatch.setattr(os,'scandir',guard)
    assert len(scanner.collect_py_files(tmp_path))==1


def test_cli_reports_disabled_llm_explicitly(tmp_path,capsys):
    assert main([str(tmp_path),'--no-llm','--format','json'])==0
    assert json.loads(capsys.readouterr().out)['llm_stats']['status']=='disabled'


def test_runtime_context_round_and_cache_invalidation(tmp_path):
    import asyncio
    from types import SimpleNamespace
    from inspection_cache import Cache
    from llm_runtime import analyze
    calls=[]
    class Client:
        def __init__(self,**kwargs): self.chat=SimpleNamespace(completions=self)
        async def __aenter__(self): return self
        async def __aexit__(self,*args): pass
        async def create(self,**kwargs):
            prompt=json.loads(kwargs['messages'][-1]['content']); calls.append(prompt)
            data={'findings':[],'context_requests':[] if prompt['additional_context'] else [{'function_id':'a:f#1','symbols':['helper']}]}
            return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=json.dumps(data)),finish_reason='stop')],usage=None)
    candidates=[{'function_id':'a:f#1','file':'a.py','line':1,'source':'def f():\n    return helper()'}]
    index={'a.py':[{'name':'helper','qualname':'helper','line':4,'source':'def helper():\n    return 1'}]}
    cache=Cache(tmp_path/'cache')
    run=lambda:asyncio.run(analyze('fake','https://example.invalid','fake',candidates,index,{},cache,Client))
    first=run(); assert first['stats']['context_rounds']==1 and len(calls)==2
    second=run(); assert second['stats']['cache_hits']==1 and len(calls)==2
    index['a.py'][0]['source']='def helper():\n    return 2'
    run(); assert len(calls)==4


def test_budget_and_oversized_input_do_not_call_api():
    import asyncio
    from llm_runtime import analyze
    class Client:
        def __init__(self,**kwargs): pass
        async def __aenter__(self): return self
        async def __aexit__(self,*args): pass
    c={'function_id':'f','file':'a.py','line':1,'source':'def f():\n    return 1'}
    result=asyncio.run(analyze('fake','https://example.invalid','fake',[c],{}, {'llm_input_budget':1},None,Client))
    assert result['outcomes'][0]['status']=='skipped_budget' and result['stats']['requests']==0
    result=asyncio.run(analyze('fake','https://example.invalid','fake',[c],{}, {'llm_batch_tokens':1},None,Client))
    assert result['outcomes'][0]['status']=='skipped_oversize' and result['stats']['requests']==0


def test_eval_binding_respects_definition_and_comprehension_scopes(tmp_path):
    p=tmp_path/'scope.py'
    p.write_text('def f(eval=eval("1")):\n    return eval("2")\n'
                 'xs = [eval for eval in range(2)]\n'
                 'eval("3")\n'
                 'class C:\n    eval = staticmethod(str)\n    def method(self):\n        return eval("4")\n',encoding='utf-8')
    assert [x['line'] for x in check_file(str(p)) if x['rule_id']=='R003']==[1,4,8]


def test_cache_expiry_refresh_and_corruption(tmp_path):
    from inspection_cache import Cache
    cache=Cache(tmp_path); cache.put('test','key',{'ok':True})
    assert cache.get('test','key')=={'ok':True}
    assert Cache(tmp_path,refresh=True).get('test','key') is None
    p=tmp_path/'test/key.json'; data=json.loads(p.read_text(encoding='utf-8')); data['created']=0
    p.write_text(json.dumps(data),encoding='utf-8'); assert cache.get('test','key') is None
    p.write_text('{',encoding='utf-8'); assert cache.get('test','key') is None


def test_v1_baseline_still_loads():
    from baseline import filter_baseline
    from collections import Counter
    finding={'file':'a.py','rule_id':'R003','line':2,'fingerprint':'new'}
    assert filter_baseline([finding],Counter({('v1','R003','a.py',2):1}))==([],1)


def test_malformed_cache_payload_is_recomputed(tmp_path,capsys):
    p=tmp_path/'a.py'; p.write_text('eval("1")\n',encoding='utf-8')
    args=[str(tmp_path),'--no-llm','--format','json']
    assert main(args)==1; capsys.readouterr()
    stored=next((tmp_path/'.inspect-cache/source').glob('*.json'))
    data=json.loads(stored.read_text(encoding='utf-8'))
    data['value']={'findings':[None],'functions':[None]}
    stored.write_text(json.dumps(data),encoding='utf-8')
    assert main(args)==1
    result=json.loads(capsys.readouterr().out)
    assert result['scan_stats']['parsed_files']==1 and result['rule_findings'][0]['rule_id']=='R003'


def test_eval_walrus_pattern_and_nested_class_bindings(tmp_path):
    p=tmp_path/'bindings.py'
    p.write_text('def f(xs):\n    [(eval := str) for x in xs]\n    return eval("x")\n'
        'def g(x):\n    match x:\n        case {**eval}:\n            return eval("x")\n'
        'class A:\n    eval = str\n    class B:\n        x = eval("1")\n',encoding='utf-8')
    assert [f['line'] for f in check_file(str(p)) if f['rule_id']=='R003']==[11]


def test_context_failure_retains_failure_reason():
    import asyncio
    from types import SimpleNamespace
    from llm_runtime import analyze
    class Client:
        def __init__(self,**kwargs): self.chat=SimpleNamespace(completions=self)
        async def __aenter__(self): return self
        async def __aexit__(self,*args): pass
        async def create(self,**kwargs):
            data=json.loads(kwargs['messages'][-1]['content'])
            if data['additional_context']:
                import openai
                raise openai.APITimeoutError(request=None)
            return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=json.dumps(
                {'findings':[],'context_requests':[{'function_id':'f','symbols':['helper']}]})),finish_reason='stop')],usage=None)
    candidates=[{'function_id':'f','file':'a.py','line':1,'source':'def f():\n    return helper()'}]
    index={'a.py':[{'name':'helper','qualname':'helper','source':'def helper():\n    return 1'}]}
    result=asyncio.run(analyze('fake','https://example.invalid','fake',candidates,index,{},None,Client))
    assert result['outcomes'][0]['status']=='context_failed'


def test_llm_cache_reuses_unchanged_functions_when_batch_changes(tmp_path):
    import asyncio
    from types import SimpleNamespace
    from inspection_cache import Cache
    from llm_runtime import analyze
    calls=[]
    class Client:
        def __init__(self,**kwargs): self.chat=SimpleNamespace(completions=self)
        async def __aenter__(self): return self
        async def __aexit__(self,*args): pass
        async def create(self,**kwargs):
            calls.append(json.loads(kwargs['messages'][-1]['content'])['functions'])
            return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content='{"findings":[],"context_requests":[]}'),finish_reason='stop')],usage=None)
    a={'function_id':'a:f','file':'a.py','line':1,'source':'def f():\n    return 1'}
    b={'function_id':'b:g','file':'b.py','line':1,'source':'def g():\n    return 2'}
    cache=Cache(tmp_path/'cache')
    run=lambda cs:asyncio.run(analyze('fake','https://example.invalid','fake',cs,{}, {},cache,Client))
    run([a,b]); b={**b,'source':'def g():\n    return 3'}
    result=run([a,b])
    assert result['stats']['cache_hits']==1 and len(calls[-1])==1 and calls[-1][0]['function_id']=='b:g'


def test_prompt_provides_explicit_relative_line_numbers():
    from llm_protocol import build_prompt
    c={'function_id':'f','file':'a.py','source':'def f():\n    return 1'}
    prompt=json.loads(build_prompt([c]))
    assert prompt['functions'][0]['source']=='1 | def f():\n2 |     return 1'
