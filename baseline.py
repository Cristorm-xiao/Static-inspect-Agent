"""Structural baselines with duplicate multiplicity and v1 read compatibility."""
import json
from collections import Counter
from pathlib import Path


def finding_key(finding):
    if finding.get('fingerprint'):
        return ('v2', finding['rule_id'], finding['file'], finding['fingerprint'])
    return ('v1', finding['rule_id'], finding['file'], finding['line'])


def write_baseline(path: Path, findings: list[dict]):
    entries = [{k:f[k] for k in ('rule_id','file','line','fingerprint','scope') if k in f} for f in findings]
    path.write_text(json.dumps({'version':2,'findings':entries},ensure_ascii=False,indent=2)+'\n',encoding='utf-8')


def load_baseline(path: Path):
    try:
        data = json.loads(path.read_text(encoding='utf-8'))
    except (OSError, UnicodeError, ValueError) as exc:
        raise ValueError(f'baseline 无法读取：{exc}') from exc
    if not isinstance(data,dict) or data.get('version') not in (1,2) or not isinstance(data.get('findings'),list):
        raise ValueError('baseline 格式无效：需要 version=1/2 和 findings 数组')
    keys = Counter()
    for item in data['findings']:
        if (not isinstance(item,dict) or not isinstance(item.get('rule_id'),str)
                or not isinstance(item.get('file'),str) or type(item.get('line')) is not int or item['line']<0
                or ('fingerprint' in item and (not isinstance(item['fingerprint'],str) or not item['fingerprint']))):
            raise ValueError('baseline 包含无效发现项')
        if data['version']==1: item = {k:v for k,v in item.items() if k!='fingerprint'}
        keys[finding_key(item)] += 1
    return keys


def filter_baseline(findings, known):
    remaining=known.copy(); new=[]; suppressed=0
    for finding in findings:
        key=finding_key(finding)
        legacy=('v1',finding['rule_id'],finding['file'],finding['line'])
        if remaining[key]<=0 and remaining[legacy]>0: key=legacy
        if remaining[key]>0:
            remaining[key]-=1; suppressed+=1
        else: new.append(finding)
    return new,suppressed
