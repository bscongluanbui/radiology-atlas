"""Incremental translation fixtures are non-clinical and never touch real data."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'offline_anatomy_viewer'))
from anatomy_language import translated_field
from sync_language_pack import merge_pack, strict_json, validate
from export_language_template import build_template
from server import AnatomyRepository


def row(source, translation=None, status='draft', **extra):
    return dict(status=status, source=source, translation=translation or {k:'' for k in source}, **extra)


def pack():
    return dict(schema_version=1, source_locale='en', locale='vi', module_key='BRAIN/mri-brain',
                complete_collections=['structures','filters','labels','texts'], structures={},filters={},labels={},texts={})


class TranslationSyncTests(unittest.TestCase):
    def test_ranked_term_separator_policy_does_not_truncate_descriptions(self):
        from translate_with_openai import normalize_term, apply_translations
        self.assertEqual(normalize_term('Mẫu A; Mẫu B; Mẫu C'),'Mẫu A')
        self.assertEqual(normalize_term('Mẫu A hoặc Mẫu B'),'Mẫu A')
        self.assertEqual(normalize_term('Mẫu A / Mẫu A / Mẫu B'),'Mẫu A')
        self.assertEqual(normalize_term('Phần (V/VI)'),'Phần (V/VI)')
        self.assertEqual(normalize_term('Phần 1/3'),'Phần 1/3')
        self.assertEqual(normalize_term('Mẫu A; Mẫu B; Mẫu C (M1)', 'Source; M1 segment'),'Mẫu A (M1)')
        self.assertEqual(normalize_term('Mẫu (A; B)'),'Mẫu (A / B)')
        p=pack();p['structures']['1:7']=row({'name':'Fixture','description_text':'Paragraph'})
        cache={'structures|name|Fixture':'A; B; C','structures|description_text|Paragraph':'Đoạn một; đoạn hai; đoạn ba.'}
        result=apply_translations(p,cache,'test')
        self.assertEqual(result['structures']['1:7']['translation']['name'],'A')
        self.assertEqual(result['structures']['1:7']['translation']['description_text'],'Đoạn một; đoạn hai; đoạn ba.')
        self.assertEqual(p['structures']['1:7']['source']['name'],'Fixture')

    def test_user_requested_auto_enable_preserves_source_and_missing_fields(self):
        from translate_with_openai import apply_translations
        template=pack()
        template['structures']['1:7']=row({'name':'Source Alpha','description_text':'Source body',
            'description_html':'<p>Source body</p>','sources_html':'<p>Reference</p>'})
        template['labels']['exact-key']=row({'text':'Absent from cache'})
        before=deepcopy(template)
        cache={'structures|name|Source Alpha':'Mẫu A',
               'structures|description_text|Source body':'Nội dung <text> & ký hiệu'}
        result=apply_translations(template,cache,'test-model')
        r=result['structures']['1:7']
        self.assertEqual(template,before)
        self.assertEqual(r['source'],before['structures']['1:7']['source'])
        self.assertEqual(r['field_status'],dict.fromkeys(r['source'],'reviewed'))
        self.assertEqual(r['translation']['description_html'],'<p>Nội dung &lt;text&gt; &amp; ký hiệu</p>')
        self.assertEqual(r['translation']['sources_html'],'<p>Reference</p>')
        self.assertEqual(result['labels']['exact-key']['field_status']['text'],'draft')
        self.assertEqual(translated_field(result,'structures','1:7','name','Source Alpha'),'Mẫu A')
        self.assertFalse(result['translation_meta']['medical_review_performed'])
        self.assertEqual(result['translation_meta']['approval_mode'],'user_requested_automatic')
        self.assertNotEqual(result['translation_meta']['approval_mode'],'medical_review')
        manual=apply_translations(template,cache,'test-model',require_review=True)
        self.assertEqual(translated_field(manual,'structures','1:7','name','Source Alpha'),'Source Alpha')
        current=deepcopy(template)
        current['structures']['1:7']['source']['name']='Changed source'
        merged,_=merge_pack(result,current)
        self.assertEqual(merged['structures']['1:7']['field_status']['name'],'needs_review')
        self.assertEqual(translated_field(merged,'structures','1:7','name','Changed source'),'Changed source')

    def setUp(self):
        self.old=pack();self.current=pack()
        self.old['structures']['1:7']=row({'name':'Source Alpha','description_text':'Old text'},
            {'name':'Mẫu A','description_text':'Bản nháp cũ'},'reviewed', note='Preserve editor note')
        self.current['structures']['1:7']=row({'name':'Source Alpha','description_text':'New text'})

    def test_change_only_one_field_preserves_other_approval(self):
        before=deepcopy(self.old);source=deepcopy(self.current)
        merged,report=merge_pack(self.old,self.current);r=merged['structures']['1:7']
        self.assertEqual(r['note'],'Preserve editor note')
        self.assertEqual(r['field_status'],{'name':'reviewed','description_text':'needs_review'})
        self.assertEqual(translated_field(merged,'structures','1:7','name','Source Alpha'),'Mẫu A')
        self.assertEqual(translated_field(merged,'structures','1:7','description_text','New text'),'New text')
        self.assertEqual(r['translation']['description_text'],'Bản nháp cũ')
        self.assertEqual(r['history'][0]['source'],'Old text')
        self.assertEqual(report['counts']['changed'],1)
        self.assertEqual(self.old,before);self.assertEqual(self.current,source)

    def test_idempotence_no_repeated_history(self):
        first,_=merge_pack(self.old,self.current);second,report=merge_pack(first,self.current)
        self.assertEqual(first,second);self.assertEqual(report['counts']['changed'],0)
        self.assertEqual(report['pending_fields'][0]['status'],'needs_review')

    def test_no_copy_by_same_name_or_other_ta_id(self):
        self.current['structures']['2:7']=row({'name':'Source Alpha'})
        merged,_=merge_pack(self.old,self.current)
        self.assertEqual(merged['structures']['2:7']['translation']['name'],'')
        self.assertEqual(merged['structures']['2:7']['field_status']['name'],'draft')

    def test_archive_and_restore_exact_key_without_approval_loss(self):
        current=pack();empty,report=merge_pack(self.old,current)
        self.assertEqual(empty['structures'],{});self.assertEqual(report['counts']['archived'],1)
        self.assertEqual(empty['archived']['structures']['1:7'][0]['row'],self.old['structures']['1:7'])
        current['structures']['1:7']=row(self.old['structures']['1:7']['source'])
        restored,report=merge_pack(empty,current)
        self.assertEqual(report['counts']['restored'],1)
        self.assertEqual(restored['structures']['1:7']['field_status']['name'],'reviewed')

    def test_source_changed_after_archive_requires_review(self):
        empty,_=merge_pack(self.old,pack());restored,_=merge_pack(empty,self.current)
        self.assertEqual(restored['structures']['1:7']['field_status']['description_text'],'needs_review')

    def test_partial_export_never_archives_uncovered_occurrences(self):
        self.old['labels']['exact-key']=row({'text':'Source'}, {'text':'Mẫu'}, 'reviewed')
        self.current['complete_collections']=['structures','filters']
        merged,report=merge_pack(self.old,self.current)
        self.assertEqual(merged['labels'],self.old['labels'])
        self.assertEqual(report['skipped_collections'],['labels','texts'])

    def test_binding_change_even_identical_text_demotes_approval(self):
        self.old['structures']['1:7']['binding']={'structure_id':123}
        self.current['structures']['1:7']['binding']={'structure_id':456}
        merged,report=merge_pack(self.old,self.current)
        self.assertEqual(merged['structures']['1:7']['field_status']['name'],'needs_review')
        self.assertEqual(report['counts']['binding_changed'],1)
        self.assertEqual(translated_field(merged,'structures','1:7','name','Source Alpha'),'Source Alpha')

    def test_field_add_remove_are_explicit_and_archived(self):
        self.current['structures']['1:7']=row({'name':'Source Alpha','sources_html':'Ref'})
        merged,report=merge_pack(self.old,self.current);r=merged['structures']['1:7']
        self.assertEqual(r['field_status']['sources_html'],'draft')
        self.assertNotIn('description_text',r['source']);self.assertEqual(r['history'][0]['reason'],'field_removed')
        self.assertEqual(report['counts']['field_removed'],1)

    def test_row_review_never_overrides_pending_or_missing_field_review(self):
        merged,_=merge_pack(self.old,self.current);r=merged['structures']['1:7'];r['status']='reviewed'
        self.assertEqual(translated_field(merged,'structures','1:7','description_text','New text'),'New text')
        r['field_status'].pop('description_text')
        self.assertEqual(translated_field(merged,'structures','1:7','description_text','New text'),'New text')
        r['field_status']['description_text']='reviewed'
        self.assertEqual(translated_field(merged,'structures','1:7','description_text','New text'),'Bản nháp cũ')

    def test_search_ignores_pending_translation_but_keeps_reviewed_name(self):
        from types import SimpleNamespace
        merged,_=merge_pack(self.old,self.current)
        definition=dict(identity_key='1:7',name='Source Alpha',latin='Latin',
                        description_html='',description_text='New text',sources_html='')
        with tempfile.TemporaryDirectory() as temp:
            repo=AnatomyRepository(Path(temp))
            with patch.object(repo,'_structures',return_value=SimpleNamespace(by_taxon={'1:7':definition})), patch('server.load_pack',return_value=merged):
                self.assertEqual(repo.search('BRAIN/mri-brain','mau a',locale='vi'),[definition])
                self.assertEqual(repo.search('BRAIN/mri-brain','ban nhap cu',locale='vi'),[])

    def test_blank_source_is_not_a_translation_task(self):
        self.current['structures']['1:7']['source']['sources_html']=''
        self.current['structures']['1:7']['translation']['sources_html']=''
        _,report=merge_pack(self.old,self.current)
        self.assertNotIn('sources_html',[r['field'] for r in report['pending_fields']])

    def test_reject_unsafe_input_without_guessing(self):
        for field,value in [('locale','fr'),('module_key','THORAX/other')]:
            other=deepcopy(self.current);other[field]=value
            with self.assertRaises(ValueError):merge_pack(self.old,other)
        other=deepcopy(self.current);other.pop('complete_collections')
        with self.assertRaises(ValueError):merge_pack(self.old,other)
        other=deepcopy(self.current);other['structures']['1:7']['translation']['name']='nonempty'
        with self.assertRaises(ValueError):merge_pack(self.old,other)
        for raw in ('{"x":1,"x":2}', '{"x":NaN}', '{broken'):
            with self.assertRaises(ValueError):strict_json(raw)

    def test_cli_bundle_hashes_input_backup_and_no_overwrite(self):
        with tempfile.TemporaryDirectory() as temp:
            d=Path(temp);original=d/'old.json';template=d/'template.json';out=d/'result'
            original.write_text(json.dumps(self.old,ensure_ascii=False),encoding='utf-8')
            template.write_text(json.dumps(self.current),encoding='utf-8');before=original.read_bytes()
            command=[sys.executable,str(ROOT/'offline_anatomy_viewer/sync_language_pack.py'),'--key','BRAIN/mri-brain',
                     '--pack',str(original),'--template',str(template),'--output-dir',str(out)]
            result=subprocess.run(command,capture_output=True,text=True,encoding='utf-8')
            self.assertEqual(result.returncode,0,result.stderr)
            self.assertEqual(original.read_bytes(),before);self.assertEqual((out/'previous.json').read_bytes(),before)
            ready=json.loads((out/'READY.json').read_text());self.assertEqual(ready['status'],'ready')
            for name,sha in ready['files'].items():self.assertEqual(hashlib.sha256((out/name).read_bytes()).hexdigest(),sha)
            second=subprocess.run(command,capture_output=True,text=True,encoding='utf-8')
            self.assertEqual(second.returncode,2);self.assertEqual(original.read_bytes(),before)
            self.assertIn('already exists',second.stderr)

    def test_exporter_coverage_and_source_change_detection(self):
        from types import SimpleNamespace
        with tempfile.TemporaryDirectory() as temp:
            d=Path(temp);(d/'normalised').mkdir()
            fake=SimpleNamespace(module_path=lambda key:d,module=lambda key:{'filters':[]},
                                 _structures=lambda key:SimpleNamespace(by_taxon={}))
            self.assertEqual(build_template(fake,'BRAIN/mri-brain','vi',False)['complete_collections'],['structures','filters'])
            with patch('export_language_template.snapshot',side_effect=[{'file':(1,1)},{'file':(2,2)}]):
                with self.assertRaises(ValueError):build_template(fake,'BRAIN/mri-brain','vi')

    def test_packaging_includes_sync_tool(self):
        from docker.release import sources
        self.assertIn(ROOT/'offline_anatomy_viewer/sync_language_pack.py',sources())


if __name__=='__main__':unittest.main()
