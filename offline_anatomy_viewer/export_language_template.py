"""Export source-only, draft translation slots without changing captured data."""
from pathlib import Path
import argparse
import json
from anatomy_language import label_key, target_key, languages
from server import AnatomyRepository, DEFAULT_DATA_ROOT


def source_files(repository, module_key):
    folder = repository.module_path(module_key)
    return sorted([p for p in (folder / "normalised").glob("*.json")] +
                  list((folder / "rendered").glob("*/*/slice_*.labels.json")))


def snapshot(paths):
    return {str(p): (p.stat().st_size, p.stat().st_mtime_ns) for p in paths}


def build_template(repository, module_key, locale, include_labels=True):
    before = snapshot(source_files(repository, module_key))
    for name in ("structures.json", "structure_details.json", "filters.json", "filters_resolved.json"):
        path = repository.module_path(module_key) / "normalised" / name
        if path.is_file():
            json.loads(path.read_text(encoding="utf-8"))
    module = repository.module(module_key)
    def draft(fields):
        return {"status": "draft", "source": fields, "translation": {k: "" for k in fields}}
    pack = dict(schema_version=1, module_key=module_key, locale=locale, source_locale="en",
                structures={}, filters={}, labels={}, texts={})
    pack["complete_collections"] = ["structures", "filters"] + (["labels", "texts"] if include_labels else [])
    for key, detail in repository._structures(module_key).by_taxon.items():
        pack["structures"][key] = draft({f: detail.get(f, "") for f in ("name", "description_html", "description_text", "sources_html")})
        pack["structures"][key]["binding"] = {f: detail.get(f) for f in ("identity_key", "structure_id", "taxon_id", "ta_id")}
    for row in module["filters"]:
        if row.get("name_resolved"):
            pack["filters"][str(row["id"])] = draft({"name": row["name"]})
    if include_labels:
        for path in sorted((repository.module_path(module_key) / "rendered").glob("*/*/slice_*.labels.json")):
            record = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(record, dict) or not isinstance(record.get("slice"), dict):
                raise ValueError(f"Invalid slice record: {path}")
            sid = record["slice"].get("active_id", record["slice"].get("id"))
            for index, label in enumerate(record.get("labels", [])):
                if not isinstance(label, dict) or not label.get("text"):
                    continue
                key = label_key(path.parent.parent.name, path.parent.name, sid, label.get("canvas"), index)
                row = draft({"text": label["text"]})
                row["binding"] = {f: label.get(f) for f in ("point_id", "point_ids", "taxon_id", "ta_id", "filter_id", "binding_verified", "x", "y", "text_align")}
                if key in pack["labels"] and pack["labels"][key] != row:
                    raise ValueError(f"Conflicting label occurrence: {key}")
                pack["labels"][key] = row
            for target in record.get("hover_targets", []):
                if not isinstance(target, dict):
                    raise ValueError(f"Invalid target record: {path}")
                name = (target.get("label") or {}).get("current") or target.get("tooltip_text")
                if not name or target.get("x") is None or target.get("y") is None:
                    continue
                key = target_key(path.parent.parent.name, path.parent.name, sid, target)
                row = draft({"text": name})
                row["binding"] = {f: target.get(f) for f in ("point_id", "taxon_id", "ta_id", "filter_id", "semantic_identities")}
                if key in pack["texts"] and pack["texts"][key] != row:
                    raise ValueError(f"Conflicting target occurrence: {key}")
                pack["texts"][key] = row
    if before != snapshot(source_files(repository, module_key)):
        raise ValueError("Source data changed during export; retry after the module upload completes")
    return pack



def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-root", type=Path, default=DEFAULT_DATA_ROOT)
    parser.add_argument("--key", required=True)
    parser.add_argument("--locale", default="vi")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--include-labels", action="store_true")
    args = parser.parse_args()
    if args.locale == "en" or args.locale not in {r["code"] for r in languages()}:
        parser.error("Choose a configured target language other than source English")
    repository = AnatomyRepository(args.data_root)
    pack = build_template(repository, args.key, args.locale, args.include_labels)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    # Exclusive create: an export must never replace someone's completed translations.
    with args.output.open("x", encoding="utf-8") as stream:
        json.dump(pack, stream, ensure_ascii=False, indent=2)
        stream.write("\n")
    print(f"TEMPLATE={args.output.resolve()}")
    print("COUNTS=" + json.dumps({k: len(pack[k]) for k in ("structures", "filters", "labels", "texts")}))
    print("TRANSLATIONS=EMPTY_DRAFTS")


if __name__ == "__main__":
    main()
