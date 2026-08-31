"""One per-request points snapshot retains exact binding and file invalidation."""
from argparse import ArgumentParser
from pathlib import Path
import copy,json,os,sys,unittest
from unittest.mock import patch
ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'offline_anatomy_viewer'));sys.path.insert(0,str(ROOT))
from server import AnatomyRepository,StructureIndex,CrossReferenceIndex
from docker.cache_store import BoundedCache
p=ArgumentParser();p.add_argument('--state-dir',required=True);p.add_argument('--data-root',required=True)
args,rest=p.parse_known_args();STATE=Path(args.state_dir)
if STATE.exists() and any(STATE.iterdir()):raise RuntimeError('Use fresh isolated state.')

class SnapshotTests(unittest.TestCase):
    def setUp(self):
        self.root=STATE/self._testMethodName
        self.key='BRAIN/sample'
        self.repo=AnatomyRepository(self.root)
        self.repo._point_cache=BoundedCache(1024*1024)
        self.normal=self.root/'modules/BRAIN/sample/normalised';self.normal.mkdir(parents=True)
        self.folder=self.normal.parent/'rendered/1_Axial/default_Default';self.folder.mkdir(parents=True)
        # This temporary source image is a copy; real data is never modified.
        image=next((Path(args.data_root)/'modules/BRAIN/mri-brain/rendered').rglob('slice_0001.png'))
        (self.folder/'slice_0001.png').write_bytes(image.read_bytes())
        self.points=[{'id':99,'slice_id':100,'taxon_id':23,'ta_id':42,'filter_id':5,'x':10,'y':10}]
        self.write_points(self.points)
        self.label={'binding_verified':True,'taxon_id':23,'ta_id':42,'filter_id':5,'point_id':99,'text':'Synthetic label'}
        self.record={'slice':{'id':100},'labels':[self.label for _ in range(5)]}
        self.write_record(self.record)
        self.definition={'identity_key':'23:42','name':'Synthetic definition','taxon_id':'23','ta_id':42}
        self.structures=StructureIndex(1,{}, {})
    def write_record(self,record):
        (self.folder/'slice_0001.labels.json').write_text(json.dumps(record),encoding='utf-8')
    def write_points(self,points):
        path=self.normal/'points.json';old=path.stat().st_mtime_ns if path.exists() else 0
        path.write_text(json.dumps(points),encoding='utf-8')
        if old:os.utime(path,ns=(old+2000000000,old+2000000000))
    def read(self):
        with patch.object(self.repo,'_structures',return_value=self.structures),patch.object(self.repo,'_scoped_definition',return_value=self.definition),patch.object(self.repo,'_cross_references',return_value=CrossReferenceIndex(1,{})):
            return self.repo.slice(self.key,'1_Axial','default_Default',1)
    def test_01_points_once_per_slice(self):
        with patch.object(self.repo,'_points',wraps=self.repo._points) as calls:row=self.read()
        self.assertEqual(calls.call_count,1);self.assertEqual(len(row['labels']),5)
        self.assertTrue(all(x['semantic_point']['id']==99 and x['filter_id']==5 for x in row['labels']))
    def test_02_updated_points_invalidates_cache(self):
        self.assertIsNotNone(self.read()['labels'][0]['semantic_point'])
        altered=copy.deepcopy(self.points);altered[0]['ta_id']=999;self.write_points(altered)
        row=self.read()['labels'][0]
        self.assertIsNone(row['semantic_point']);self.assertIsNone(row['definition']);self.assertIsNone(row['filter_id'])
    def test_03_wrong_filter_never_bound(self):
        self.points[0]['filter_id']=999;self.write_points(self.points)
        self.assertIsNone(self.read()['labels'][0]['semantic_point'])
    def test_04_deleted_point_never_bound(self):
        self.write_points([]);self.assertIsNone(self.read()['labels'][0]['semantic_point'])
    def test_05_new_request_reads_updated_label(self):
        self.assertEqual(self.read()['labels'][0]['text'],'Synthetic label')
        self.record['labels'][0]={**self.label,'taxon_id':999,'text':'Changed fixture'};self.write_record(self.record)
        row=self.read()['labels'][0];self.assertEqual(row['text'],'Changed fixture');self.assertIsNone(row['definition'])

if __name__=='__main__':
    result=unittest.main(argv=[sys.argv[0],*rest],exit=False).result
    print(f'SNAPSHOT_TESTS={"PASS" if result.wasSuccessful() else "FAIL"}; tests={result.testsRun}; once_per_request,mtime_invalidation,TA_filter_point_identity,changed_label')
    raise SystemExit(0 if result.wasSuccessful() else 1)
