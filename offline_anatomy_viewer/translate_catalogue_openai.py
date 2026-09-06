"""Translate remaining catalogue modules without writing to English capture data.

Uses exact catalogue keys (space-named regions are not filesystem slugs),
region-scoped deduplication, restartable checkpoints and hash-checked installation.
Run again with the same work directory to resume the same source snapshot.
"""
import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
import hashlib
import json
import gzip
import os
from pathlib import Path
import time

from anatomy_language import field_status, read_json
from export_language_template import build_template, source_files
from server import AnatomyRepository
from sync_language_pack import validate
from translate_region_openai import COLLECTIONS, batches, fields, hash_file, read, write
from translate_with_openai import apply_translations, call, normalize_term, term_meanings

POLICY = 'catalogue-vi-two-meanings-organ-context-v1'


def job_id(region, kind, source):
    return hashlib.sha256((region + '\0' + kind + '\0' + source).encode('utf-8')).hexdigest()


def input_hashes(repo, key):
    return {str(p.resolve()): hash_file(p) for p in source_files(repo, key)}


def assert_source(args, key, spec):
    # Check the file set too: newly added source files also invalidate an export.
    current = input_hashes(AnatomyRepository(args.data_root), key)
    if current != spec['source_hashes']:
        raise ValueError('Source changed after preparation: ' + key)


def selected_modules(data_root, exclude_regions):
    rows = read(data_root / 'module_catalogue.json')['modules']
    selected = {}
    for row in rows:
        region, slug = row['region'], row['slug']
        if region.replace(' ', '_') in exclude_regions:
            continue
        key = region + '/' + slug
        if key in selected:
            raise ValueError('Duplicate catalogue key: ' + key)
        selected[key] = row
    return selected


def prepare(args):
    path = args.work_dir / 'source_manifest.json'
    selected = selected_modules(args.data_root, set(args.exclude_region))
    if path.exists():
        manifest = read(path)
        if manifest['policy'] != POLICY or set(manifest['modules']) != set(selected):
            raise ValueError('Source selection changed; use a new work directory')
        return manifest
    manifest = {'policy': POLICY, 'modules': {}}
    for key, row in selected.items():
        # Fresh repository for each module bounds the large definition/point caches.
        repo = AnatomyRepository(args.data_root)
        hashes = input_hashes(repo, key)
        template = build_template(repo, key, 'vi', True)
        validate(template, template=True)
        if not template['structures'] or not template['labels']:
            raise ValueError('No usable structure/label source: ' + key)
        if hashes != input_hashes(repo, key):
            raise ValueError('Source changed during export: ' + key)
        target = args.work_dir / 'templates' / (key + '.json')
        write(target, template)
        manifest['modules'][key] = {'template': str(target.resolve()), 'sha256': hash_file(target),
            'source_hashes': hashes, 'counts': {c: len(template[c]) for c in COLLECTIONS},
            'title': row.get('title', ''), 'modality': row.get('modality', '')}
        print('EXPORTED=' + key + '; labels=' + str(len(template['labels'])), flush=True)
        del template, repo
    write(path, manifest)
    return manifest


def translated_jobs(template, spec):
    region = template['module_key'].split('/')[0]
    for col, key, row, field, source, kind, _ in fields(template):
        tid = job_id(region, kind, source)
        yield tid, {'id': tid, 'source': source, 'kind': kind, 'region': region,
            'context': template['module_key'] + ' / ' + spec['title'] + ' / ' + spec['modality']
                       + ' / ' + col + '.' + field + '; ' + row['source'].get('name', '')}, col, key, row, field


def request_batch(batch, args):
    import requests
    payload = [{k: item[k] for k in ('id', 'source', 'context')} for item in batch]
    with requests.Session() as session:
        session.trust_env = False  # This user-configured LAN endpoint is direct.
        try:
            return call(session, args.base_url.rstrip('/') + '/chat/completions', args.api_key,
                        args.model, payload, batch[0]['region'] + ' ' + batch[0]['kind'], retries=2)
        except (requests.HTTPError, requests.ConnectionError):
            # Do not multiply requests on authentication or endpoint failures.
            raise
        except Exception:
            if len(batch) == 1:
                raise
            half = len(batch) // 2
            return {**request_batch(batch[:half], args), **request_batch(batch[half:], args)}


def validate_output(template, pack):
    validate(pack)
    counts = {'enabled_fields': 0, 'term_fields': 0}
    for c in COLLECTIONS:
        if pack[c].keys() != template[c].keys():
            raise ValueError('Occurrence keys changed')
        for key, row in pack[c].items():
            if row['source'] != template[c][key]['source'] or row.get('binding') != template[c][key].get('binding'):
                raise ValueError('English source or binding changed')
            for field, source in row['source'].items():
                value = row['translation'].get(field, '')
                if source.strip():
                    if not value.strip() or field_status(row, field) != 'reviewed':
                        raise ValueError('Pending field: ' + key + '/' + field)
                    counts['enabled_fields'] += 1
                if field in ('name', 'text') and value:
                    if ';' in value or len(term_meanings(value)) > 2:
                        raise ValueError('Invalid synonym separator/count')
                    counts['term_fields'] += 1
    return counts


def install_module(args, key, raw):
    # Journal originals BEFORE each write; a rerun never replaces its own backup.
    journal_path = args.work_dir / 'install_manifest.json'
    journal = read(journal_path) if journal_path.exists() else {'files': {}}
    for n, base in enumerate([args.output_root] + ([args.mirror_root] if args.mirror_root else [])):
        dst = (base / (key + '.json')).resolve()
        # Keep the offline mirror as ordinary JSON for an already-running local
        # viewer. New repository packs are losslessly compressed for distribution.
        content = raw
        if n == 0 and not dst.exists():
            dst = dst.with_suffix('.json.gz')
            content = gzip.compress(raw, compresslevel=6, mtime=0)
        rel = str(dst)
        if rel not in journal['files']:
            backup = args.work_dir / 'previous' / str(n) / (key + '.json')
            old_sha = None
            if dst.exists():
                old = dst.read_bytes(); old_sha = hashlib.sha256(old).hexdigest()
                backup.parent.mkdir(parents=True, exist_ok=True); backup.write_bytes(old)
            journal['files'][rel] = {'before': old_sha, 'backup': str(backup.resolve()),
                                      'after': hashlib.sha256(content).hexdigest()}
            write(journal_path, journal)
        dst.parent.mkdir(parents=True, exist_ok=True)
        tmp = dst.with_suffix(dst.suffix + '.tmp'); tmp.write_bytes(content); tmp.replace(dst)
        if hash_file(dst) != hashlib.sha256(content).hexdigest():
            raise ValueError('Installed hash mismatch')


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--data-root', type=Path, required=True)
    p.add_argument('--work-dir', type=Path, required=True)
    p.add_argument('--output-root', type=Path, required=True)
    p.add_argument('--mirror-root', type=Path)
    p.add_argument('--exclude-region', action='append', default=['BRAIN'])
    p.add_argument('--workers', type=int, default=6)
    p.add_argument('--prepare-only', action='store_true')
    p.add_argument('--base-url', default=os.environ.get('TRANSLATOR_BASE_URL'))
    p.add_argument('--api-key', default=os.environ.get('TRANSLATOR_API_KEY'))
    p.add_argument('--model', default=os.environ.get('TRANSLATOR_MODEL', 'gemini-3.8-flash-high'))
    args = p.parse_args()
    manifest = prepare(args)
    if args.prepare_only:
        print('PREPARE=PASS; modules=' + str(len(manifest['modules'])), flush=True)
        return
    cp = args.work_dir / 'checkpoint.json'
    cache = read(cp) if cp.exists() else {'policy': POLICY, 'model': args.model, 'entries': {}}
    if cache['policy'] != POLICY or cache['model'] != args.model:
        raise ValueError('Checkpoint policy/model mismatch')
    jobs = {}; entries = cache['entries']
    for key, spec in manifest['modules'].items():
        path = Path(spec['template'])
        if hash_file(path) != spec['sha256']:
            raise ValueError('Template changed: ' + key)
        template = read(path)
        prior_path = args.output_root / (key + '.json')
        if not prior_path.exists(): prior_path = prior_path.with_suffix('.json.gz')
        old = read_json(prior_path, {})
        for tid, job, c, rkey, row, field in translated_jobs(template, spec):
            jobs.setdefault(tid, job)
            prior = old.get(c, {}).get(rkey, {})
            value = prior.get('translation', {}).get(field, '')
            if (tid not in entries and value.strip() and prior.get('source', {}).get(field) == job['source']
                    and prior.get('binding') == row.get('binding') and field_status(prior, field) == 'reviewed'
                    and (job['kind'] != 'term' or len(term_meanings(value)) <= 2)):
                entries[tid] = normalize_term(value, job['source']) if job['kind'] == 'term' else value
        del template, old
    write(cp, cache)
    pending = [j for tid, j in jobs.items() if not entries.get(tid, '').strip()]
    if pending and (not args.base_url or not args.api_key):
        raise ValueError('Set TRANSLATOR_BASE_URL and TRANSLATOR_API_KEY')
    queue = []
    for region in sorted({j['region'] for j in pending}):
        for kind in ('term', 'description'):
            queue.extend(batches([j for j in pending if j['region'] == region and j['kind'] == kind]))
    write(args.work_dir / 'jobs.json', jobs)
    print(f'PLAN: modules={len(manifest["modules"])}; unique={len(jobs)}; pending={len(pending)}; batches={len(queue)}', flush=True)
    completed = 0; started = time.monotonic()
    with ThreadPoolExecutor(max_workers=max(1, min(args.workers, 6))) as pool:
        futures = {pool.submit(request_batch, b, args): b for b in queue}
        for future in as_completed(futures):
            batch = futures[future]
            try:
                result = future.result()
                for item in batch:
                    value = result[item['id']]
                    entries[item['id']] = normalize_term(value, item['source']) if item['kind'] == 'term' else value
                write(cp, cache); completed += len(batch)
                print(f'TRANSLATED={completed}/{len(pending)}; region={batch[0]["region"]}; kind={batch[0]["kind"]}; elapsed_s={int(time.monotonic()-started)}', flush=True)
            except Exception as exc:
                print(f'BATCH_FAILED={type(exc).__name__}; items={len(batch)}', flush=True)
    missing = [tid for tid in jobs if not entries.get(tid, '').strip()]
    write(args.work_dir / 'failures.json', {'missing': missing})
    if missing:
        raise RuntimeError(f'{len(missing)} texts pending; rerun to resume. Published packs unchanged.')
    report = {'policy': POLICY, 'model': args.model, 'modules': {}, 'unique_jobs': len(jobs), 'missing': 0}
    for key, spec in manifest['modules'].items():
        assert_source(args, key, spec)
        template = read(Path(spec['template'])); mapped = {}
        for tid, job, c, rkey, row, field in translated_jobs(template, spec):
            mapped[f'{c}|{field}|{job["source"]}'] = entries[tid]
        pack = apply_translations(template, mapped, args.model)
        counts = validate_output(template, pack)
        # Compact JSON keeps large occurrence packs smaller without changing their
        # schema or any text/identity. The viewer reads the same JSON structure.
        out = args.work_dir / 'packs' / (key + '.json')
        out.parent.mkdir(parents=True, exist_ok=True)
        tmp = out.with_suffix('.json.tmp')
        tmp.write_text(json.dumps(pack, ensure_ascii=False, separators=(',', ':'), allow_nan=False) + '\n', encoding='utf-8')
        tmp.replace(out)
        report['modules'][key] = {**counts, 'counts': spec['counts'], 'sha256': hash_file(out)}
        print('VALIDATED=' + key + '; enabled_fields=' + str(counts['enabled_fields']), flush=True)
        del template, pack
    # Every template/output is checked before installing any pack.
    for key, spec in manifest['modules'].items():
        assert_source(args, key, spec)
        install_module(args, key, (args.work_dir / 'packs' / (key + '.json')).read_bytes())
        print('PUBLISHED=' + key, flush=True)
    report['source_hashes_unchanged'] = True
    write(args.work_dir / 'report.json', report)
    print(f'CATALOGUE_TRANSLATION=PASS; modules={len(report["modules"])}; missing=0; source_bindings=PASS', flush=True)


if __name__ == '__main__':
    main()
