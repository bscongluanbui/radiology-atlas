"""Resumable, bounded-concurrency region translation. Credentials stay in env."""
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
import argparse
import hashlib
import json
import os
import time

from export_language_template import build_template, source_files
from server import AnatomyRepository
from sync_language_pack import validate
from translate_with_openai import apply_translations, call, normalize_term, term_meanings

COLLECTIONS = ('structures', 'filters', 'labels', 'texts')
POLICY = 'brain-vi-two-meanings-v1'


def read(path):
    return json.loads(path.read_text(encoding='utf-8'))


def write(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + '.tmp')
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')
    tmp.replace(path)


def hash_file(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def token(kind, source):
    return hashlib.sha256((kind+'\0'+source).encode('utf-8')).hexdigest()


def fields(pack):
    for col in COLLECTIONS:
        for key, row in pack[col].items():
            for field, source in row['source'].items():
                if not source.strip() or field in ('description_html','sources_html'):
                    continue
                kind = 'description' if field == 'description_text' else 'term'
                yield col, key, row, field, source, kind, token(kind, source)


def prepare(args):
    repo = AnatomyRepository(args.data_root)
    folders = sorted(p for p in (repo.modules_root/args.region).iterdir() if p.is_dir())
    manifest_path = args.work_dir/'source_manifest.json'
    if manifest_path.exists():
        return read(manifest_path)
    result = {'region': args.region, 'modules': {}, 'source_hashes': {}}
    for folder in folders:
        key = args.region+'/'+folder.name
        files = source_files(repo, key)
        hashes = {str(p.resolve()): hash_file(p) for p in files}
        pack = build_template(repo, key, 'vi', True)
        validate(pack, template=True)
        if hashes != {str(p.resolve()): hash_file(p) for p in files}:
            raise ValueError('Source changed during export: '+key)
        if not pack['structures'] or not pack['labels']:
            raise ValueError('Incomplete module source: '+key)
        path = args.work_dir/'templates'/(folder.name+'.json')
        write(path, pack)
        result['modules'][key] = {'template':str(path.resolve()), 'sha256':hash_file(path),
                                'counts':{c:len(pack[c]) for c in COLLECTIONS}}
        result['source_hashes'].update(hashes)
        print('EXPORTED='+key+'; '+json.dumps(result['modules'][key]['counts']), flush=True)
    write(manifest_path, result)
    return result


def batches(items):
    batch, size = [], 0
    for item in items:
        limit = 16000 if item['kind']=='description' else 7000
        max_items = 12 if item['kind']=='description' else 100
        n = len(item['source'])
        if batch and (size+n>limit or len(batch)>=max_items):
            yield batch; batch=[]; size=0
        batch.append(item); size+=n
    if batch: yield batch


def request_batch(batch, args):
    import requests
    session = requests.Session()
    payload = [{'id':item['id'], 'source':item['source'], 'context':item['context']} for item in batch]
    try:
        return call(session,args.base_url.rstrip('/')+'/chat/completions',args.api_key,args.model,
                    payload,'BRAIN '+batch[0]['kind'],retries=2)
    except Exception:
        if len(batch)==1: raise
        half=len(batch)//2
        return {**request_batch(batch[:half],args),**request_batch(batch[half:],args)}
    finally:
        session.close()


def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--data-root',type=Path,required=True)
    ap.add_argument('--region',default='BRAIN')
    ap.add_argument('--work-dir',type=Path,required=True)
    ap.add_argument('--output-root',type=Path,required=True)
    ap.add_argument('--mirror-root',type=Path)
    ap.add_argument('--workers',type=int,default=4)
    ap.add_argument('--prepare-only',action='store_true')
    ap.add_argument('--base-url',default=os.environ.get('TRANSLATOR_BASE_URL'))
    ap.add_argument('--api-key',default=os.environ.get('TRANSLATOR_API_KEY'))
    ap.add_argument('--model',default=os.environ.get('TRANSLATOR_MODEL','gemini-3.8-flash-high'))
    args=ap.parse_args()
    if args.region!='BRAIN': raise ValueError('This terminology profile is restricted to region BRAIN')
    manifest=prepare(args)
    if args.prepare_only: return
    cp=args.work_dir/'checkpoint.json'
    cache=read(cp) if cp.exists() else {'policy':POLICY,'model':args.model,'entries':{}}
    if cache['policy']!=POLICY or cache['model']!=args.model: raise ValueError('Checkpoint model/policy mismatch')
    entries=cache['entries']; jobs={}; seeded=0
    for key, spec in manifest['modules'].items():
        path=Path(spec['template'])
        if hash_file(path)!=spec['sha256']: raise ValueError('Template hash mismatch')
        pack=read(path)
        oldpath=args.output_root/(key+'.json')
        old=read(oldpath) if oldpath.exists() else {}
        for col,rkey,row,field,source,kind,tid in fields(pack):
            prior=old.get(col,{}).get(rkey,{})
            value=prior.get('translation',{}).get(field,'')
            status=prior.get('field_status',{}).get(field,prior.get('status'))
            if tid not in entries and value.strip() and prior.get('source',{}).get(field)==source and prior.get('binding')==row.get('binding') and status=='reviewed':
                # Existing >2 alternatives need the model to rank them, not a blind truncation.
                if kind!='term' or len(term_meanings(value))<=2:
                    entries[tid]=normalize_term(value, source) if kind=='term' else value
                    seeded+=1
            jobs.setdefault(tid, {'id':tid,'source':source,'kind':kind,
                'context': key+' / '+col+'.'+field+'; '+row['source'].get('name','')})
    write(cp,cache)
    pending=[j for tid,j in jobs.items() if not entries.get(tid,'').strip()]
    if pending and (not args.base_url or not args.api_key):
        raise ValueError('Set TRANSLATOR_BASE_URL and TRANSLATOR_API_KEY for pending requests')
    queue=[]
    for kind in ('term','description'):
        queue.extend(batches([j for j in pending if j['kind']==kind]))
    print(f'PLAN: modules={len(manifest["modules"])}; unique={len(jobs)}; seeded={seeded}; cached={len(entries)}; pending={len(pending)}; batches={len(queue)}; workers={args.workers}',flush=True)
    failures=[]; completed=0; started=time.monotonic()
    with ThreadPoolExecutor(max_workers=max(1,min(args.workers,6))) as pool:
        futures={pool.submit(request_batch,b,args):b for b in queue}
        for future in as_completed(futures):
            batch=futures[future]
            try:
                result=future.result()
                for item in batch:
                    value=result[item['id']]
                    entries[item['id']]=normalize_term(value, item['source']) if item['kind']=='term' else value
                write(cp,cache);completed+=len(batch)
                print(f'TRANSLATED={completed}/{len(pending)}; batch={batch[0]["kind"]}:{len(batch)}; elapsed_s={int(time.monotonic()-started)}',flush=True)
            except Exception as e:
                failures.extend(item['id'] for item in batch)
                print('BATCH_FAILED='+type(e).__name__+'; items='+str(len(batch)),flush=True)
    missing=[tid for tid in jobs if not entries.get(tid,'').strip()]
    write(args.work_dir/'failures.json',{'missing':missing})
    if missing: raise RuntimeError(f'{len(missing)} texts pending; rerun to resume; published packs were not changed')
    for path,expected in manifest['source_hashes'].items():
        if hash_file(Path(path))!=expected: raise ValueError('Source changed before publish: '+path)
    report={'modules':{},'unique':len(jobs),'missing':0,'source_hashes_unchanged':True,'model':args.model,'policy':POLICY}
    for key,spec in manifest['modules'].items():
        template=read(Path(spec['template'])); mapped={}
        for col,rkey,row,field,source,kind,tid in fields(template): mapped[f'{col}|{field}|{source}']=entries[tid]
        pack=apply_translations(template,mapped,args.model)
        validate(pack)
        count=0
        for col in COLLECTIONS:
            for rkey,row in pack[col].items():
                assert row['source']==template[col][rkey]['source'] and row.get('binding')==template[col][rkey].get('binding')
                for field,src in row['source'].items():
                    if src.strip():
                        assert row['translation'].get(field,'').strip() and row['field_status'][field]=='reviewed', (key,rkey,field)
                        count+=1
                    if field in ('name','text'):
                        assert ';' not in row['translation'].get(field,'')
        out=args.work_dir/'packs'/(key+'.json'); write(out,pack)
        report['modules'][key]={'counts':spec['counts'],'enabled_fields':count,'sha256':hash_file(out)}
    # Publish only after all source bindings and all output packs have passed.
    for key in manifest['modules']:
        raw=(args.work_dir/'packs'/(key+'.json')).read_bytes()
        for base in [args.output_root]+([args.mirror_root] if args.mirror_root else []):
            dst=base/(key+'.json');dst.parent.mkdir(parents=True,exist_ok=True)
            tmp=dst.with_suffix('.json.tmp');tmp.write_bytes(raw);tmp.replace(dst)
        print('PUBLISHED='+key,flush=True)
    write(args.work_dir/'report.json',report)
    print('REGION_TRANSLATION=PASS; modules='+str(len(manifest['modules']))+'; missing=0; original_source_hashes=PASS',flush=True)


if __name__=='__main__': main()
