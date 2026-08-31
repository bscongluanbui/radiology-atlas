"""Build an isolated non-clinical fixture; no real dataset/credentials in CI or image."""
import json
from pathlib import Path
import shutil
import sys

ROOT = Path(__file__).resolve().parents[2]

def create_fixture(target):
    target = Path(target)
    if target.exists() and any(target.iterdir()):
        raise ValueError('Fixture target must be empty')
    target.mkdir(parents=True, exist_ok=True)
    rows=[]
    for region,slug in [('BRAIN','mri-brain'),('THORAX','sample-lung')]:
        rows.append({'region':region,'slug':slug,'title':'CI fixture '+slug,'modality':'MRI'})
        variant=target/'modules'/region/slug/'rendered/1_Axial/default_Default'
        variant.mkdir(parents=True)
        # Copy an existing thumbnail byte-for-byte; it is a test image, not a clinical slice.
        shutil.copyfile(ROOT/'offline_anatomy_viewer/assets/module-icons/mri-brain.png', variant/'slice_0001.png')
        (variant/'slice_0001.labels.json').write_text(json.dumps({'slice':{'id':1},'labels':[]}),encoding='utf-8')
    (target/'module_catalogue.json').write_text(json.dumps({'modules':rows}),encoding='utf-8')
    return target

if __name__ == '__main__':
    create_fixture(sys.argv[1]); print('FIXTURE=PASS; modules=2; clinical_data=none')
