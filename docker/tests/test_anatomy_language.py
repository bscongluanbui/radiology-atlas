"""Synthetic language fixtures; source anatomy never changes."""
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'offline_anatomy_viewer'))
import anatomy_language as language
from server import AnatomyRepository


class AnatomyLanguageTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)
        (self.directory / 'languages.json').write_text(json.dumps({'languages': [
            {'code': 'en', 'label': 'English'}, {'code': 'vi', 'label': 'Tiếng Việt'},
            {'code': 'ja', 'label': 'Japanese'}]}), encoding='utf-8')
        self.mock = patch.object(language, 'TRANSLATIONS_DIR', self.directory)
        self.mock.start(); self.addCleanup(self.mock.stop)
        self.source = dict(identity_key='1:7', name='Fixture Alpha', latin='Fixture Latin',
                           description_html='<p>Original description</p>', description_text='Original description', sources_html='Ref')
        self.pack = language.load_pack('BRAIN/mri-brain', 'vi')
        self.pack['structures']['1:7'] = dict(status='reviewed', source={k:self.source[k] for k in ('name','description_text')},
                                           translation=dict(name='Mẫu thử', description_text='Nội dung riêng'))

    def install(self):
        p = self.directory / self.pack['locale'] / 'BRAIN/mri-brain.json'
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(self.pack, ensure_ascii=False), encoding='utf-8')
        return p

    def test_exact_identity_review_and_source_guard(self):
        self.assertEqual(language.translated_field(self.pack, 'structures', '1:7', 'name', 'Fixture Alpha'), 'Mẫu thử')
        for key, original in [('2:7','Fixture Alpha'), ('1:7','New source')]:
            self.assertEqual(language.translated_field(self.pack, 'structures', key, 'name', original), original)
        for row in [None, [], 123, {}, {'status':'draft'}]:
            self.pack['structures']['1:7'] = row
            self.assertEqual(language.translated_field(self.pack,'structures','1:7','name','Fixture Alpha'),'Fixture Alpha')

    def test_invalid_missing_pack_and_traversal(self):
        self.assertEqual(self.pack['status'], 'pending')
        path = self.install()
        self.assertEqual(language.load_pack('BRAIN/mri-brain','vi')['status'], 'available')
        for field, value in [('schema_version',999), ('module_key','OTHER/module'), ('source_locale','fr'), ('locale','ja')]:
            original=self.pack[field]; self.pack[field]=value; self.install()
            self.assertEqual(language.load_pack('BRAIN/mri-brain','vi')['status'],'invalid_pack')
            self.pack[field]=original
        path.write_text('{broken',encoding='utf-8')
        self.assertEqual(language.load_pack('BRAIN/mri-brain','vi')['status'],'pending')
        for key in ('../mri-brain','BRAIN/../../escape','BRAIN\\evil/mri'):
            with self.assertRaises(ValueError): language.load_pack(key,'vi')
        with self.assertRaises(ValueError): language.load_pack('BRAIN/mri-brain','en-vi')

    def test_search_keeps_source_ids_and_supports_both_languages(self):
        self.install()
        repo = AnatomyRepository(self.directory)
        other = {**self.source, 'identity_key':'2:7', 'name':'Fixture Beta'}
        with patch.object(repo, '_structures', return_value=SimpleNamespace(by_taxon={'1:7':self.source, '2:7':other})):
            for query in ('Mẫu thử','mau thu','noi dung rieng','Fixture Alpha'):
                self.assertEqual(repo.search('BRAIN/mri-brain',query,locale='vi'),[self.source])
            self.assertEqual(len(repo.search('BRAIN/mri-brain','Fixture Latin',locale='vi')),2)
            self.assertEqual(repo.search('BRAIN/mri-brain','mau thu',locale='en'),[])
        self.assertEqual(self.source['name'],'Fixture Alpha')

    def test_unicode_and_exact_occurrence_keys(self):
        self.assertEqual(language.search_text('Đường [Mẫu] thử'),'duong mau thu')
        self.assertEqual(language.search_text('日本語 αβγ Кость'),'日本語 αβγ кость')
        self.assertEqual(language.label_key('S','V',5,'C',0),'["S","V","5","C",0]')
        self.assertEqual(language.target_key('S','V',5,dict(point_id=7,x=1.5,y=2)), '["target","S","V","5","7",1.5,2]')
        self.assertEqual(language.label_key('S','V',5,None,0),'["S","V","5","",0]')
        self.assertEqual(language.target_key('S','V',5,dict(point_id=0,x=1,y=2)), '["target","S","V","5","0",1,2]')

    def test_client_regressions(self):
        node = shutil.which('node')
        self.assertIsNotNone(node, 'Node is required by the published-image test gate')
        result = subprocess.run([node,str(ROOT/'docker/tests/test_anatomy_language.cjs')],capture_output=True,text=True,encoding='utf-8')
        self.assertEqual(result.returncode,0,result.stdout+result.stderr)
        self.assertIn('LANGUAGE_DOM=PASS',result.stdout)


if __name__ == '__main__': unittest.main()
