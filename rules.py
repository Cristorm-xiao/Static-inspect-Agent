"""规则引擎：对每个 .py 文件做 AST 级别的确定性检查。

设计原则：
- 装饰器注册表：每条规则独立注册，扩展新规则只需写函数 + 加 @rule 装饰器
- 发现项格式统一：{file, line, rule, rule_id, severity, desc}
- 严重级别：error（必改）/ warning（建议改）/ info（提示）
- 兼容现有测试：rule 字段保留，新增 rule_id 和 severity
"""
import ast
import logging
import hashlib
from pathlib import Path

from state import InspectState
from source_analysis import parse_bytes, digest, function_records, select_candidates, VERSION
from inspection_cache import project_cache, cache_key

logger = logging.getLogger(__name__)

# 严重级别常量
SEVERITY_ERROR = "error"
SEVERITY_WARNING = "warning"
SEVERITY_INFO = "info"


# ---- 规则注册表 ----
# 每条规则是一个 dict: {rule_id, name, severity, visit}
# visit(node, ctx) → list[dict]  返回该节点命中的发现
_RULES: list[dict] = []


def rule(rule_id: str, name: str, severity: str = SEVERITY_WARNING):
    """规则注册装饰器：标记一个函数为规则并加入注册表。

    用法：
        @rule("R001", "bare_except", SEVERITY_ERROR)
        def _check(node, ctx):
            ...
    """
    def decorator(fn):
        _RULES.append({
            "rule_id": rule_id,
            "name": name,
            "severity": severity,
            "check": fn,
        })
        return fn
    return decorator


def get_rules() -> list[dict]:
    """返回所有已注册规则（供 CLI 打印规则清单）。"""
    return list(_RULES)


# ---- 规则实现 ----

@rule("R001", "bare_except", SEVERITY_ERROR)
def _check_bare_except(node, ctx) -> list[dict]:
    """except: 不带异常类型 → 吞掉 KeyboardInterrupt 等。"""
    if node.type is None:
        return [{
            "file": ctx["file"],
            "line": getattr(node, "lineno", 0),
            "rule": "bare_except",
            "rule_id": "R001",
            "severity": SEVERITY_ERROR,
            "desc": ("裸 except 捕获所有异常后直接重新抛出；请确认确需覆盖 BaseException"
                     if node.body and isinstance(node.body[-1], ast.Raise) and node.body[-1].exc is None
                     else "裸 except 捕获所有异常（含 KeyboardInterrupt），可能阻止异常传播，请检查处理路径"),
        }]
    return []


@rule("R002", "mutable_default_arg", SEVERITY_ERROR)
def _check_mutable_default(node, ctx) -> list[dict]:
    """list/dict/set 作为函数默认值 → 所有调用共享同一对象。"""
    findings = []
    positional = node.args.posonlyargs + node.args.args
    defaults = list(zip(positional[len(positional)-len(node.args.defaults):], node.args.defaults))
    defaults += list(zip(node.args.kwonlyargs, node.args.kw_defaults))
    for argument, default in defaults:
        if isinstance(default, (ast.List, ast.Dict, ast.Set)):
            findings.append({
                "_identity": argument.arg + ':' + ast.dump(default, include_attributes=False),
                "file": ctx["file"],
                "line": getattr(node, "lineno", 0),
                "rule": "mutable_default_arg",
                "rule_id": "R002",
                "severity": SEVERITY_ERROR,
                "desc": f"函数 {node.name} 的默认参数是可变对象，调用间会共享状态",
            })
    return findings


@rule("R003", "eval_call", SEVERITY_WARNING)
def _check_eval_call(node, ctx) -> list[dict]:
    """A direct eval call deserves review because it may execute untrusted input."""
    if not isinstance(node.func, ast.Name) or node.func.id != "eval" or ctx.get("eval_shadowed"):
        return []
    return [{
        "file": ctx["file"], "line": node.lineno, "rule": "eval_call",
        "rule_id": "R003", "severity": SEVERITY_WARNING,
        "desc": "直接调用 eval 可能执行不可信输入，请确认输入来源并优先使用安全解析方式",
    }]


@rule("R004", "wildcard_import", SEVERITY_WARNING)
def _check_wildcard_import(node, ctx) -> list[dict]:
    if not any(alias.name == "*" for alias in node.names):
        return []
    return [{
        "file": ctx["file"], "line": node.lineno, "rule": "wildcard_import",
        "rule_id": "R004", "severity": SEVERITY_WARNING,
        "desc": "通配导入会模糊名称来源，建议显式导入所需名称",
    }]


# ---- 注册规则对应的 AST 节点类型 → 规则函数映射 ----
# 遍历一次 AST 时，每个节点类型触发对应的规则
_NODE_TYPE_RULES: dict[type, list] = {}


def _build_dispatch_table():
    """把规则注册表里的规则按它们关心的 AST 节点类型分组。

    当前规则数量少，直接在规则里硬编码关心的节点类型；
    后续规则多了可以在 @rule 里加 ast_type 参数自动生成。
    """
    # 规则1: bare_except → ExceptHandler 节点
    _NODE_TYPE_RULES.setdefault(ast.ExceptHandler, []).append(_check_bare_except)
    # 规则2: mutable_default_arg → FunctionDef/AsyncFunctionDef
    _NODE_TYPE_RULES.setdefault(ast.FunctionDef, []).append(_check_mutable_default)
    _NODE_TYPE_RULES.setdefault(ast.AsyncFunctionDef, []).append(_check_mutable_default)
    _NODE_TYPE_RULES.setdefault(ast.Call, []).append(_check_eval_call)
    _NODE_TYPE_RULES.setdefault(ast.ImportFrom, []).append(_check_wildcard_import)


_build_dispatch_table()


# ---- AST 遍历：一次遍历触发所有注册规则 ----

class RuleVisitor(ast.NodeVisitor):
    """Lexical binding check, deliberately not a flow-sensitive alias analysis."""
    def __init__(self, file_path):
        self.ctx = {"file": file_path}
        self.findings = []
        self.frames = []

    def _push(self, node, kind, name):
        names=set(); globals_=set()
        def collect(n):
            if n is not node and isinstance(n,(ast.FunctionDef,ast.AsyncFunctionDef,ast.ClassDef,ast.Lambda)):
                if hasattr(n,'name'): names.add(n.name)
                return
            if isinstance(n,(ast.ListComp,ast.SetComp,ast.DictComp,ast.GeneratorExp)):
                def collect_walrus(part):
                    if isinstance(part,(ast.Lambda,ast.FunctionDef,ast.AsyncFunctionDef)): return
                    if isinstance(part,ast.NamedExpr) and isinstance(part.target,ast.Name): names.add(part.target.id)
                    for child in ast.iter_child_nodes(part): collect_walrus(child)
                collect_walrus(n)
                return
            if isinstance(n,ast.Name) and isinstance(n.ctx,ast.Store): names.add(n.id)
            if isinstance(n,ast.arg): names.add(n.arg)
            if isinstance(n,(ast.Import,ast.ImportFrom)):
                names.update(a.asname or a.name.split('.')[0] for a in n.names)
            if isinstance(n,ast.ExceptHandler) and n.name: names.add(n.name)
            if isinstance(n,(ast.MatchAs,ast.MatchStar)) and n.name: names.add(n.name)
            if isinstance(n,ast.MatchMapping) and n.rest: names.add(n.rest)
            if isinstance(n,ast.Global): globals_.update(n.names)
            for child in ast.iter_child_nodes(n): collect(child)
        def bind():
            collect(node)
            names.difference_update(globals_)
        # Only an actual eval call needs binding analysis; most files never use it.
        self.frames.append([kind,name,names,globals_,bind])

    def _shadowed(self):
        nested=False
        for frame in reversed(self.frames):
            kind,name,names,globals_,bind=frame
            if kind=='class' and nested: continue
            if bind:
                bind(); frame[4]=None
            if 'eval' in globals_:
                if self.frames[0][4]:
                    self.frames[0][4](); self.frames[0][4]=None
                return bool(self.frames and 'eval' in self.frames[0][2])
            if 'eval' in names: return True
            if kind in ('function','comprehension','class'): nested=True
        return False

    def visit(self,node):
        checks=_NODE_TYPE_RULES.get(type(node),[])
        if isinstance(node,ast.Call) and isinstance(node.func,ast.Name) and node.func.id=='eval':
            self.ctx['eval_shadowed']=self._shadowed()
        scope=[f[1] for f in self.frames if f[1]] if checks else []
        if checks and isinstance(node,(ast.FunctionDef,ast.AsyncFunctionDef)): scope.append(node.name)
        for check_fn in checks:
            for finding in check_fn(node,self.ctx):
                identity=finding.pop('_identity',None)
                if identity is None: identity=ast.dump(node,include_attributes=False)
                finding['scope']='.'.join(scope) or '<module>'
                finding['fingerprint']=digest(finding['rule_id']+'|'+finding['scope']+'|'+identity)
                self.findings.append(finding)
        return super().visit(node)

    def visit_Module(self,node):
        self._push(node,'module','')
        self.generic_visit(node)
        self.frames.pop()

    def visit_FunctionDef(self,node):
        # Defaults, decorators and annotations execute outside the function body.
        for item in node.decorator_list + node.args.defaults + [x for x in node.args.kw_defaults if x]: self.visit(item)
        for arg in node.args.posonlyargs + node.args.args + node.args.kwonlyargs + [a for a in (node.args.vararg,node.args.kwarg) if a]:
            if arg.annotation: self.visit(arg.annotation)
        if node.returns: self.visit(node.returns)
        self._push(node,'function',node.name)
        for item in node.body: self.visit(item)
        self.frames.pop()

    visit_AsyncFunctionDef=visit_FunctionDef

    def visit_ClassDef(self,node):
        for item in node.decorator_list + node.bases + node.keywords: self.visit(item)
        self._push(node,'class',node.name)
        for item in node.body: self.visit(item)
        self.frames.pop()

    def visit_Lambda(self,node):
        for item in node.args.defaults + [x for x in node.args.kw_defaults if x]: self.visit(item)
        self._push(node,'function','<lambda>'); self.visit(node.body); self.frames.pop()

    def _visit_comprehension(self,node):
        self.visit(node.generators[0].iter)
        names={n.id for gen in node.generators for n in ast.walk(gen.target) if isinstance(n,ast.Name)}
        self.frames.append(['comprehension','<comprehension>',names,set(),None])
        for i,gen in enumerate(node.generators):
            if i: self.visit(gen.iter)
            for condition in gen.ifs: self.visit(condition)
        if isinstance(node,ast.DictComp): self.visit(node.key); self.visit(node.value)
        else: self.visit(node.elt)
        self.frames.pop()

    visit_ListComp=_visit_comprehension
    visit_SetComp=_visit_comprehension
    visit_DictComp=_visit_comprehension
    visit_GeneratorExp=_visit_comprehension


# ---- 入口函数 ----

def check_file(file_path: str) -> list[dict]:
    """对单个文件跑所有规则，返回发现列表。"""
    try:
        source, tree = parse_bytes(Path(file_path).read_bytes(), file_path)
    except (SyntaxError, UnicodeDecodeError, PermissionError, OSError) as e:
        return [{
            "file": file_path,
            "line": getattr(e, "lineno", 0),
            "rule": "syntax_error",
            "rule_id": "E000",
            "severity": SEVERITY_ERROR,
            "desc": str(e)[:120],
        }]

    visitor = RuleVisitor(file_path)
    visitor.visit(tree)
    return visitor.findings


def _valid_cached_source(data, relative):
    if not isinstance(data,dict) or not isinstance(data.get('findings'),list) or not isinstance(data.get('functions'),list): return False
    for f in data['findings']:
        if (not isinstance(f,dict) or f.get('file')!=relative or type(f.get('line')) is not int or f['line']<1
                or any(not isinstance(f.get(k),str) or not f[k] for k in ('rule_id','rule','severity','desc','scope','fingerprint'))): return False
    for c in data['functions']:
        if (not isinstance(c,dict) or any(not isinstance(c.get(k),str) or not c[k] for k in ('name','qualname','identity','source'))
                or type(c.get('line')) is not int or type(c.get('end_line')) is not int or not 1<=c['line']<=c['end_line']
                or not isinstance(c.get('metrics'),dict)
                or any(type(c['metrics'].get(k)) is not int or c['metrics'][k]<0 for k in
                       ('effective_lines','branches','nesting','parameters','repeated_statements'))): return False
    return True


def rule_check_node(state: InspectState) -> dict:
    """LangGraph 节点：对所有 py_files 跑规则检查。"""
    findings = []; candidates=[]; index={}
    cfg=state.get('config',{}); cache=project_cache(state)
    stats={'parsed_files':0,'cache_hits':0,'read_errors':0}
    root=Path(state['project_path']).resolve()
    include_functions=state.get('enable_llm',True)
    for py_file in state.get("py_files", []):
        relative=Path(py_file).relative_to(root).as_posix()
        try:
            raw=Path(py_file).read_bytes()
            file_hash=hashlib.sha256(raw).hexdigest()
            key=cache_key([VERSION,'rules-v3',relative,file_hash,include_functions,cfg.get('ignore_package_reexports',False)])
            data=cache.get('source',key)
            if not _valid_cached_source(data,relative):
                source,tree=parse_bytes(raw,py_file); stats['parsed_files']+=1
                visitor=RuleVisitor(relative); visitor.visit(tree)
                rules=visitor.findings
                if cfg.get('ignore_package_reexports') and Path(py_file).name=='__init__.py':
                    rules=[f for f in rules if f['rule_id']!='R004']
                data={'findings':rules,'functions':function_records(source,tree) if include_functions else []}
                cache.put('source',key,data)
            else: stats['cache_hits']+=1
            findings.extend(data['findings'])
            index[relative]=data['functions']
            for cand in select_candidates(data['functions'],cfg.get('long_func_threshold',30)):
                candidates.append({**cand,'file':relative,'function_id':relative+':'+cand['identity'],'file_hash':file_hash})
        except (SyntaxError,UnicodeError,OSError,LookupError) as exc:
            stats['read_errors']+=1
            findings.append({'file':relative,'line':getattr(exc,'lineno',0) or 0,'rule':'syntax_error',
                'rule_id':'E000','severity':'error','desc':str(exc)[:120]})
    logger.info("检查 %d 个文件，发现 %d 处问题",
                len(state.get('py_files', [])), len(findings))
    return {'rule_findings':findings,'candidates':candidates,'source_index':index,'scan_stats':stats}
