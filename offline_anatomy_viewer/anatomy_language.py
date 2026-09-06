"""Presentation-only language packs: exact keys, reviewed fields, source fallback."""
from __future__ import annotations

import json
import re
import unicodedata
from pathlib import Path

TRANSLATIONS_DIR = Path(__file__).resolve().parent / "translations"
COLLECTIONS = ("structures", "filters", "labels", "texts")


def read_json(path, fallback):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return fallback


def languages():
    config = read_json(TRANSLATIONS_DIR / "languages.json", {})
    rows = config.get("languages", []) if isinstance(config, dict) else []
    return [r for r in rows if isinstance(r, dict)
            and re.fullmatch(r"[a-z]{2}(?:-[A-Z]{2})?", str(r.get("code", "")))
            and isinstance(r.get("label"), str)] or [{"code": "en", "label": "English"}]


def load_pack(module_key, locale):
    parts = module_key.split("/")
    if len(parts) != 2 or not all(re.fullmatch(r"[A-Za-z0-9_.() -]+", p) and p not in (".", "..") for p in parts):
        raise ValueError("Invalid language module key")
    if locale not in {r["code"] for r in languages()}:
        raise ValueError("Unsupported anatomy language")
    empty = dict(schema_version=1, locale=locale, module_key=module_key,
                 source_locale="en", structures={}, filters={}, labels={}, texts={})
    if locale == "en":
        return empty
    path = TRANSLATIONS_DIR / locale / parts[0] / (parts[1] + ".json")
    raw = read_json(path, None)
    if not isinstance(raw, dict):
        return {**empty, "status": "pending"}
    if any(raw.get(k) != empty[k] for k in ("schema_version", "locale", "module_key", "source_locale")):
        return {**empty, "status": "invalid_pack"}
    return {**empty, **{k: raw.get(k, {}) if isinstance(raw.get(k, {}), dict) else {} for k in COLLECTIONS}, "status": "available"}


def translated_field(pack, collection, key, field, original):
    row = pack.get(collection, {}).get(str(key), {})
    if not isinstance(row, dict) or field_status(row, field) != "reviewed":
        return original
    source, translated = row.get("source"), row.get("translation")
    if not isinstance(source, dict) or not isinstance(translated, dict):
        return original
    value = translated.get(field)
    return value if field in source and source[field] == original and isinstance(value, str) and value.strip() else original


def field_status(row, field):
    """Legacy row approval is supported; explicit per-field approval takes precedence."""
    if "field_status" in row:
        statuses = row["field_status"]
        return statuses.get(field, "draft") if isinstance(statuses, dict) else "draft"
    return row.get("status", "draft")


def localized_definition(pack, definition):
    result = dict(definition)
    result["source_name"] = definition["name"]
    for field in ("name", "description_html", "description_text", "sources_html"):
        result[field] = translated_field(pack, "structures", definition["identity_key"], field, definition.get(field, ""))
    return result


def search_text(value):
    text = unicodedata.normalize("NFD", str(value or "").casefold().replace("đ", "d"))
    text = "".join(c for c in text if not unicodedata.category(c).startswith("M"))
    return " ".join("".join(c if c.isalnum() else " " for c in text).split())


def label_key(series, variant, slice_id, canvas, index):
    return json.dumps(["" if value is None else str(value) for value in (series, variant, slice_id, canvas)] + [int(index)], ensure_ascii=False, separators=(",", ":"))


def target_key(series, variant, slice_id, target):
    def number(value):
        value = float(value)
        return int(value) if value.is_integer() else value
    parts = ["" if value is None else str(value) for value in (series, variant, slice_id, target.get("point_id"))]
    return json.dumps(["target", *parts,
                       number(target["x"]), number(target["y"])], ensure_ascii=False, separators=(",", ":"))
