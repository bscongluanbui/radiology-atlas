"""Validate the real read-only data mount and persistent state, without changing anatomy."""
import json
import os
from pathlib import Path
import sys
import tempfile

def validate(data, state):
    data, state = Path(data), Path(state)
    try:
        catalogue = json.loads((data/'module_catalogue.json').read_text(encoding='utf-8'))
        if not isinstance(catalogue, dict) or not isinstance(catalogue.get('modules'), list) or not catalogue['modules']:
            raise ValueError('module_catalogue.json must contain a non-empty modules list')
        if not (data/'modules').is_dir():
            raise ValueError('modules/ directory is missing')
        sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'offline_anatomy_viewer'))
        from server import AnatomyRepository
        repo = AnatomyRepository(data); repo.validate()
        rows = repo.catalogue()
        if not rows['captured_module_count']:
            raise ValueError('no readable image + labels pair; upload data or check UID 10001 read access')
        state.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryFile(dir=state) as f:
            f.write(b'preflight'); f.flush()
        return rows['module_count'], rows['captured_module_count']
    except (OSError, ValueError, RuntimeError) as exc:
        raise ValueError(str(exc)) from exc

if __name__ == '__main__':
    try:
        modules, ready = validate(os.environ.get('DATA_ROOT','/data'), os.environ.get('STATE_DIR','/state'))
    except ValueError as exc:
        print('PREFLIGHT=FAIL; '+str(exc), file=sys.stderr); sys.exit(2)
    print(f'PREFLIGHT=PASS; modules={modules}; ready={ready}; data=read-only; state=writable')
