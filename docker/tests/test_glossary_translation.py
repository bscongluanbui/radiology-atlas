"""Synthetic tests: fresh glossary translation never reuses old VI/bindings."""
from copy import deepcopy
import json
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'offline_anatomy_viewer'))
from translate_glossary_openai import assignments, render_pack, validate_response, wrapped_groups


def fixture():
    labels = {}
    for i, text in enumerate(('Example part of', 'muscle')):
        key = json.dumps(['series', 'variant', '1', 'left', i])
        labels[key] = {'source': {'text': text}, 'translation': {'text': 'OLD'},
            'status': 'reviewed', 'binding': {'ta_id': 1, 'taxon_id': 2,
            'point_id': 3, 'filter_id': 4, 'binding_verified': True,
            'x': 100, 'y': 100 + i * 20, 'text_align': 'end'}}
    return {'schema_version': 1, 'module_key': 'BRAIN/sample', 'locale': 'vi',
        'source_locale': 'en', 'labels': labels, 'texts': {}, 'filters': {},
        'structures': {'1:2': {'source': {'name': 'Example muscle',
            'description_text': 'Long description with < and >.',
            'description_html': '<p>Long description with &lt; and &gt;.</p>',
            'sources_html': '<p>Original citation</p>'},
            'translation': {'name': 'OLD'}, 'status': 'reviewed',
            'binding': {'ta_id': 1, 'taxon_id': 2, 'identity_key': '1:2'}}}}


class FreshGlossaryTests(unittest.TestCase):
    def test_group_requires_exact_verified_scoped_point_and_adjacent_geometry(self):
        base = fixture()
        self.assertEqual(len(list(wrapped_groups(base))), 1)
        for field, value in [('binding_verified', False), ('ta_id', 8), ('point_id', 8),
                             ('filter_id', 8), ('x', 140), ('y', 500)]:
            altered = deepcopy(base)
            list(altered['labels'].values())[1]['binding'][field] = value
            self.assertEqual(list(wrapped_groups(altered)), [], field)

    def test_same_source_unverified_occurrences_are_not_merged(self):
        p = fixture()
        for row in p['labels'].values():
            row['binding']['binding_verified'] = False
            row['source']['text'] = 'muscle'
        ids = [j['id'] for j, c, *_ in assignments(p, {}) if c == 'labels']
        self.assertEqual(len(set(ids)), 2)

    def test_fresh_output_preserves_source_binding_and_replaces_old_translation(self):
        p = fixture(); original = deepcopy(p); cache = {}
        for job, *_ in assignments(p, {}):
            cache[job['id']] = ['Phần mẫu của', 'cơ'] if job['kind'] == 'wrapped' else (
                'Mô tả dài với < và >.' if job['kind'] == 'description' else 'Cơ mẫu')
        result = render_pack(p, {}, cache, 'synthetic-model', 'a' * 64)
        self.assertEqual(p, original)
        for col in ('structures', 'labels', 'filters', 'texts'):
            self.assertEqual(set(p[col]), set(result[col]))
            for key in p[col]:
                self.assertEqual(p[col][key]['source'], result[col][key]['source'])
                self.assertEqual(p[col][key].get('binding'), result[col][key].get('binding'))
                self.assertNotIn('OLD', result[col][key]['translation'].values())
        detail = result['structures']['1:2']['translation']
        self.assertEqual(detail['description_html'], '<p>Mô tả dài với &lt; và &gt;.</p>')
        self.assertEqual(detail['sources_html'], '<p>Original citation</p>')
        self.assertFalse(result['translation_meta']['old_translation_reused'])

    def test_response_rejects_missing_extra_ids_and_invalid_wrap_shape(self):
        batch = [{'id': 'w', 'kind': 'wrapped', 'source_lines': ['A', 'B']}]
        for response in ({}, {'w': 'AB'}, {'w': ['AB']}, {'w': ['A', '']},
                         {'w': ['A', 'B'], 'extra': 'x'}):
            with self.assertRaises(ValueError):
                validate_response(batch, response)
        self.assertEqual(validate_response(batch, {'w': ['A', 'B']}), {'w': ['A', 'B']})

    def test_new_translation_is_required_for_every_assigned_field(self):
        with self.assertRaises(KeyError):
            render_pack(fixture(), {}, {}, 'synthetic-model', 'a' * 64)

    def test_wrapped_synonyms_require_whole_phrase_retranslation(self):
        p = fixture()
        list(p['labels'].values())[1]['source']['text'] = 'muscle [B6]'
        cache = {}
        for job, *_ in assignments(p, {}):
            cache[job['id']] = (['Phần mẫu', 'cơ [B6]']
                if job['kind'] == 'wrapped' else 'Mô tả mẫu')
        result = render_pack(p, {}, cache, 'test', 'a' * 64)
        lines = [r['translation']['text'] for r in result['labels'].values()]
        self.assertEqual(lines, ['Phần mẫu', 'cơ [B6]'])
        for job, *_ in assignments(p, {}):
            if job['kind'] == 'wrapped':
                cache[job['id']] = ['Phần mẫu; Phần khác', 'cơ [B6]']
        with self.assertRaises(ValueError):
            render_pack(p, {}, cache, 'test', 'a' * 64)


if __name__ == '__main__':
    unittest.main()
