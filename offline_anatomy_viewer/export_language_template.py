"""Export source-only, draft translation slots without changing captured data."""
from pathlib import Path
import argparse
import json
from anatomy_language import label_key, target_key, languages
from server import AnatomyRepository, DEFAULT_DATA_ROOT


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
    module = repository.module(args.key)
    def draft(fields):
        return {"status": "draft", "source": fields, "translation": {k: "" for k in fields}}
    pack = dict(schema_version=1, module_key=args.key, locale=args.locale, source_locale="en",
                structures={}, filters={}, labels={}, texts={})
    for key, detail in repository._structures(args.key).by_taxon.items():
        pack["structures"][key] = draft({f: detail.get(f, "") for f in ("name", "description_html", "description_text", "sources_html")})
    for row in module["filters"]:
        if row.get("name_resolved"):
            pack["filters"][str(row["id"])] = draft({"name": row["name"]})
    if args.include_labels:
        for path in sorted((repository.module_path(args.key) / "rendered").glob("*/*/slice_*.labels.json")):
            record = json.loads(path.read_text(encoding="utf-8"))
            sid = record["slice"].get("active_id", record["slice"].get("id"))
            for index, label in enumerate(record.get("labels", [])):
                if not isinstance(label, dict) or not label.get("text"):
                    continue
                key = label_key(path.parent.parent.name, path.parent.name, sid, label.get("canvas"), index)
                pack["labels"][key] = draft({"text": label["text"]})
            for target in record.get("hover_targets", []):
                name = (target.get("label") or {}).get("current") or target.get("tooltip_text")
                if not name or target.get("x") is None or target.get("y") is None:
                    continue
                key = target_key(path.parent.parent.name, path.parent.name, sid, target)
                pack["texts"][key] = draft({"text": name})
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
