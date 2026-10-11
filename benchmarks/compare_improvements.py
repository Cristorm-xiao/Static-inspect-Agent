"""Reproduce offline before/after timings; --live is an explicit paid evaluation."""
import argparse
import asyncio
import ast
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import statistics
import subprocess
import sys
import time
import zipfile

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from source_analysis import parse_bytes, function_records, select_candidates
from scanner import collect_py_files


def read(p): return json.loads(p.read_text(encoding='utf-8'))
def save(p,value):
    p.parent.mkdir(parents=True,exist_ok=True)
    p.write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
def sha(raw): return hashlib.sha256(raw).hexdigest()


def prepare(output,corpus,prepare_only=False,heldout_seed='holdout-20261010',exclude_ids=None):
    output.mkdir(parents=True,exist_ok=False)
    manifest=read(ROOT/'benchmarks/corpus.json')
    previous=read(ROOT/'benchmarks/results/2026-10-10-llm/samples.json')
    known={s['sample_id'] for s in previous}
    selected=[]; heldout=[]; context={}; metrics=[]
    for project in manifest:
        repo=corpus/project['name']; source_root=repo/project['source']
        commit=subprocess.check_output(['git','-C',str(repo),'rev-parse','HEAD'],text=True).strip()
        assert commit==project['commit']
        assert not subprocess.check_output(['git','-C',str(repo),'status','--porcelain'],text=True).strip()
        old=[]; new=[]; file_records={}
        for filename in collect_py_files(source_root):
            relative=Path(filename).relative_to(source_root).as_posix()
            source,tree=parse_bytes(Path(filename).read_bytes(),filename)
            records=function_records(source,tree)
            file_records[relative]=records
            new.extend((relative,c) for c in select_candidates(records))
            for c in records:
                if c['end_line']-c['line']+1>=30: old.append((relative,c))
        metrics.append({'project':project['name'],'old_candidates':len(old),'new_candidates':len(new),
            'old_source_bytes':sum(len(c['source'].encode()) for _,c in old),
            'new_source_bytes':sum(len(c['source'].encode()) for _,c in new)})
        pool=[]
        for relative,c in old:
            sample_id=f"{project['name']}:{relative}:{c['line']}:{c['name']}"
            item={**c,'sample_id':sample_id,'project':project['name'],'commit':commit,
                  'file':project['name']+'/'+relative,'function_id':sample_id,
                  'new_selector_accepts':any(r==relative and n['line']<=c['line'] and n['end_line']>=c['end_line'] for r,n in new)}
            if sample_id in known: selected.append(item)
            elif sample_id not in (exclude_ids or set()): pool.append(item)
        chosen=sorted(pool,key=lambda c:sha((heldout_seed+'|'+c['sample_id']).encode()))[:2]
        heldout.extend(chosen)
        for item in [c for c in selected+chosen if c['project']==project['name']]:
            rel=item['file'].split('/',1)[1]; context[item['file']]=file_records[rel]
    order={s['sample_id']:i for i,s in enumerate(previous)}
    selected.sort(key=lambda c:order[c['sample_id']])
    controls=[]; control_metrics=[]
    for c in read(ROOT/'benchmarks/llm_controls.json')['cases']:
        source,tree=parse_bytes(c['source'].encode(),'controls.py'); records=function_records(source,tree)
        accepted=select_candidates(records)
        control_metrics.append({'id':c['id'],'positive':c['positive'],'selected':bool(accepted)})
        controls.append({**records[0],'function_id':c['id'],'sample_id':c['id'],'file':'controls/'+c['id']+'.py',
                         'group':'control','new_selector_accepts':bool(accepted)})
    save(output/'candidate-metrics.json',metrics)
    save(output/'control-selection.json',control_metrics)
    save(output/'live-samples.json',[{**s,'group':'original'} for s in selected]+controls+[{**s,'group':'heldout'} for s in heldout])
    save(output/'context-index.json',context)
    save(output/'provenance.json',{'prepared_at':datetime.now(timezone.utc).isoformat(),
        'product_sha256':{f.name:sha(f.read_bytes()) for f in ROOT.glob('*.py') if not f.name.startswith('test_')},
        'corpus':manifest,'heldout_selection':f'2 per project, smallest SHA256({heldout_seed}|sample_id), excluding original25 and supplied prior heldout IDs',
        'excluded_prior_ids':sorted(exclude_ids or []),
        'quality_note':'Same original25 isolates prompt change; controls8 and heldout10 also sent regardless of new selector. No human gold quality labels.'})
    if prepare_only:
        print('Frozen samples and product provenance prepared; no timings or paid calls.',flush=True)
        return
    before=output/'before-tool'; before.mkdir()
    with zipfile.ZipFile(ROOT/'benchmarks/results/2026-10-10-llm/evaluated-source.zip') as z:
        for name in z.namelist():
            if '/' not in name and name.endswith('.py'): (before/name).write_bytes(z.read(name))
    env=os.environ.copy(); env['DEEPSEEK_API_KEY']=''; env['PYTHONIOENCODING']='utf-8'
    runs=[]
    for project in manifest:
        target=corpus/project['name']/project['source']
        for mode,repeats in [('before',3),('after_no_cache',3),('after_cold',1),('after_warm',3)]:
            for repeat in range(repeats):
                folder=before if mode=='before' else ROOT
                command=[sys.executable,'-B','-m','main',str(target),'--no-llm','--format','json']
                if mode=='after_no_cache': command+=['--no-cache']
                elif mode.startswith('after'): command+=['--cache-dir',str(output/'cache'/project['name'])]
                start=time.perf_counter(); result=subprocess.run(command,cwd=folder,env=env,capture_output=True)
                seconds=time.perf_counter()-start
                assert result.returncode in (0,1),(project['name'],mode,result.stderr.decode(errors='replace'))
                data=json.loads(result.stdout.decode('utf-8'))
                run={'project':project['name'],'mode':mode,'repeat':repeat,'seconds':seconds,'exit_code':result.returncode,
                     'files':data['files_scanned'],'findings':len(data['rule_findings']),'scan_stats':data.get('scan_stats')}
                runs.append(run); save(output/'runs'/f"{project['name']}-{mode}-{repeat}.json",{'measurement':run,'report':data})
            print(project['name'],mode,round(statistics.median(r['seconds'] for r in runs if r['project']==project['name'] and r['mode']==mode),3),flush=True)
    save(output/'timings.json',runs)
    print('Offline comparison complete.',flush=True)


def live(output):
    from dotenv import dotenv_values
    from openai import AsyncOpenAI
    from llm_runtime import analyze
    config=dotenv_values(ROOT/'.env'); key=config.get('DEEPSEEK_API_KEY')
    base=config.get('DEEPSEEK_BASE_URL','https://api.deepseek.com/v1'); model=config.get('DEEPSEEK_MODEL','deepseek-chat')
    assert key and base.rstrip('/') in ('https://api.deepseek.com/v1','https://api.deepseek.com')
    expected=read(output/'provenance.json')['product_sha256']
    assert all(sha((ROOT/n).read_bytes())==v for n,v in expected.items()),'Product changed since prepare; prepare a fresh output.'
    if (output/'live-calls').exists(): raise ValueError('Use fresh output to avoid overwriting paid call evidence.')
    (output/'live-calls').mkdir(); records=[]
    def client_factory(**kwargs):
        client=AsyncOpenAI(**kwargs); actual=client.chat.completions.create
        async def create(**request):
            rec={'number':len(records),'request':request}; records.append(rec); start=time.perf_counter()
            try:
                response=await actual(**request)
                rec.update({'status':'ok','returned_model':response.model,'usage':response.usage.model_dump() if response.usage else None,
                            'content':response.choices[0].message.content,'finish_reason':response.choices[0].finish_reason})
                return response
            except Exception as exc:
                rec.update({'status':'failed','error_type':type(exc).__name__}); raise
            finally:
                rec['seconds']=time.perf_counter()-start
                assert key not in json.dumps(rec,ensure_ascii=False)
                save(output/'live-calls'/f"{rec['number']:02d}.json",rec)
                print('model call',rec['number'],rec['status'],round(rec['seconds'],3),flush=True)
        client.chat.completions.create=create
        return client
    settings={'llm_concurrency':2,'llm_max_requests':12,'llm_input_budget':200000,'llm_output_tokens':2000,'llm_batch_tokens':24000}
    save(output/'live-settings.json',settings)
    result=asyncio.run(analyze(key,base,model,read(output/'live-samples.json'),read(output/'context-index.json'),settings,None,client_factory))
    save(output/'live-result.json',result)
    print(json.dumps(result['stats'],ensure_ascii=False),flush=True)


if __name__=='__main__':
    parser=argparse.ArgumentParser(); parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--corpus',type=Path,default=ROOT.parent/'validation-2026-10-09/corpus')
    parser.add_argument('--live',action='store_true')
    parser.add_argument('--prepare-only',action='store_true')
    parser.add_argument('--heldout-seed',default='holdout-20261010')
    parser.add_argument('--exclude-heldout',type=Path,help='Prior live-samples.json; excludes its heldout IDs')
    args=parser.parse_args()
    if args.live: live(args.output.resolve())
    else:
        excluded={c['sample_id'] for c in read(args.exclude_heldout) if c['group']=='heldout'} if args.exclude_heldout else set()
        prepare(args.output.resolve(),args.corpus.resolve(),args.prepare_only,args.heldout_seed,excluded)
