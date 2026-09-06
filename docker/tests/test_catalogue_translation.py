"""Synthetic tests for the all-region, English-preserving translator."""
from copy import deepcopy
import json
import gzip
import tempfile
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'offline_anatomy_viewer'))
import translate_catalogue_openai as pipeline
import anatomy_language as language
from translate_with_openai import apply_translations, call, normalize_term


class CatalogueTranslationTests(unittest.TestCase):
    def template(self):
        return dict(schema_version=1, module_key='HEAD AND NECK/fixture', locale='vi', source_locale='en',
            complete_collections=list(pipeline.COLLECTIONS), structures={}, filters={}, texts={},
            labels={'exact-key': dict(status='draft', source={'text': 'Fixture'}, translation={'text': ''},
                                     binding={'point_id': 12, 'x': 1, 'y': 2})})

    def test_catalogue_keys_not_directory_slugs_and_brain_excluded(self):
        data = {'modules': [dict(region='BRAIN', slug='brain'),
                            dict(region='HEAD AND NECK', slug='fixture')]}
        with patch.object(pipeline, 'read', return_value=data):
            self.assertEqual(list(pipeline.selected_modules(Path('.'), {'BRAIN'})), ['HEAD AND NECK/fixture'])

    def test_region_scoped_dedup_keeps_organ_context(self):
        self.assertNotEqual(pipeline.job_id('BRAIN', 'term', 'Ventricle'), pipeline.job_id('THORAX', 'term', 'Ventricle'))
        self.assertNotEqual(pipeline.job_id('THORAX', 'term', 'Fixture'), pipeline.job_id('THORAX', 'description', 'Fixture'))
        job = list(pipeline.translated_jobs(self.template(), {'title': 'Test organ', 'modality': 'CT'}))[0][1]
        self.assertIn('Test organ / CT', job['context'])

    def test_vertebral_and_bronchial_codes_remain_exact(self):
        self.assertEqual(normalize_term('Mẫu', 'Fixture L4 B6'), 'Mẫu (L4, B6)')
        self.assertEqual(normalize_term('Mẫu C10', 'Fixture C1 C10'), 'Mẫu C10 (C1)')

    def test_capitalization_alone_is_not_a_second_meaning(self):
        self.assertEqual(normalize_term('tĩnh mạch / Tĩnh mạch'), 'tĩnh mạch')
        self.assertEqual(normalize_term('Mẫu A; mẫu a; Mẫu B; Mẫu C'), 'Mẫu A / Mẫu B')

    def test_translation_preserves_bindings_and_source_exactly(self):
        template = self.template(); before = deepcopy(template)
        result = apply_translations(template, {'labels|text|Fixture': 'Mẫu A; Mẫu B; Mẫu C'}, 'test')
        self.assertEqual(pipeline.validate_output(template, result)['enabled_fields'], 1)
        self.assertEqual(result['labels']['exact-key']['translation']['text'], 'Mẫu A / Mẫu B')
        self.assertEqual(template, before)
        for change in ('source', 'binding'):
            bad = deepcopy(result); bad['labels']['exact-key'][change]['text' if change == 'source' else 'point_id'] = 'wrong'
            with self.assertRaises(ValueError): pipeline.validate_output(template, bad)

    def test_pending_or_missing_occurrence_never_publishes(self):
        template = self.template()
        with self.assertRaises(ValueError): pipeline.validate_output(template, template)
        bad = deepcopy(template); bad['labels'] = {}
        with self.assertRaises(ValueError): pipeline.validate_output(template, bad)

    def test_source_file_additions_also_invalidate_snapshot(self):
        with patch.object(pipeline, 'input_hashes', return_value={'old': 'hash', 'new': 'hash'}):
            with self.assertRaises(ValueError): pipeline.assert_source(SimpleNamespace(data_root=Path('.')), 'BRAIN/fixture', {'source_hashes': {'old': 'hash'}})

    def test_prompt_is_organ_aware_and_descriptions_are_not_truncated(self):
        class Session:
            def post(self, url, **kwargs):
                self.payload = kwargs['json']
                return SimpleNamespace(raise_for_status=lambda: None, json=lambda: {'choices': [{'message': {'content': json.dumps({'translations': {'1': 'Mẫu'}})}}]})
        s = Session(); call(s, 'http://fixture', 'fixture-token', 'test', [{'id': '1', 'source': 'Fixture'}], 'THORAX term')
        prompt = s.payload['messages'][0]['content']
        self.assertIn('cardiac ventricle=tâm thất', prompt)
        self.assertNotIn('neuroanatomical context', prompt)
        self.assertIn('Descriptions must be translated in full', prompt)

    def test_compressed_pack_has_identical_schema_and_plain_file_priority(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);folder=root/'vi'/'HEAD AND NECK';folder.mkdir(parents=True)
            plain=folder/'fixture.json';compressed=folder/'fixture.json.gz'
            template=self.template();translated=apply_translations(template,{'labels|text|Fixture':'Mẫu'},'test')
            compressed.write_bytes(gzip.compress(json.dumps(translated,ensure_ascii=False).encode('utf-8'),mtime=0))
            with patch.object(language,'TRANSLATIONS_DIR',root), patch.object(language,'languages',return_value=[{'code':'en'},{'code':'vi'}]):
                loaded=language.load_pack(template['module_key'],'vi')
                self.assertEqual(loaded['status'],'available')
                self.assertEqual(loaded['labels'],translated['labels'])
                plain.write_text(json.dumps(template),encoding='utf-8')
                self.assertEqual(language.load_pack(template['module_key'],'vi')['labels'],template['labels'])
                plain.unlink();compressed.write_bytes(b'not gzip')
                self.assertEqual(language.load_pack(template['module_key'],'vi')['status'],'pending')

    def test_release_includes_compressed_language_packs(self):
        import docker.release as release
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);viewer=root/'offline_anatomy_viewer';folder=viewer/'translations/vi/REGION';folder.mkdir(parents=True)
            pack=folder/'fixture.json.gz';pack.write_bytes(b'fixture')
            with patch.object(release,'ROOT',root):self.assertIn(pack,release.sources())

    def test_incremental_sync_accepts_gzip_and_backs_up_original_bytes(self):
        from sync_language_pack import main
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);template=self.template()
            old=apply_translations(template,{'labels|text|Fixture':'Mẫu'},'test')
            raw=gzip.compress(json.dumps(old).encode(),mtime=0)
            previous=root/'old.json.gz';previous.write_bytes(raw)
            current=root/'template.json';current.write_text(json.dumps(template),encoding='utf-8')
            out=root/'sync'
            self.assertEqual(main(['--key',template['module_key'],'--pack',str(previous),'--template',str(current),'--output-dir',str(out)]),0)
            self.assertEqual(previous.read_bytes(),raw)
            self.assertEqual((out/'previous.json.gz').read_bytes(),raw)
            self.assertEqual(json.loads((out/'pack.json').read_text(encoding='utf-8'))['labels']['exact-key']['translation']['text'],'Mẫu')


if __name__ == '__main__': unittest.main()
