"""Per-slice native overlay PNGs, strict identity/geometry validation and resume.

Masks are GROUP/LAYER overlays, not per-structure segmentations. A layer may be
shared by multiple filter IDs. Do not infer structure boundaries from colours.
"""
from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import math
import re
import struct
import zlib
from datetime import datetime, timezone
from pathlib import Path

SCHEMA_VERSION = 1
CAPTURE_JS = Path(__file__).with_suffix('.js').read_text(encoding='utf-8')
RUNTIME_INIT_JS = Path(__file__).with_name('overlay_runtime.js').read_text(encoding='utf-8')


def read_json(path, default=None):
    try:
        return json.loads(Path(path).read_text(encoding='utf-8'))
    except (OSError, ValueError, TypeError):
        return default


def atomic_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + '.pending')
    tmp.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')
    tmp.replace(path)


def sha(value):
    return hashlib.sha256(value).hexdigest()


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':')).encode('utf-8')


def layer_names(value):
    return sorted({x.strip() for x in str(value or '').split(',') if x.strip()})


def range_contains(value, order):
    if not isinstance(value, str) or not value.strip():
        raise ValueError('OVERLAY_RANGE_UNRESOLVED')
    ranges = []
    for item in value.split(','):
        m = re.fullmatch(r'\s*(\d+)(?:\s*-\s*(\d+))?\s*', item)
        if not m:
            raise ValueError('OVERLAY_RANGE_INVALID')
        low, high = int(m[1]), int(m[2] or m[1])
        if low > high:
            raise ValueError('OVERLAY_RANGE_INVALID')
        ranges.append((low, high))
    return any(low <= order <= high for low, high in ranges)


def load_overlay_plan(module):
    normal = Path(module) / 'normalised'
    overlays = read_json(normal / 'overlays.json')
    filters = read_json(normal / 'filters.json', [])
    slices = read_json(normal / 'slices.json', [])
    if not isinstance(overlays, list) or not all(isinstance(r, dict) for r in overlays):
        return {'error':'OVERLAY_DESCRIPTORS_MISSING_OR_INVALID', 'descriptors':[], 'slices':{}, 'filters':[]}
    if not isinstance(slices,list):
        return {'error':'OVERLAY_SLICE_METADATA_INVALID', 'descriptors':overlays, 'slices':{}, 'filters':[]}
    return {'descriptors':overlays, 'filters':filters if isinstance(filters,list) else [],
            'slices':{str(r.get('id')):r for r in slices if isinstance(r,dict)}}


def expected_overlay(plan, record):
    if plan.get('error'):
        return {'error':plan['error'], 'layers':[], 'plan_sha256':None}
    meta = record.get('slice') or {}
    sid = str(meta.get('active_id') or meta.get('id') or '')
    if not plan['descriptors']:
        return {'layers':[], 'plan_sha256':sha(b'no-overlay-descriptors')}
    row = plan['slices'].get(sid)
    if not row or str(row.get('series_id')) != str((record.get('series') or {}).get('id')):
        return {'error':'OVERLAY_SLICE_SERIES_MISMATCH','layers':[], 'plan_sha256':None}
    try:
        order = int(row['sort_order'])
        layers = {}
        for desc in plan['descriptors']:
            name = desc.get('layer')
            if not isinstance(name,str) or not name.strip():
                raise ValueError('OVERLAY_LAYER_INVALID')
            if range_contains(desc.get('range'), order):
                if name in layers:
                    raise ValueError('OVERLAY_DUPLICATE_DESCRIPTOR')
                layers[name] = {'layer':name,'filter_ids':sorted({str(f['id']) for f in plan['filters']
                    if isinstance(f,dict) and f.get('id') is not None and name in layer_names(f.get('layer'))}),
                    'range':desc['range']}
        entries = [layers[k] for k in sorted(layers)]
        return {'slice_id':sid,'sort_order':order, 'series_id':str(row['series_id']), 'layers':entries,
                'plan_sha256':sha(canonical([row,entries]))}
    except (ValueError, KeyError, TypeError) as exc:
        return {'error':str(exc),'layers':[],'plan_sha256':None}


def sidecar_path(directory, record):
    return Path(directory) / f"slice_{int(record['slice']['index']):04d}.overlays.json"


def safe_asset(module, relative):
    if not isinstance(relative,str) or not re.fullmatch(r'normalised/overlay_assets/[0-9a-f]{64}\.png',relative):
        raise ValueError('OVERLAY_ASSET_PATH_INVALID')
    root = Path(module).resolve()
    path = (root / relative).resolve()
    if root not in path.parents or path.suffix != '.png':
        raise ValueError('OVERLAY_ASSET_PATH_INVALID')
    return path


def png_info(data, deep=True):
    if not data.startswith(b'\x89PNG\r\n\x1a\n') or len(data)<33 or data[8:16]!=b'\x00\x00\x00\rIHDR':
        raise ValueError('OVERLAY_PNG_INVALID')
    width,height,depth,colour,compression,flt,interlace = struct.unpack('>IIBBBBB',data[16:29])
    if not (0<width<=16384 and 0<height<=16384 and width*height<=32000000
            and depth==8 and colour==6 and compression==0 and flt==0 and interlace==0):
        raise ValueError('OVERLAY_PNG_NOT_RGBA8')
    if deep:
        pos=8; packed=[]; ended=False
        while pos+12<=len(data):
            size=struct.unpack('>I',data[pos:pos+4])[0]
            tag=data[pos+4:pos+8]; body=data[pos+8:pos+8+size]
            end=pos+12+size
            if end>len(data) or zlib.crc32(tag+body)&0xffffffff != struct.unpack('>I',data[end-4:end])[0]:
                raise ValueError('OVERLAY_PNG_CRC_INVALID')
            if tag==b'IDAT': packed.append(body)
            if tag==b'IEND': ended=True; break
            pos=end
        decoder=zlib.decompressobj()
        limit=(width*4+1)*height
        raw=decoder.decompress(b''.join(packed),limit+1)
        if not ended or len(raw)!=limit or not decoder.eof or decoder.unconsumed_tail:
            raise ValueError('OVERLAY_PNG_DECODE_INVALID')
    return width,height


def validate_overlay(module, directory, record, plan, deep=True):
    expected=expected_overlay(plan,record)
    issues=[]; valid=[]
    if expected.get('error'):
        return {'status':'PARTIAL','expected_count':0,'valid_layers':[], 'issues':[expected['error']]}
    if not expected['layers']:
        return {'status':'NOT_APPLICABLE','expected_count':0,'valid_layers':[], 'issues':[]}
    payload=read_json(sidecar_path(directory,record),{})
    image=record.get('image') or {}
    identity={'slice_id':expected['slice_id'],'series_id':expected['series_id'],
              'variant_value':str((record.get('variant') or {}).get('value')),
              'base_image_sha256':image.get('sha256'),'canvas_width':image.get('width'),
              'canvas_height':image.get('height'),'plan_sha256':expected['plan_sha256']}
    if not isinstance(payload,dict) or payload.get('schema_version')!=SCHEMA_VERSION or any(payload.get(k)!=v for k,v in identity.items()):
        return {'status':'PARTIAL','expected_count':len(expected['layers']), 'valid_layers':[],
                'issues':['OVERLAY_BINDING_MISSING_OR_STALE']}
    entries=payload.get('layers')
    if not isinstance(entries,list): entries=[]
    for item in expected['layers']:
        name=item['layer']; matches=[r for r in entries if isinstance(r,dict) and r.get('layer')==name]
        try:
            if len(matches)!=1 or matches[0].get('status')!='PASS':
                detail=matches[0].get('error') if len(matches)==1 else None
                raise ValueError(detail or 'OVERLAY_LAYER_MISSING_OR_PARTIAL')
            r=matches[0]; proof=r.get('proof') or {}; transform=r.get('transform')
            if not item['filter_ids']:
                raise ValueError('OVERLAY_FILTER_MEMBERSHIP_MISSING')
            if r.get('filter_ids')!=item['filter_ids'] or proof.get('method')!='NATIVE_OVERLAY_IMAGE_SETTINGS_AND_BASE_HASH':
                raise ValueError('OVERLAY_MEMBERSHIP_OR_PROOF_INVALID')
            for k,v in {'slice_id':expected['slice_id'],'series_id':expected['series_id'],
                        'sort_order':expected['sort_order'],'layer':name,'type':'overlay',
                        'base_image_sha256':image.get('sha256')}.items():
                if proof.get(k)!=v: raise ValueError('OVERLAY_RESOURCE_IDENTITY_MISMATCH')
            if not isinstance(transform,list) or len(transform)!=6 or not all(
                isinstance(v,(int,float)) and not isinstance(v,bool) and math.isfinite(v) and abs(v)<1e7 for v in transform):
                raise ValueError('OVERLAY_TRANSFORM_INVALID')
            if abs(transform[0]*transform[3]-transform[1]*transform[2])<1e-12:
                raise ValueError('OVERLAY_TRANSFORM_SINGULAR')
            body=safe_asset(module,r.get('relative_file')).read_bytes()
            if len(body)!=r.get('bytes') or (deep and sha(body)!=r.get('sha256')):
                raise ValueError('OVERLAY_ASSET_HASH_MISMATCH')
            size=png_info(body,deep)
            if size!=(r.get('width'),r.get('height')): raise ValueError('OVERLAY_DIMENSION_MISMATCH')
            alpha=r.get('alpha') or {}
            if not (isinstance(alpha.get('nonzero'),int) and isinstance(alpha.get('transparent'),int)
                    and alpha['nonzero']>0 and alpha['transparent']>0
                    and alpha['nonzero']+alpha['transparent']==size[0]*size[1]):
                raise ValueError('OVERLAY_ALPHA_INVALID')
            if not isinstance(r.get('opacity'),(float,int)) or not 0<=r['opacity']<=1:
                raise ValueError('OVERLAY_OPACITY_INVALID')
            valid.append(r)
        except (OSError, ValueError, TypeError, KeyError, struct.error, zlib.error) as exc:
            issues.append(f'{name}: {exc}')
    if issues and payload.get('error'):
        issues.append(str(payload['error']))
    return {'status':'PASS' if not issues else 'PARTIAL','expected_count':len(expected['layers']),
            'valid_layers':valid,'issues':issues}


async def capture_overlays(page, module, directory, record, plan, *, deep=False):
    """Retry only missing layers. A mismatch never attaches data to another image."""
    expected=expected_overlay(plan,record)
    validation=validate_overlay(module,directory,record,plan)
    if validation['status'] in {'PASS','NOT_APPLICABLE'}:
        return validation
    image=record.get('image') or {}
    sid=str((record.get('slice') or {}).get('active_id') or (record.get('slice') or {}).get('id') or '')
    payload={'schema_version':SCHEMA_VERSION,'slice_id':sid,
             'series_id':str((record.get('series') or {}).get('id')),
             'variant_value':str((record.get('variant') or {}).get('value')),
             'base_image_sha256':image.get('sha256'),'canvas_width':image.get('width'),'canvas_height':image.get('height'),
             'plan_sha256':expected.get('plan_sha256'),'scope':'FILTER_LAYER_NOT_INDIVIDUAL_STRUCTURE',
             'captured_at':datetime.now(timezone.utc).isoformat(), 'layers':list(validation['valid_layers'])}
    rows={r['layer']:r for r in payload['layers']}
    remaining=[r['layer'] for r in expected['layers'] if r['layer'] not in rows]
    attempts=3 if deep else 1
    error=expected.get('error')
    for attempt in range(attempts):
        if not remaining or error: break
        try:
            result=await page.evaluate(CAPTURE_JS, {'slice_id':sid,'series_id':payload['series_id'],
                'sort_order':expected['sort_order'],'global_index':record['slice'].get('global_index',record['slice']['index']),
                'base_image_sha256':image.get('sha256'),'canvas_width':image.get('width'),'canvas_height':image.get('height'),
                'layers':remaining,'timeout_ms':5000 if deep else 1200, 'retry_index':attempt})
            if isinstance(result.get('adapter'), dict):
                payload['native_adapter'] = result['adapter']
            for entry in result.get('layers',[]):
                layer=entry.get('layer')
                if layer not in remaining: continue
                if entry.get('status')=='PASS':
                    body=base64.b64decode(entry.pop('png').split(',',1)[1],validate=True)
                    png_info(body)
                    digest=sha(body)
                    relative=f'normalised/overlay_assets/{digest}.png'
                    target=safe_asset(module,relative); target.parent.mkdir(parents=True,exist_ok=True)
                    if not target.exists() or sha(target.read_bytes())!=digest:
                        tmp=target.with_suffix('.pending');tmp.write_bytes(body);tmp.replace(target)
                    entry.update({'relative_file':relative,'sha256':digest,'bytes':len(body),
                                  'filter_ids':next(r['filter_ids'] for r in expected['layers'] if r['layer']==layer)})
                rows[layer]=entry
            remaining=[r['layer'] for r in expected['layers'] if rows.get(r['layer'],{}).get('status')!='PASS']
        except Exception as exc:
            error=str(exc)
            # No partial commit after slice/view changes; existing valid sidecar is untouched.
            if any(k in error for k in ('SLICE_CHANGED','OVERLAY_VIEW_CHANGED','OVERLAY_BASE_IMAGE_NOT_CURRENT')):
                return {'status':'PARTIAL','expected_count':len(expected['layers']),'valid_layers':validation['valid_layers'],
                        'issues':[error], 'retry_slice':True}
            break
        if remaining and attempt+1<attempts:
            await asyncio.sleep(0.3)
    payload['layers']=list(rows.values())
    payload['status']='PARTIAL' if remaining or error else 'PASS'
    payload['error']=error
    atomic_json(sidecar_path(directory,record),payload)
    final=validate_overlay(module,directory,record,plan)
    if payload['status'] != final['status']:
        payload['status']=final['status']
        atomic_json(sidecar_path(directory,record),payload)
    if error: final['issues'].append(error)
    final['alignment_mismatch']=bool(error and 'OVERLAY_BASE_ALIGNMENT_MISMATCH' in error)
    return final


def overlay_summary(module, summaries, plan):
    result={'schema_version':SCHEMA_VERSION,'expected_layers':0,'captured_layers':0,'partial_slices':0,'issues':[]}
    for summary in summaries:
        directory=Path(module)/'rendered'/summary['directory']
        for record in summary.get('records',[]):
            check=validate_overlay(module,directory,record,plan,deep=False)
            result['expected_layers']+=check['expected_count']
            result['captured_layers']+=len(check['valid_layers'])
            if check['issues']:
                result['partial_slices']+=1
                result['issues'].append({'directory':summary['directory'],'slice_index':record['slice']['index'],
                                         'slice_id':record['slice'].get('active_id'),'errors':check['issues']})
    result['status']='PARTIAL' if result['issues'] else 'PASS'
    return result
