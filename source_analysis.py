"""Encoding-aware parsing and reusable function metrics."""
import ast
from collections import Counter
import hashlib
import io
import tokenize

VERSION = 'source-v1'
FUNCTIONS = (ast.FunctionDef, ast.AsyncFunctionDef)
SCOPES = (*FUNCTIONS, ast.ClassDef)
BRANCHES = (ast.If, ast.For, ast.AsyncFor, ast.While, ast.Try, ast.With, ast.AsyncWith, ast.Match)


def digest(value):
    return hashlib.sha256(value.encode('utf-8')).hexdigest()


def parse_bytes(raw, filename):
    encoding, _ = tokenize.detect_encoding(io.BytesIO(raw).readline)
    source = raw.decode(encoding)
    return source, ast.parse(source, filename=filename)


def function_records(source, tree):
    lines = source.splitlines()
    code_lines = set()
    for token in tokenize.generate_tokens(io.StringIO(source).readline):
        if token.type not in {tokenize.COMMENT, tokenize.NL, tokenize.NEWLINE, tokenize.INDENT,
                              tokenize.DEDENT, tokenize.ENDMARKER, tokenize.ENCODING}:
            code_lines.update(range(token.start[0], token.end[0]+1))
    records = []
    occurrences = Counter()

    def walk(node, scope=''):
        next_scope = f'{scope}.{node.name}'.strip('.') if isinstance(node, SCOPES) else scope
        if isinstance(node, FUNCTIONS):
            excluded = set()
            for child in ast.walk(node):
                if isinstance(child, SCOPES) and child.body:
                    first = child.body[0]
                    if isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant) and isinstance(first.value.value, str):
                        excluded.update(range(first.lineno, first.end_lineno+1))
            own_nodes = []
            max_depth = 0
            def metrics(n, depth=0):
                nonlocal max_depth
                if n is not node and isinstance(n, SCOPES): return
                own_nodes.append(n)
                depth += isinstance(n, BRANCHES)
                max_depth = max(max_depth, depth)
                for child in ast.iter_child_nodes(n): metrics(child, depth)
            metrics(node)
            statements = Counter(ast.dump(n, include_attributes=False) for n in own_nodes
                                 if isinstance(n, (ast.Assign, ast.Expr, ast.Return)) and not
                                 (isinstance(n, ast.Expr) and isinstance(n.value, ast.Constant)))
            occurrences[next_scope] += 1
            records.append({'name':node.name, 'qualname':next_scope,
                'identity':f'{next_scope}#{occurrences[next_scope]}',
                'line':node.lineno, 'end_line':node.end_lineno,
                'source':'\n'.join(lines[node.lineno-1:node.end_lineno]),
                'metrics':{'effective_lines':len((code_lines & set(range(node.lineno,node.end_lineno+1))) - excluded),
                    'branches':sum(isinstance(n, (ast.If, ast.For, ast.AsyncFor, ast.While, ast.ExceptHandler, ast.IfExp)) for n in own_nodes),
                    'nesting':max_depth,
                    'parameters':len(node.args.posonlyargs+node.args.args+node.args.kwonlyargs)+bool(node.args.vararg)+bool(node.args.kwarg),
                    'repeated_statements':max(statements.values(), default=0)}})
        for child in ast.iter_child_nodes(node): walk(child, next_scope)
    walk(tree)
    return records


def select_candidates(records, threshold=30):
    selected = []
    for item in sorted(records, key=lambda r:(r['line'], -r['end_line'])):
        m = item['metrics']; reasons = []
        if m['effective_lines'] >= threshold: reasons.append('effective_lines')
        if m['nesting'] >= 4: reasons.append('nesting')
        if m['branches'] >= 8: reasons.append('branches')
        if m['repeated_statements'] >= 3: reasons.append('repetition')
        if m['parameters'] >= 10 and m['branches'] >= 2: reasons.append('parameters_and_branches')
        if reasons and not any(p['line'] <= item['line'] and p['end_line'] >= item['end_line'] for p in selected):
            selected.append({**item, 'selection_reasons':reasons})
    return selected
