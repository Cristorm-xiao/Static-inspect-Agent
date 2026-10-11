"""Offline aggregation of the frozen live run and Codex source-review decisions."""
import argparse
from collections import Counter
import csv
import hashlib
import json
from pathlib import Path
import statistics

FROZEN_RESPONSES = {
    'primary-click-0': '28c819f01c524507afff5f9ff21d43593d6f16bb7f7335647e5abe0d8d06cf87',
    'primary-django-0': 'bfac1e7f701c660a1c0debaeeccc34ad5c90dab52175bc726b929799620151a9',
    'primary-flask-0': '1d056f58b2803207674ac96a164bcd0bcf50e80784688d2c08c239e2b2e54cb6',
    'primary-httpx-0': '000d7bfb357f774b1010ef04f2e9091341e7771953b2fd201137f643b3092c2d',
    'primary-requests-0': 'f899e816130e13cc0e2cf076fbb0677d8ed67d0916073e307fa555c7cf73e78b',
}
CONTROL_REVIEW = {
    'c01': '命中嵌套目标：多层群组、成员、权限循环及条件。',
    'c02': '负例收到文档冗长建议，属于风格偏好，未证明需要重点修改。',
    'c03': '命中重复目标：不同渠道的计算块重复；额外常量与命名建议不计目标命中。',
    'c04': '负例将非负截断的 0 当作未解释魔法值，文档已说明非负金额语义。',
    'c05': '命中命名目标：函数和变量名称缺少语义。',
    'c06': '负例收到文档冗长建议，复杂度与异常说明是否保留是风格取舍。',
    'c07': '命中魔法数字目标：计算阈值及比例缺少语义命名。',
    'c08': '负例将紧密相关的字段提取、计算及结果构造认定多职责，缺少实质证据。',
}

# Ordered against original response items; A is a review lead, never a confirmed bug.
REVIEWS = {
    'requests': [
        ('B', 'HTTP 头与 scheme 是协议约定，提取常量属于维护偏好。'),
        ('B', '代理解析和认证头调整共同完成重定向代理重建，未证明需要拆分。'),
        ('B', 'https、socks 和双斜线具有协议含义。'),
        ('B', 'url 表示最终请求地址，重赋值可读性建议优先级低。'),
        ('A', 'Jython 与 IronPython 分支执行相同语句，可考虑合并。'),
        ('B', '实现名称来自平台标识，非任意魔法值。'),
        ('A', '部分成立：tried_encodings 只追加而未读取；encoding=None 的 TypeError 已被处理，不能确认逻辑错误。'),
        ('B', '弃用提醒与兼容解码属于同一公开接口的生命周期行为。'),
        ('C', '函数仅执行一次辅助函数调用；长文档不构成职责过多。'),
        ('C', '文档明确说明构建连接池属性及子类覆盖用途，委托实现不使名称失真。'),
    ],
    'flask': [
        ('B', '两个短回退分支模式相同，提取辅助函数收益有限。'),
        ('C', '状态类构造器初始化相关属性，未提供多职责的具体证据。'),
        ('B', '主机与子域条件存在，但嵌套较浅且承担配置分流。'),
        ('B', '这些名称是公开配置键，不能仅凭字符串认定设计问题。'),
        ('B', '应用构造器负责初始化应用及静态路由，拆分属于设计取舍。'),
        ('B', 'static 及路径为框架路由约定。'),
        ('B', 'flashes 重赋值表达过滤消息集合，命名建议优先级低。'),
        ('B', '_flashes 是会话存储键，单独常量是可选维护策略。'),
        ('B', 'site-packages 与 lib 是目录布局约定。'),
        ('B', '平台判断使用提前返回，不能仅从分支数量推断需要重构。'),
    ],
    'click': [
        ('C', '局部导入可能用于启动开销或依赖管理，缺少必须移到顶部的证据。'),
        ('A', '临时目录清理异常被静默忽略，可考虑可选诊断日志；不建议直接抛出破坏清理语义。'),
        ('A', '调用执行、退出码归一化和结果封装包含可单独审查的复杂阶段。'),
        ('A', 'with、try 与多层退出码分支确实增加阅读复杂度；与上一建议重叠。'),
        ('B', '0 与 1 是常规进程退出状态。'),
        ('C', 'dt 不在 invoke 函数中，而在另一个输入片段，存在跨片段混淆。'),
        ('B', '不同输出流需要分别 flush/getvalue，类似表达式不等于有益的抽象机会。'),
        ('B', '三个 convert 定义分别实现不同 arity 的转换策略，并非重复实现。'),
        ('B', 'nargs=-1/1 是参数数量约定，附近有语义注释。'),
        ('B', '包装函数用于异常转换，与底层迭代器检查名称相似并不直接造成错误。'),
        ('B', '公开 API 的较多可选参数需结合兼容性判断，不能直接改配置对象。'),
        ('B', 'width 与 update_min_steps 已是具名且有文档的默认参数。'),
        ('C', '局部导入是否应移动需要依赖和启动上下文，片段不足以支持结论。'),
        ('C', '主要为文档及参数转发，没有所声称的复杂参数解析职责。'),
        ('A', '弃用分支可考虑共享消息逻辑，但需保留延迟导入和版本差异。'),
        ('A', '同一函数已导入 warnings，版本分支内再次导入确实冗余。'),
        ('B', '9.0/9.1 是弃用版本政策，不能任意替换。'),
    ],
    'httpx': [
        ('B', '请求对象初始化需要组合 URL、头和消息体，尚不足以认定职责过多。'),
        ('B', '内容类型条件表达式可读性可改善，但嵌套不深。'),
        ('B', 'content-type 是标准 HTTP 头。'),
        ('A', '多个输入模式重复赋值同样四个字段，可审查是否先归一化再统一赋值。'),
        ('B', '多数为平铺的模式分流，复杂性来自支持多种合法输入。'),
        ('B', '元组索引与具名超时字段对应，属于既定参数格式。'),
        ('C', '模型未获得 get/post/put 片段，不能仅凭当前函数确认可消除的重复。'),
        ('B', '公开 HTTP 方法保留显式关键字参数有类型提示和兼容性收益。'),
        ('B', '流式接口参数与请求接口对齐，是否缩减需要 API 上下文。'),
        ('B', '显式参数转发支持类型提示，抽象收益未被证明。'),
        ('C', '没有性能测量或依赖上下文证明局部导入造成问题。'),
        ('B', '类型适配、调用和响应转换共同承担传输适配职责。'),
        ('C', 'assert 不是魔法值；内部不变量检查是否替换需结合调用约束。'),
    ],
    'django': [
        ('A', '嵌套循环和多层条件确实较深，可审查提前过滤或抽取比较函数。'),
        ('A', '重复使用 deconstruct 结果的索引和切片，具名局部变量可澄清含义。'),
        ('A', '候选匹配、关系调整与状态记录较复杂，可按阶段提取；与嵌套建议部分重叠。'),
        ('A', '路径解析、转换构造与 join 创建可作为单独审查的阶段。'),
        ('C', '将循环内 try/except/else 误称为 for/else；复杂性存在但具体控制流描述不准确。'),
        ('B', 'targets 等变量在文档中有说明，命名偏好不足以证明问题。'),
        ('C', 'docstring 已明确解释 escape/safe 禁止条件，var| 是模板表达式语法。'),
        ('C', 'unused 明确标记不用的解包位置，建议缺乏实质依据。'),
        ('B', '同步与异步包装需不同调用方式，主要公共逻辑已抽取。'),
        ('B', 'GET、HEAD 与 HTTP 头名称是协议常量。'),
        ('A', 'SQL 构建与数据库执行可以分离以改善局部可测试性，尚未实施验证。'),
        ('B', '255 与 verbosity 阈值需结合缓存字段及 CLI 约定，不直接认定缺陷。'),
        ('C', 'dry-run 打印 SQL、实际路径执行 SQL，操作不同，不能作为重复输出处理。'),
    ],
}

def read(p):
    return json.loads(p.read_text(encoding='utf-8'))

def save(p, value):
    p.write_text(json.dumps(value, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')

def summarize(root):
    calls = sorted((read(p) for p in (root/'calls').glob('*.json')), key=lambda c:c['call_id'])
    observed = {c['call_id']: hashlib.sha256(c['content'].encode()).hexdigest() for c in calls if c['kind']=='primary'}
    if observed != FROZEN_RESPONSES:
        raise ValueError('Review labels only apply to the frozen 2026-10-10 responses; review new responses separately.')
    samples = {s['sample_id']: s for s in read(root/'samples.json')}
    rows = []
    projects = []
    for c in calls:
        if c['kind'] != 'primary': continue
        project = c['phase'].removeprefix('primary-')
        content = c['content'].strip()
        if content.startswith('```'): content = '\n'.join(content.splitlines()[1:-1])
        raw = json.loads(content)
        assert len(raw) == len(REVIEWS[project]) == len(c['parsed_findings'])
        for ordinal, (finding, (category, reason)) in enumerate(zip(raw, REVIEWS[project])):
            sample = samples[c['sample_ids'][finding['index']]]
            rows.append(dict(call_id=c['call_id'], ordinal=ordinal, sample_id=sample['sample_id'],
                category=category, review_reason=reason, **finding, source_url=sample['source_url'],
                source_sha256=sample['source_sha256'], response_sha256=hashlib.sha256(c['content'].encode()).hexdigest()))
        projects.append(dict(project=project, functions=len(c['sample_ids']), suggestions=len(raw), seconds=c['seconds'],
            prompt_tokens=c['usage']['prompt_tokens'], completion_tokens=c['usage']['completion_tokens']))
    save(root/'source-review.json', {'reviewer':'Codex; not human gold labels',
        'rubric': {'A':'有源码依据的具体改进线索，需进一步审查，不是确认缺陷', 'B':'低优先级、风格偏好或需权衡的设计建议', 'C':'描述不准确或缺少支持结论的上下文'}, 'items':rows})
    with (root/'source-review.csv').open('w',encoding='utf-8-sig',newline='') as f:
        writer=csv.DictWriter(f,fieldnames=list(rows[0])); writer.writeheader(); writer.writerows(rows)
    repetitions=[next(c for c in calls if c['phase']==p) for p in ['primary-requests','repeat-requests-2','repeat-requests-3']]
    def exact(c): return {(f['file'],f['line'],f['smell'],f['reason']) for f in c['parsed_findings']}
    repeat_metrics={'counts':[len(c['parsed_findings']) for c in repetitions],
        'flagged_function_counts':[len({(f['file'],f['line']) for f in c['parsed_findings']}) for c in repetitions],
        'exact_pairwise_jaccard':[len(exact(a)&exact(b))/len(exact(a)|exact(b)) for a,b in [(repetitions[0],repetitions[1]),(repetitions[0],repetitions[2]),(repetitions[1],repetitions[2])]],
        'note':'Exact text agreement is not semantic agreement; three observations only.'}
    controls=[]
    for label in read(root/'control-labels.json')['cases']:
        findings=[f for c in calls if c['kind']=='controls' for f in c['parsed_findings'] if f['file']==f"controls/{label['id']}.py"]
        controls.append({'id':label['id'], 'label':{k:v for k,v in label.items() if k!='source'},'findings':findings,
            'target_hit_by_codex_review': True if label['positive'] else None,
            'any_suggestion': bool(findings), 'review_reason': CONTROL_REVIEW[label['id']]})
    save(root/'control-outcomes.json',controls)
    summary={'formal_logical_calls':len(calls),'successful_calls':sum(c['status']=='ok' for c in calls),
        'http_attempts':sum(c['http_attempts'] for c in calls),'http_statuses':dict(Counter(str(s) for c in calls for s in c['http_statuses'])),
        'strict_valid_after_fence_removal':sum(c['format']['strict_items'] for c in calls),
        'fenced_responses':sum(c['content'].strip().startswith('```') for c in calls),
        'returned_models':sorted({c['returned_model'] for c in calls}),
        'tokens':{k:sum(c['usage'].get(k,0) for c in calls) for k in ['prompt_tokens','completion_tokens','total_tokens','prompt_cache_hit_tokens','prompt_cache_miss_tokens']},
        'primary':projects,'review_categories':dict(Counter(r['category'] for r in rows)),
        'primary_latency_median_seconds':statistics.median(p['seconds'] for p in projects),
        'repeat_requests':repeat_metrics, 'cli':read(root/'phase-results/cli-smoke.json'),
        'limitations':['Only 25/1144 real candidate functions sampled, five per project; not population-weighted.',
            '63 suggestions reviewed by Codex, no exhaustive defect truth, no accuracy/recall estimate.',
            'Controls: 4/4 intended positive smells observed and 4/4 negative controls received suggestions; tiny constructed set.',
            'Sequential project groups; does not measure whole-project concurrency, rate limiting or production reliability.',
            'Connectivity probe 7 input + 1 output tokens excluded from formal metrics.']}
    save(root/'summary.json',summary)
    print(json.dumps(summary,ensure_ascii=False,indent=2))

if __name__=='__main__':
    parser=argparse.ArgumentParser(); parser.add_argument('root',type=Path)
    summarize(parser.parse_args().root)
