"""Select source-grounded candidates and run the bounded LLM workflow."""
import asyncio
import logging
import os
from pathlib import Path
from dotenv import load_dotenv
from openai import AsyncOpenAI
from config import LONG_FUNC_THRESHOLD, LLM_BATCH_SIZE
from inspection_cache import project_cache
from llm_protocol import build_prompt, parse_response
from llm_runtime import analyze
from source_analysis import parse_bytes, function_records, select_candidates

logger=logging.getLogger(__name__)


def extract_candidates(file_path, threshold=LONG_FUNC_THRESHOLD):
    try:
        source,tree=parse_bytes(Path(file_path).read_bytes(),file_path)
        return select_candidates(function_records(source,tree),threshold)
    except (SyntaxError,UnicodeError,OSError,LookupError):
        return []


def llm_analyze_node(state):
    candidates=state.get('candidates')
    cfg=state.get('config',{})
    def skipped(status):
        return {'llm_findings':[], 'llm_stats':{'status':status,'candidates':len(candidates or []),'requests':0,'completed_functions':0},
                'llm_outcomes':[{'function_id':c['function_id'],'status':status} for c in (candidates or [])]}
    if not state.get('enable_llm',True): return skipped('disabled')
    load_dotenv()
    key=os.environ.get('DEEPSEEK_API_KEY','')
    if not key or '在此填入' in key: return skipped('unconfigured')
    # Direct callers can still use this node; the graph normally supplies shared AST-derived records.
    source_index=state.get('source_index',{})
    if candidates is None:
        candidates=[]; root=Path(state['project_path']).resolve()
        for filename in state.get('py_files',[]):
            relative=Path(filename).relative_to(root).as_posix()
            try:
                source,tree=parse_bytes(Path(filename).read_bytes(),filename)
                records=function_records(source,tree); source_index[relative]=records
                for c in select_candidates(records,cfg.get('long_func_threshold',LONG_FUNC_THRESHOLD)):
                    candidates.append({**c,'file':relative,'function_id':relative+':'+c['identity']})
            except (SyntaxError,UnicodeError,OSError,LookupError): continue
    if not candidates: return skipped('no_candidates')
    settings={'llm_batch_size':LLM_BATCH_SIZE,**cfg}
    result=asyncio.run(analyze(key,os.environ.get('DEEPSEEK_BASE_URL','https://api.deepseek.com/v1'),
        os.environ.get('DEEPSEEK_MODEL','deepseek-chat'),candidates,source_index,settings,project_cache(state),AsyncOpenAI))
    return {'llm_findings':result['findings'],'llm_stats':result['stats'],'llm_outcomes':result['outcomes']}
