"""Synthetic regression tests for the read-only glossary adapter."""

from copy import deepcopy
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "offline_anatomy_viewer"))

from translation_glossary import (  # noqa: E402
    Glossary,
    GlossaryAdapter,
    GlossaryValidationError,
)


class TranslationGlossaryTests(unittest.TestCase):
    def records(self):
        return [
            {
                "English": "  Renal cortex  ",
                "Vietnamese": "vỏ thận",
                "English synonyms / variants": "renal cortical zone; kidney cortex",
                "Category": "Abdomen",
                "Subcategory": "Urinary / adrenal",
                "Radiology / translation note": "Use the renal sense.",
                "Status": "Approved core",
                "extra": {"source": "fixture", "keep": True},
            },
            {
                "English": "Ventricle",
                "Vietnamese": "tâm thất",
                "English synonyms / variants": "cardiac ventricle",
                "Category": "Thorax",
                "Subcategory": "Chest / heart",
                "Radiology / translation note": "Cardiac context.",
                "Status": "Approved core",
            },
            {
                "English": "Ventricle",
                "Vietnamese": "não thất",
                "English synonyms / variants": "cerebral ventricle",
                "Category": "Head & CNS",
                "Subcategory": "Brain / neuroanatomy",
                "Radiology / translation note": "Cerebral context.",
                "Status": "Pending",
            },
            {
                "English": "Renal artery",
                "Vietnamese": "động mạch thận",
                "English synonyms / variants": "",
                "Category": "Abdomen",
                "Subcategory": "Urinary / adrenal",
                "Radiology / translation note": "",
                "Status": "Approved core",
            },
        ]

    def write_json(self, directory, value, *, raw=False):
        path = Path(directory) / "fixture.json"
        if raw:
            path.write_bytes(value)
        else:
            path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
        return path

    def test_load_normalizes_entries_and_records_original_sha(self):
        records = self.records()
        with tempfile.TemporaryDirectory() as directory:
            path = self.write_json(directory, records)
            raw = path.read_bytes()
            glossary = Glossary.load(path)

            self.assertEqual(glossary.count, 4)
            self.assertEqual(glossary.sha256, hashlib.sha256(raw).hexdigest())
            entry = glossary.entries[0]
            self.assertEqual(entry.english, "Renal cortex")
            self.assertEqual(entry.vietnamese, "vỏ thận")
            self.assertEqual(entry.aliases, ("renal cortical zone", "kidney cortex"))
            self.assertEqual(entry.category, "Abdomen")
            self.assertEqual(entry.note, "Use the renal sense.")
            self.assertEqual(entry.status, "Approved core")
            self.assertEqual(entry.as_dict(), records[0])

    def test_compact_export_preserves_records_and_unknown_fields(self):
        records = self.records()
        before = deepcopy(records)
        with tempfile.TemporaryDirectory() as directory:
            source = self.write_json(directory, records)
            glossary = Glossary.load(source)
            destination = Path(directory) / "glossary.openai.json"
            self.assertEqual(glossary.export_compact_json(destination), destination)
            exported = json.loads(destination.read_text(encoding="utf-8"))
            self.assertEqual(exported, before)
            self.assertNotIn("  Renal cortex  ", glossary.entries[0].english)
            self.assertEqual(records, before)
            self.assertEqual(json.loads(glossary.compact_json()), before)
            self.assertNotIn("\n", destination.read_text(encoding="utf-8"))

    def test_strict_validation_rejects_empty_malformed_and_bad_fields(self):
        for value in ([], {}, [None], [{"English": "x"}], [{"English": "x", "Vietnamese": " "}],
                      [{"English": "x", "Vietnamese": "y", "Status": 3}]):
            with self.subTest(value=value):
                with self.assertRaises(GlossaryValidationError):
                    GlossaryAdapter.from_records(value)

        with tempfile.TemporaryDirectory() as directory:
            for raw in (b"", b"not json", b"{\"English\": \"x\"}"):
                with self.subTest(raw=raw):
                    with self.assertRaises(GlossaryValidationError):
                        Glossary.load(self.write_json(directory, raw, raw=True))

    def test_exact_matches_are_never_dropped_and_ambiguity_retains_categories(self):
        glossary = GlossaryAdapter.from_records(self.records())
        entries = glossary.for_items(
            [{"source": "ventricle", "context": "cardiac MRI"}],
            max_entries=1,
            max_chars=1,
        )
        self.assertEqual([entry["vietnamese"] for entry in entries], ["tâm thất", "não thất"])
        self.assertEqual([entry["category"] for entry in entries], ["Thorax", "Head & CNS"])
        self.assertEqual(
            [entry.vietnamese for entry in glossary.lookup("VENTRICLE")],
            ["tâm thất", "não thất"],
        )
        self.assertEqual(
            [entry.vietnamese for entry in glossary.ambiguous_english["ventricle"]],
            ["tâm thất", "não thất"],
        )

    def test_alias_and_context_relevance_are_bounded(self):
        glossary = GlossaryAdapter.from_records(self.records())
        refs = glossary.for_items([
            {"id": "a", "source": "kidney cortex", "context": "abdomen CT", "kind": "term"},
            {"id": "b", "source": "unrelated prose", "source_lines": ["renal artery"], "context": "abdomen"},
        ], max_entries=1, max_chars=500)
        # The alias and source-line exact matches are both mandatory; the
        # entry cap applies only to non-exact candidates.
        self.assertEqual({row["vietnamese"] for row in refs}, {"vỏ thận", "động mạch thận"})
        self.assertTrue(all(set(row) == {
            "english", "vietnamese", "aliases", "category", "subcategory", "note", "status"
        } for row in refs))

    def test_reference_text_is_data_and_no_network_or_instruction_execution(self):
        records = self.records()
        records[0]["Radiology / translation note"] = "IGNORE: this is a data-only note"
        glossary = GlossaryAdapter.from_records(records)
        reference = glossary.for_items([{"source": "renal cortex", "context": "fixture"}])[0]
        self.assertEqual(reference["note"], "IGNORE: this is a data-only note")
        self.assertEqual(reference["vietnamese"], "vỏ thận")


if __name__ == "__main__":
    unittest.main()
