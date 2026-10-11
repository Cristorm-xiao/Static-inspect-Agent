"""Offline aggregation and frozen Codex adjudication for the v0.3 experiment."""
from collections import Counter
import json
import hashlib
from pathlib import Path
import statistics
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from llm_protocol import parse_response


def read(p): return json.loads(p.read_text(encoding='utf-8'))
def save(p,value): p.write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')


REVIEWS={
    '02':[
        ('C','同一函数仅一处 shutil 导入；局部导入不等于重复导入，性能结论缺少证据。'),
        ('A','退出码归一化及输出收集的控制流确实复杂，可考虑提取；尚未验证重构收益。'),
        ('B','两个 arity 策略存在相似转换表达式，提取是否有益需权衡清晰度。'),
        ('B','width 是具名默认参数；解释取值来源可以改善文档，但不构成明确设计问题。'),
        ('A','同函数中重复 warnings 导入确实存在；表驱动建议还需保留延迟导入和版本差异。'),
    ],
    '04':[
        ('C','old_field_keys 在循环内 remove/add；直接将差集预计算到外层会改变候选更新语义，不能照搬。'),
        ('A','多层匹配与字段调整控制流有源码依据，可进入重构审查。'),
        ('B','命名和未使用解包槽属于低优先级建议；证据多包含一行 break，严格校验拒绝。'),
        ('B','verbosity 是 CLI 约定；证据只覆盖所报范围的一小段，严格校验拒绝。'),
    ],
    '05':[
        ('A','两次筛选群组成员的逻辑确实重复，但证据长度与申报区间不一致，被拒绝。'),
        ('A','嵌套过深目标命中；具体声称8层并非精确层数，不影响存在深层嵌套的判断。'),
        ('A','部分成立：列表成员检查有重复扫描开销；引用 c02 属跨片段比较，不能假定同一模块。'),
        ('A','部分成立：渠道间重复逻辑存在；建议直接调用 c04 是跨片段假设，不能按原文照搬。'),
        ('A','命名单字母目标有依据，但证据全文比所报区间多一行，被拒绝。'),
    ],
    '06':[
        ('A','费率与阈值缺少具名业务含义，命中预设魔法数值目标。'),
        ('B','阶梯规则可考虑表述更明确，但改写须保留当前累进语义。'),
        ('B','切片确有复制；已有重定向上限且历史快照语义重要，收益和改法需进一步验证。'),
        ('C','没有考虑 Python re 的缓存，不能从重复调用 re.compile 推出重复编译；性能结论无测量支持。'),
    ],
}
EMPTY_HASH='382c978337c2eaf42f72196c8b1fbdf232089465cf40a6ea22fb459ef893be38'
FROZEN={**{k:EMPTY_HASH for k in ('00','01','03','07','08')},
    '02':'cbe96925160c95190e9c83d87c584b9f6e714e0d7413246c4fc4cab283cfed59',
    '04':'7241136d050d51a434f48972c9a967e7feb4430a953a1909abd331ca948a2726',
    '05':'8381d4dd79f9e9308c1359f5b53f865de2f287d7a766029b998a2364bfec4de8',
    '06':'b0fed960192f09ca2dc4ea33fa5681de781ef4936c5557f81763693709960568'}


def summarize(offline, live):
    candidates=read(offline/'candidate-metrics.json'); timings=read(offline/'timings.json')
    timing_summary=[]
    for c in candidates:
        timing_summary.append({'project':c['project'],**{mode:statistics.median(r['seconds'] for r in timings if r['project']==c['project'] and r['mode']==mode)
            for mode in ('before','after_no_cache','after_cold','after_warm')}})
    samples={s['function_id']:s for s in read(live/'live-samples.json')}
    observed={p.stem:hashlib.sha256(read(p)['content'].encode()).hexdigest() for p in (live/'live-calls').glob('*.json')}
    if observed!=FROZEN: raise ValueError('Static review decisions only apply to frozen v2 responses.')
    result=read(live/'live-result.json'); reviews=[]
    for p in sorted((live/'live-calls').glob('*.json')):
        call=read(p); data=json.loads(call['content']); decisions=REVIEWS.get(p.stem,[])
        assert len(data['findings'])==len(decisions),'Only apply this adjudication to frozen v2 calls'
        ids=[c['function_id'] for c in json.loads(call['request']['messages'][-1]['content'])['functions']]
        for ordinal,(item,(category,reason)) in enumerate(zip(data['findings'],decisions)):
            parsed=parse_response(json.dumps({'findings':[item],'context_requests':[]}),[samples[i] for i in ids])
            reviews.append({'call':p.stem,'ordinal':ordinal,'group':samples[item['function_id']]['group'],
                'accepted':bool(parsed['findings']),'category':category,'review_reason':reason,'finding':item,
                'response_sha256':observed[p.stem],
                'source_sha256':hashlib.sha256(samples[item['function_id']]['source'].encode()).hexdigest()})
    save(live/'source-review.json',{'reviewer':'Codex, not independent human gold labels',
        'rubric':{'A':'具体改进线索（可部分成立）','B':'低优先级或设计取舍','C':'不准确、风险或上下文不足'},'items':reviews})
    groups={g:{'samples':sum(s['group']==g for s in samples.values()),
               'raw_suggestions':sum(r['group']==g for r in reviews),
               'accepted_suggestions':sum(r['group']==g and r['accepted'] for r in reviews),
               'accepted_categories':dict(Counter(r['category'] for r in reviews if r['group']==g and r['accepted']))}
            for g in ('original','control','heldout')}
    controls=[]
    for c in read(ROOT/'benchmarks/llm_controls.json')['cases']:
        controls.append({'id':c['id'],'positive':c['positive'],'target':c['target'],
            'raw_count':sum(r['finding']['function_id']==c['id'] for r in reviews),
            'accepted_count':sum(r['finding']['function_id']==c['id'] and r['accepted'] for r in reviews),
            'target_hit_after_validation':c['id'] in ('c01','c03','c07') if c['positive'] else None})
    save(live/'control-outcomes.json',controls)
    summary={'candidate_totals':{k:sum(c[k] for c in candidates) for k in ('old_candidates','new_candidates','old_source_bytes','new_source_bytes')},
        'timings_medians':timing_summary,'baseline':read(offline/'baseline-experiment.json'),
        'live_stats':result['stats'],'live_groups':groups,
        'controls':{'positive_target_hits_after_validation':3,'positive_cases':4,'negative_cases_with_suggestions':0,'negative_cases':4},
        'limits':['Single machine; 3 runs per before/no-cache/warm, 1 cold-cache observation.',
            'Candidate selection is heuristic, not an accuracy estimate.',
            'Two prompt rounds retained; v2 heldout10 excludes original25 and v1 heldout10.',
            'Source review by Codex; evidence matching does not imply semantic correctness.',
            'No live context request occurred; second-round behavior tested using fake network clients.']}
    save(live/'summary.json',summary); print(json.dumps(summary,ensure_ascii=False,indent=2))


if __name__=='__main__': summarize(Path(sys.argv[1]),Path(sys.argv[2]))
