"""Incremental, non-destructive translation updates. Never translates or auto-approves."""
from __future__ import annotations

import argparse
from copy import deepcopy
import hashlib
import gzip
import json
from pathlib import Path
import sys

from anatomy_language import COLLECTIONS, field_status, languages
from export_language_template import build_template
from server import AnatomyRepository, DEFAULT_DATA_ROOT

FIELDS = {"structures": {"name", "description_html", "description_text", "sources_html"},
          "filters": {"name"}, "labels": {"text"}, "texts": {"text"}}
STATUSES = {"draft", "needs_review", "reviewed"}


def digest(data):
    return hashlib.sha256(data).hexdigest()


def encode(value):
    return (json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n").encode("utf-8")


def strict_json(data):
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError(f"Duplicate JSON key: {key}")
            result[key] = value
        return result
    return json.loads(data, object_pairs_hook=pairs,
                      parse_constant=lambda value: (_ for _ in ()).throw(ValueError(f"Invalid number: {value}")))


def validate(pack, template=False):
    if not isinstance(pack, dict) or pack.get("schema_version") != 1 or pack.get("source_locale") != "en":
        raise ValueError("Expected an English-source schema_version=1 pack")
    if not isinstance(pack.get("module_key"), str) or not isinstance(pack.get("locale"), str):
        raise ValueError("Missing module_key or locale")
    for collection in COLLECTIONS:
        rows = pack.get(collection)
        if not isinstance(rows, dict):
            raise ValueError(f"Invalid collection: {collection}")
        for key, row in rows.items():
            if not isinstance(row, dict) or not isinstance(row.get("source"), dict) or not isinstance(row.get("translation"), dict):
                raise ValueError(f"Invalid row: {collection}/{key}")
            if not row["source"] or not set(row["source"]) <= FIELDS[collection]:
                raise ValueError(f"Unsupported source fields: {collection}/{key}")
            if not set(row["translation"]) <= set(row["source"]):
                raise ValueError(f"Translation without source: {collection}/{key}")
            if any(not isinstance(v, str) for v in [*row["source"].values(), *row["translation"].values()]):
                raise ValueError(f"Non-string text: {collection}/{key}")
            if row.get("status", "draft") not in STATUSES:
                raise ValueError(f"Unknown status: {collection}/{key}")
            if "field_status" in row and (not isinstance(row["field_status"], dict)
                    or not set(row["field_status"]) <= set(row["source"])
                    or any(v not in STATUSES for v in row["field_status"].values())):
                raise ValueError(f"Invalid field_status: {collection}/{key}")
            if "history" in row and not isinstance(row["history"], list):
                raise ValueError(f"Invalid history: {collection}/{key}")
            if template and (row.get("status") != "draft" or any(row["translation"].values())
                             or any(field_status(row, f) != "draft" for f in row["source"])):
                raise ValueError("Current source template must contain empty draft translations only")
    archive = pack.get("archived", {})
    if not isinstance(archive, dict) or not set(archive) <= set(COLLECTIONS):
        raise ValueError("Invalid archive")
    for collection, rows in archive.items():
        if not isinstance(rows, dict):
            raise ValueError("Invalid archive collection")
        for snapshots in rows.values():
            if not isinstance(snapshots, list) or not all(isinstance(s, dict) and isinstance(s.get("row"), dict) for s in snapshots):
                raise ValueError("Invalid archived snapshots")
    if template:
        covered = pack.get("complete_collections")
        if not isinstance(covered, list) or not covered or len(covered) != len(set(covered)) or not set(covered) <= set(COLLECTIONS):
            raise ValueError("Template needs complete_collections; re-export with the current exporter")
        if any(pack[c] for c in COLLECTIONS if c not in covered):
            raise ValueError("Uncovered template collections must be empty")


def summarize_status(statuses):
    if "needs_review" in statuses.values():
        return "needs_review"
    return "reviewed" if statuses and all(v == "reviewed" for v in statuses.values()) else "draft"


def merge_pack(previous, current):
    validate(previous); validate(current, template=True)
    if any(previous[k] != current[k] for k in ("schema_version", "module_key", "source_locale", "locale")):
        raise ValueError("Pack and source template belong to different modules/languages")
    result = deepcopy(previous)
    result.pop("status", None)  # Runtime availability is not a translation-review status.
    result["complete_collections"] = list(current["complete_collections"])
    result["sync_version"] = 1
    archive = result.setdefault("archived", {})
    events = []
    def event(kind, collection, key, field=None):
        events.append(dict(kind=kind, collection=collection, key=key, **({"field": field} if field else {})))
    for collection in current["complete_collections"]:
        output = {}; old_rows = previous[collection]
        for key, fresh in current[collection].items():
            old = old_rows.get(key)
            if old is None:
                # Recover only this exact occurrence/identity key. No name-based
                # copying across keys, taxonomies, modules, or coordinate changes.
                saved = archive.get(collection, {}).get(key, [])
                if saved:
                    old = deepcopy(saved[-1]["row"])
                    check = {**previous, **{c: {} for c in COLLECTIONS}, "archived": {}}
                    check[collection] = {key: old}; validate(check)
                    event("restored", collection, key)
            if old is None:
                row = deepcopy(fresh)
                row["field_status"] = {f: "draft" for f in fresh["source"]}
                row["status"] = "draft"; output[key] = row
                event("added", collection, key)
                continue
            row = deepcopy(old)
            row["source"] = deepcopy(fresh["source"])
            row["translation"] = {}; row["field_status"] = {}
            binding_changed = old.get("binding") != fresh.get("binding")
            if "binding" in fresh:
                row["binding"] = deepcopy(fresh["binding"])
            else:
                row.pop("binding", None)
            if binding_changed:
                event("binding_changed", collection, key)
            for field, source in fresh["source"].items():
                exists = field in old["source"]
                changed = exists and (old["source"][field] != source or binding_changed)
                row["translation"][field] = old["translation"].get(field, "") if exists else ""
                row["field_status"][field] = "needs_review" if changed else field_status(old, field) if exists else "draft"
                if changed:
                    row.setdefault("history", []).append(dict(field=field, source=old["source"][field],
                        translation=old["translation"].get(field, ""), status=field_status(old, field),
                        binding=deepcopy(old.get("binding")), reason="binding_changed" if binding_changed else "source_changed"))
                    event("changed", collection, key, field)
                elif not exists:
                    event("field_added", collection, key, field)
                else:
                    event("kept", collection, key, field)
            for field in old["source"].keys() - fresh["source"].keys():
                row.setdefault("history", []).append(dict(field=field, source=old["source"][field],
                    translation=old["translation"].get(field, ""), status=field_status(old, field), reason="field_removed"))
                event("field_removed", collection, key, field)
            row["status"] = summarize_status(row["field_status"])
            output[key] = row
        for key in old_rows.keys() - current[collection].keys():
            entry = dict(reason="missing_in_source", row=deepcopy(old_rows[key]))
            versions = archive.setdefault(collection, {}).setdefault(key, [])
            if not versions or versions[-1] != entry:
                versions.append(entry)
            event("archived", collection, key)
        result[collection] = output
    validate(result)
    counts = {kind: sum(e["kind"] == kind for e in events) for kind in
              ("added", "restored", "archived", "kept", "changed", "field_added", "field_removed", "binding_changed")}
    pending = [dict(collection=c, key=k, field=f, status=field_status(row, f))
               for c in COLLECTIONS for k, row in result[c].items() for f in row["source"]
               if row["source"][f].strip() and (field_status(row, f) != "reviewed" or not row["translation"].get(f, "").strip())]
    report = dict(module_key=current["module_key"], locale=current["locale"], counts=counts,
                  skipped_collections=[c for c in COLLECTIONS if c not in current["complete_collections"]],
                  pending_fields=pending, events=events)
    return result, report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-root", type=Path, default=DEFAULT_DATA_ROOT)
    parser.add_argument("--key", required=True)
    parser.add_argument("--locale", default="vi")
    parser.add_argument("--pack", type=Path, help="Existing pack; omit only for an initial empty pack")
    parser.add_argument("--template", type=Path, help="Previously exported current-source draft; otherwise read data-root")
    parser.add_argument("--definitions-only", action="store_true", help="Update structures/filters; leave occurrences untouched")
    parser.add_argument("--output-dir", required=True, type=Path, help="New directory, never overwritten")
    args = parser.parse_args(argv)
    try:
        if args.locale == "en" or args.locale not in {r["code"] for r in languages()}:
            raise ValueError("Choose a registered target language, e.g. vi (not en-vi)")
        if args.template and args.definitions_only:
            raise ValueError("Template coverage already determines which collections to update")
        out = args.output_dir.resolve()
        if out.exists():
            raise ValueError("Output directory already exists; choose a new path")
        raw = args.pack.read_bytes() if args.pack else None
        compressed = bool(args.pack and args.pack.name.endswith('.json.gz'))
        current = strict_json(args.template.read_bytes()) if args.template else build_template(
            AnatomyRepository(args.data_root), args.key, args.locale, not args.definitions_only)
        if current.get("module_key") != args.key or current.get("locale") != args.locale:
            raise ValueError("Source template does not match --key/--locale")
        previous = strict_json(gzip.decompress(raw) if compressed else raw) if raw is not None else {
            **{k: current[k] for k in ("schema_version", "module_key", "locale", "source_locale")},
            **{c: {} for c in COLLECTIONS}}
        merged, report = merge_pack(previous, current)
        files = {"pack.json": encode(merged), "report.json": encode(report),
                 ("previous.json.gz" if compressed else "previous.json"): raw if raw is not None else encode(previous)}
        # Refuse changes made to the input pack while a large source scan ran.
        if args.pack and args.pack.read_bytes() != raw:
            raise ValueError("Input translation pack changed during synchronization; retry")
        out.mkdir(parents=True, exist_ok=False)
        for name, data in files.items():
            with (out / name).open("xb") as stream:
                stream.write(data)
            if (out / name).read_bytes() != data:
                raise OSError("Output verification failed")
        ready = dict(status="ready", module_key=args.key, locale=args.locale,
                     files={name: digest(data) for name, data in files.items()})
        # A folder without READY.json is an interrupted bundle, not an installable pack.
        with (out / "READY.json").open("xb") as stream:
            stream.write(encode(ready))
        print("SYNC=PASS; input_unchanged=true; auto_translated=0; auto_approved=0")
        print("COUNTS=" + json.dumps(report["counts"], sort_keys=True))
        print("BUNDLE=" + str(out))
        return 0
    except (OSError, ValueError, KeyError, TypeError, EOFError) as error:
        print("SYNC=FAIL; " + str(error), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
