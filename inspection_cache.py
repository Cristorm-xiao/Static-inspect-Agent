"""Best-effort atomic JSON cache; no credentials or AST objects are stored."""
import hashlib
import json
import logging
import os
from pathlib import Path
import time
import uuid

logger = logging.getLogger(__name__)


def cache_key(value):
    return hashlib.sha256(json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':')).encode()).hexdigest()


class Cache:
    def __init__(self, root, enabled=True, refresh=False, ttl=86400):
        self.root=Path(root); self.enabled=enabled; self.refresh=refresh; self.ttl=ttl

    def get(self, namespace, key):
        if not self.enabled or self.refresh: return None
        try:
            path=self.root/namespace/(key+'.json')
            data=json.loads(path.read_text(encoding='utf-8'))
            if not isinstance(data,dict) or data.get('key')!=key or type(data.get('created')) not in (int,float): return None
            if not 0 <= time.time()-data['created'] <= self.ttl: return None
            return data.get('value')
        except (OSError,ValueError,TypeError): return None

    def put(self, namespace, key, value):
        if not self.enabled: return
        temp=None
        try:
            folder=self.root/namespace; folder.mkdir(parents=True,exist_ok=True)
            temp=folder/(key+'.'+uuid.uuid4().hex+'.tmp')
            temp.write_text(json.dumps({'key':key,'created':time.time(),'value':value},ensure_ascii=False),encoding='utf-8')
            os.replace(temp,folder/(key+'.json'))
        except OSError:
            logger.warning('缓存不可写，本次分析继续且不保存缓存')
        finally:
            if temp is not None:
                try: temp.unlink(missing_ok=True)
                except OSError: pass


def project_cache(state):
    cfg=state.get('config',{})
    return Cache(cfg.get('cache_dir') or Path(state['project_path'])/'.inspect-cache',
                 cfg.get('cache_enabled',True),cfg.get('refresh_cache',False),cfg.get('cache_ttl_seconds',86400))
