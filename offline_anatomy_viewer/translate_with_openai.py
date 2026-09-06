#!/usr/bin/env python3
"""Translate a language-pack template through an OpenAI-compatible endpoint.
English source is never changed. Complete translations are enabled automatically
by user preference; --require-review retains the manual publishing gate.
The legacy 'reviewed' flag means enabled, not a claim of medical review.
"""
import argparse,json,os,re,time,sys
from pathlib import Path
import html
from copy import deepcopy


def term_meanings(value):
    """Split synonym separators, but preserve parentheses, fractions and URLs."""
    parts, start, depth = [], 0, 0
    for match in re.finditer(r'[()\[\]{}]|;|/|\bhoặc\b', value, re.IGNORECASE):
        token = match.group()
        if token in ('(', '[', '{'): depth += 1
        elif token in (')', ']', '}'): depth = max(0, depth - 1)
        elif depth == 0:
            if token == '/' and (re.search(r'\d$', value[:match.start()]) and re.match(r'\d', value[match.end():]) or '://' in value):
                continue
            parts.append(value[start:match.start()].strip()); start = match.end()
    parts.append(value[start:].strip())
    return list(dict.fromkeys(p for p in parts if p))


def normalize_term(value, source=''):
    # The model ranks candidates first; the rendering convention keeps at most two.
    result = ' / '.join(term_meanings(re.sub(r'\s*;\s*', ' / ', value))[:2])
    # Source segment codes are identifiers, not additional candidate meanings.
    # Keep them even when an alias containing the code was ranked third.
    codes = list(dict.fromkeys(re.findall(r'\b[ACMPSTV][0-9]+[a-z]?\b', source)))
    missing = [code for code in codes if code not in result]
    if result and missing:
        result += ' (' + ', '.join(missing) + ')'
    return result


def apply_translations(template, cache, model, require_review=False):
    """Bind only exact source-key matches, without modifying the input template."""
    d = deepcopy(template)
    status = 'needs_review' if require_review else 'reviewed'
    for col in ('structures', 'filters', 'labels', 'texts'):
        for row in d.get(col, {}).values():
            tr = row.setdefault('translation', {})
            fs = row.setdefault('field_status', {})
            for field, src in row.get('source', {}).items():
                if col == 'structures' and field in ('description_html', 'sources_html'):
                    continue
                val = cache.get(f'{col}|{field}|{src}', '') if src.strip() else ''
                tr[field] = (normalize_term(val, src) if field in ('name', 'text') else val) if isinstance(val, str) else ''
                fs[field] = status if tr[field].strip() else 'draft'
            if col == 'structures':
                # Rebuild markup from escaped translated text, preserving English
                # source HTML and bibliographic citations in the source record.
                if 'description_html' in row['source']:
                    text = tr.get('description_text', '')
                    tr['description_html'] = '<p>' + html.escape(text) + '</p>' if text.strip() and row['source']['description_html'].strip() else ''
                    fs['description_html'] = status if tr['description_html'] else 'draft'
                if 'sources_html' in row['source']:
                    tr['sources_html'] = row['source']['sources_html']
                    fs['sources_html'] = status if tr['sources_html'].strip() else 'draft'
            row['status'] = status if any(v.strip() for v in tr.values()) else 'draft'
    d['translation_meta'] = {
        'engine': 'openai-compatible', 'model': model,
        'approval_mode': 'manual' if require_review else 'user_requested_automatic',
        'requires_medical_review': True, 'medical_review_performed': False,
        'source_unchanged': True, 'references_preserved': True,
        'term_policy': 'maximum_two_ranked_meanings_slash_separator',
    }
    return d

def batched(items, limit):
    cur=[]; n=0
    for x in items:
        size=len(x.get('source',''))
        if cur and n+size>limit: yield cur; cur=[]; n=0
        cur.append(x); n+=size
    if cur: yield cur

def call(session,url,key,model,items,purpose,retries=3):
    glossary='Use standard Vietnamese anatomical/radiological terminology (e.g. gyrus=hồi não, sulcus=rãnh, fissure=khe, lobe=thùy, ventricle=não thất, cistern=bể, artery=động mạch, vein=tĩnh mạch, sinus=xoang tĩnh mạch, dura mater=màng cứng, arachnoid mater=màng nhện, pia mater=màng mềm, white matter=chất trắng, gray matter=chất xám, brainstem=thân não, cerebellum=tiểu não). Preserve proper names, Latin terms, numbers, laterality, abbreviations, URLs, HTML tags and placeholders exactly.'
    prompt=f'''Translate the following medical anatomy {purpose} from English to Vietnamese. Return ONLY strict JSON exactly {{"translations":{{"id":"translation"}}}} with every id included and no extra ids. {glossary} Do not explain or transliterate names unnecessarily.
For a term with several possible translations, select ONLY the two meanings closest to the source in its neuroanatomical context, best first, separated by " / ". If there is one precise meaning, return one; do not invent a second meaning. Do not use semicolon or the word "hoặc" to separate alternatives. Preserve grouped distinct structures and anatomical qualifiers; do not confuse them with synonyms. If the source lists more than two DISTINCT structures, preserve them all in a comma-separated grouped phrase; the two-meaning cap applies only to synonyms. Short wrapped fragments must remain fragments, not invented full structure names. Descriptions must be translated in full, not summarized or shortened to two meanings. Bibliographic citations, Latin and URLs remain original.'''
    payload={'model':model,'temperature':0.1,'messages':[{'role':'system','content':prompt},{'role':'user','content':json.dumps({'items':items},ensure_ascii=False)}]}
    for attempt in range(retries):
        try:
            r=session.post(url,headers={'Authorization':'Bearer '+key,'Content-Type':'application/json'},json=payload,timeout=180,proxies={'http':None,'https':None})
            r.raise_for_status(); content=r.json()['choices'][0]['message']['content'].strip()
            content=re.sub(r'^```(?:json)?\s*|\s*```$','',content,flags=re.I|re.S).strip()
            obj=json.loads(content); tr=obj.get('translations')
            if not isinstance(tr,dict) or set(tr)!={str(x['id']) for x in items} or any(not isinstance(v,str) or not v.strip() for v in tr.values()): raise ValueError('invalid translation JSON')
            return tr
        except Exception as e:
            if attempt==retries-1: raise
            time.sleep(2*(attempt+1))

def main():
    import requests  # Optional dependency; the viewer itself needs no API client.
    ap=argparse.ArgumentParser(); ap.add_argument('--template',required=True); ap.add_argument('--output',required=True); ap.add_argument('--checkpoint',required=True); ap.add_argument('--base-url',default=os.environ.get('TRANSLATOR_BASE_URL')); ap.add_argument('--api-key',default=os.environ.get('TRANSLATOR_API_KEY')); ap.add_argument('--model',default=os.environ.get('TRANSLATOR_MODEL','gemini-3.8-flash-high')); ap.add_argument('--require-review',action='store_true',help='Keep generated translations disabled pending manual approval'); args=ap.parse_args()
    if not args.base_url or not args.api_key: raise SystemExit('TRANSLATOR_BASE_URL and TRANSLATOR_API_KEY required')
    d=json.load(open(args.template,encoding='utf-8')); cp=Path(args.checkpoint); cache=json.load(open(cp,encoding='utf-8')) if cp.exists() else {}
    s=requests.Session(); endpoint=args.base_url.rstrip('/')+'/chat/completions'
    jobs=[]
    # Translate unique source values; map back to occurrences.
    for col in ('structures','filters','labels','texts'):
      vals={}
      for key,row in d.get(col,{}).items():
        for field,src in row.get('source',{}).items():
          if not isinstance(src,str) or not src.strip() or (col=='structures' and field in ('description_html','sources_html')): continue
          vals[(field,src)]=None
      for (field,src) in vals:
        ck=f'{col}|{field}|{src}'
        if ck not in cache: jobs.append((col,field,src,ck))
    print(f'pending={len(jobs)} cached={len(cache)}',flush=True)
    for col in ('structures','filters','labels','texts'):
      group=[j for j in jobs if j[0]==col]
      for field in sorted(set(j[1] for j in group)):
        items=[{'id':str(i),'source':j[2]} for i,j in enumerate([x for x in group if x[1]==field])]
        refs=[x for x in group if x[1]==field]
        for b in batched(items,6000 if field=='text' else 30000):
          print(f'translating {col}.{field} {len(b)}',flush=True)
          tr=call(s,endpoint,args.api_key,args.model,b,f'{col} {field}')
          for x in b:
            cache[refs[int(x['id'])][3]]=tr[str(x['id'])]
          cp.parent.mkdir(parents=True,exist_ok=True); cp.write_text(json.dumps(cache,ensure_ascii=False),encoding='utf-8')
    d=apply_translations(d,cache,args.model,args.require_review)
    Path(args.output).parent.mkdir(parents=True,exist_ok=True); Path(args.output).write_text(json.dumps(d,ensure_ascii=False,indent=2),encoding='utf-8')
    print(f'wrote={args.output}',flush=True)
if __name__=='__main__': main()
