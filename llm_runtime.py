"""Bounded model scheduling, cache reuse and one-round source lookup."""
import asyncio
import json
from collections import Counter
import time
from openai import APIError
from config import DEFAULTS
from inspection_cache import cache_key
from llm_protocol import SYSTEM, PROMPT_VERSION, build_prompt, estimate_tokens, parse_response, resolve_context


def make_batches(candidates, cfg):
    batches=[]; oversized=[]; current=[]
    for cand in candidates:
        if estimate_tokens(SYSTEM+build_prompt([cand]))+cfg['llm_output_tokens']>cfg['llm_batch_tokens']:
            oversized.append(cand); continue
        proposed=current+[cand]
        if current and (len(proposed)>cfg['llm_batch_size'] or
                estimate_tokens(SYSTEM+build_prompt(proposed))+cfg['llm_output_tokens']>cfg['llm_batch_tokens']):
            batches.append(current); current=[]
        current.append(cand)
    if current: batches.append(current)
    return batches,oversized


async def analyze(api_key, base_url, model, candidates, source_index, settings, cache, client_factory):
    cfg={**DEFAULTS,**settings}
    stats={'candidates':len(candidates),'requests':0,'cache_hits':0,'estimated_input_reserved':0,
           'prompt_tokens':0,'completion_tokens':0,'usage_missing_calls':0,'rejected_items':0,
           'context_rounds':0,'returned_models':[]}
    outcomes=[]; findings=[]
    def function_key(c):
        return cache_key(['function-cache-v1',PROMPT_VERSION,SYSTEM,base_url,model,
            {k:v for k,v in cfg.items() if k.startswith('llm_')},c,source_index.get(c['file'],[])])
    pending=[]
    for cand in candidates:
        cached=cache.get('llm',function_key(cand)) if cache else None
        parsed=parse_response(cached['raw'],[cand]) if isinstance(cached,dict) and isinstance(cached.get('raw'),str) else None
        if parsed and parsed['valid'] and not parsed['requests'] and not parsed['rejected']:
            stats['cache_hits']+=1; findings.extend(parsed['findings'])
            outcomes.append({'function_id':cand['function_id'],'status':'cache_hit','findings':len(parsed['findings'])})
        else: pending.append(cand)
    batches,oversized=make_batches(pending,cfg)
    for cand in oversized:
        outcomes.append({'function_id':cand['function_id'],'status':'skipped_oversize'})
    semaphore=asyncio.Semaphore(cfg['llm_concurrency'])
    started=time.perf_counter()

    async with client_factory(api_key=api_key,base_url=base_url,timeout=30.0,max_retries=2) as client:
        async def request(batch, context=''):
            prompt=build_prompt(batch,context)
            reservation=estimate_tokens(SYSTEM+prompt)
            async with semaphore:
                # No await between checking and reserving shared counters.
                if (stats['requests']>=cfg['llm_max_requests'] or
                        stats['estimated_input_reserved']+reservation>cfg['llm_input_budget']):
                    return None,'skipped_budget'
                if reservation+cfg['llm_output_tokens']>cfg['llm_batch_tokens']:
                    return None,'skipped_oversize'
                stats['requests']+=1; stats['estimated_input_reserved']+=reservation
                try:
                    response=await client.chat.completions.create(model=model,
                        messages=[{'role':'system','content':SYSTEM},{'role':'user','content':prompt}],
                        temperature=0,max_tokens=cfg['llm_output_tokens'])
                    usage=getattr(response,'usage',None)
                    if usage:
                        stats['prompt_tokens']+=getattr(usage,'prompt_tokens',0) or 0
                        stats['completion_tokens']+=getattr(usage,'completion_tokens',0) or 0
                    else: stats['usage_missing_calls']+=1
                    returned=getattr(response,'model',model)
                    if returned not in stats['returned_models']: stats['returned_models'].append(returned)
                    choice=response.choices[0]
                    if getattr(choice,'finish_reason','stop')!='stop': return None,'truncated'
                    raw=choice.message.content or ''
                    parsed=parse_response(raw,batch)
                    return (raw,parsed),'completed' if parsed['valid'] else 'invalid_response'
                except (APIError,IndexError,AttributeError,TypeError,ValueError):
                    # Error bodies may contain credentials/provider data. Persist only state.
                    return None,'failed'

        async def run_batch(batch):
            parsed=None; status='completed'; raw=None
            if parsed is None:
                response,status=await request(batch)
                if response: raw,parsed=response
                if parsed and parsed['valid']:
                    stats['rejected_items']+=parsed['rejected']
                    if parsed['requests']:
                        context,resolved=resolve_context(parsed['requests'],batch,source_index,cfg['llm_context_chars'])
                        requested={(r['function_id'],s) for r in parsed['requests'] for s in r['symbols']}
                        if context:
                            stats['context_rounds']+=1
                            followup,follow_status=await request(batch,context)
                            if followup and followup[1]['valid']:
                                raw,parsed=followup
                                stats['rejected_items']+=parsed['rejected']
                                status='completed'
                                if requested-set(resolved) or parsed['requests']: status='context_unresolved'
                            else:
                                status='context_'+follow_status
                        else: status='context_unresolved'
                    if parsed['rejected'] and status=='completed': status='partial_invalid'
                    if status=='completed' and cache:
                        for cand in batch:
                            items=[{'function_id':f['function_id'],'category':f['smell'],
                                    'evidence_lines':f['evidence_lines'],'evidence':f['evidence'],
                                    'impact':f['reason'],'suggestion':f['suggestion'],'needs_context':False}
                                   for f in parsed['findings'] if f['function_id']==cand['function_id']]
                            cache.put('llm',function_key(cand),{'raw':json.dumps({'findings':items,'context_requests':[]},ensure_ascii=False)})
            if parsed and parsed['valid']: findings.extend(parsed['findings'])
            for cand in batch:
                count=sum(f['function_id']==cand['function_id'] for f in (parsed or {}).get('findings',[]))
                outcomes.append({'function_id':cand['function_id'],'status':status,'findings':count})

        # A fixed number of workers avoids allocating a task per file/batch.
        queue=asyncio.Queue()
        for batch in batches: queue.put_nowait(batch)
        async def worker():
            while True:
                try: batch=queue.get_nowait()
                except asyncio.QueueEmpty: break
                try: await run_batch(batch)
                finally: queue.task_done()
        await asyncio.gather(*(worker() for _ in range(min(cfg['llm_concurrency'],len(batches)))))
    findings.sort(key=lambda f:(f['file'],f['line'],f['smell']))
    outcomes.sort(key=lambda o:o['function_id'])
    stats['statuses']=dict(Counter(o['status'] for o in outcomes))
    stats['completed_functions']=sum(o['status'] in ('completed','cache_hit') for o in outcomes)
    stats['status']='completed' if stats['completed_functions']==len(candidates) else 'partial'
    stats['seconds']=time.perf_counter()-started
    return {'findings':findings,'outcomes':outcomes,'stats':stats}
