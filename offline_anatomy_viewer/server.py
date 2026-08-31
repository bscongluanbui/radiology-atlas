"""Serve the multi-module Radiology Atlas viewer and captured anatomy data locally."""

from __future__ import annotations

import sys
import argparse
import html
import http.server
import json
import mimetypes
import re
import threading
import urllib.parse
import urllib.request
import webbrowser
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


VIEWER_DIR = Path(__file__).resolve().parent
WORKSPACE = VIEWER_DIR.parent
sys.path.insert(0, str(WORKSPACE))
from anatomy_identity import detail_for, identity_key, structure_index, semantic_identity_verified
from overlay_capture import load_overlay_plan, validate_overlay
from anatomy_language import languages, load_pack, localized_definition, search_text

DEFAULT_DATA_ROOT = WORKSPACE / "imaios_data" / "all_modules"
SAFE_SEGMENT = re.compile(r"^[A-Za-z0-9_.() -]+$")

mimetypes.add_type("application/javascript; charset=utf-8", ".js")
mimetypes.add_type("application/json; charset=utf-8", ".json")


def load_json(path: Path, default: Any = None) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeDecodeError):
        return default


def module_icon_url(key: str, source_url: str, icons: object) -> str:
    """Use only a local thumbnail mapped to this exact module, never a modality guess."""
    item = icons.get(key) if isinstance(icons, dict) else None
    if not isinstance(item, dict) or not source_url or item.get("source_url") != source_url:
        return ""
    slug = key.rsplit("/", 1)[-1]
    if not re.fullmatch(r"[a-z0-9-]+", slug) or item.get("file") != f"{slug}.png":
        return ""
    revision = str(item.get("sha256") or "")
    if not re.fullmatch(r"[0-9a-f]{64}", revision):
        return ""
    if not (VIEWER_DIR / "assets" / "module-icons" / item["file"]).is_file():
        return ""
    return f"/assets/module-icons/{item['file']}?v={revision[:16]}"


def normalize_name(value: object) -> str:
    text = str(value or "").casefold()
    text = re.sub(r"\[[^]]+]", " ", text)
    return " ".join(re.sub(r"[^a-z0-9]+", " ", text).split())


def safe_key(value: str) -> tuple[str, str]:
    parts = value.replace("\\", "/").split("/")
    if len(parts) != 2 or not all(part and SAFE_SEGMENT.fullmatch(part) for part in parts):
        raise ValueError("Invalid module key")
    return parts[0], parts[1]


def safe_child(root: Path, *parts: str) -> Path:
    if not all(part and SAFE_SEGMENT.fullmatch(part) for part in parts):
        raise ValueError("Invalid path segment")
    target = root.joinpath(*parts).resolve()
    root_resolved = root.resolve()
    if target != root_resolved and root_resolved not in target.parents:
        raise ValueError("Path is outside the data root")
    return target


def strip_html(value: object) -> str:
    text = re.sub(r"<[^>]+>", " ", str(value or ""))
    return " ".join(html.unescape(text).split())


@dataclass
class StructureIndex:
    mtime_ns: int
    by_taxon: dict[str, dict]
    by_name: dict[str, list[dict]]


@dataclass
class PointIndex:
    mtime_ns: int
    by_slice: dict[str, list[dict]]


@dataclass
class CrossReferenceIndex:
    mtime_ns: int
    by_slice: dict[str, list[dict]]


class AnatomyRepository:
    def __init__(self, data_root: Path) -> None:
        self.data_root = data_root.resolve()
        self.modules_root = self.data_root / "modules"
        self.catalogue_path = self.data_root / "module_catalogue.json"
        self._structure_cache: dict[str, StructureIndex] = {}
        self._point_cache: dict[str, PointIndex] = {}
        self._cross_reference_cache: dict[str, CrossReferenceIndex] = {}

    def validate(self) -> None:
        required = [self.catalogue_path, self.modules_root, VIEWER_DIR / "index.html", VIEWER_DIR / "app.js"]
        missing = [str(path) for path in required if not path.exists()]
        if missing:
            raise RuntimeError("Missing required viewer input: " + ", ".join(missing))

    def module_path(self, key: str) -> Path:
        region, slug = safe_key(key)
        # Catalogue/API keys retain display names (HEAD AND NECK); capture folders
        # use underscores (HEAD_AND_NECK). Keep old space-named folders readable.
        canonical = safe_child(self.modules_root, region.replace(" ", "_"), slug)
        legacy = safe_child(self.modules_root, region, slug)
        return canonical if canonical.is_dir() or not legacy.is_dir() else legacy

    @staticmethod
    def _available_series_count(module_dir: Path) -> int:
        rendered = module_dir / "rendered"
        if not rendered.is_dir():
            return 0
        # Inspect only until the first image/label pair in each series. Empty or
        # image-only folders created by an ongoing capture are not ready to open.
        count = 0
        for series in rendered.iterdir():
            if not series.is_dir():
                continue
            ready = any(
                re.fullmatch(r"slice_\d+\.labels\.json", label.name)
                and label.with_name(label.name.removesuffix(".labels.json") + ".png").is_file()
                for variant in series.iterdir() if variant.is_dir()
                for label in variant.glob("slice_*.labels.json")
            )
            count += bool(ready)
        return count

    def catalogue(self) -> dict:
        source = load_json(self.catalogue_path, {}) or {}
        icon_manifest = load_json(VIEWER_DIR / "assets" / "module-icons" / "manifest.json", {})
        icons = icon_manifest.get("icons", {}) if isinstance(icon_manifest, dict) and icon_manifest.get("schema_version") == 1 else {}
        modules: list[dict] = []
        for row in source.get("modules", []):
            region = str(row.get("region") or "OTHER")
            slug = str(row.get("slug") or "")
            if not slug:
                continue
            key = f"{region}/{slug}"
            try:
                module_dir = self.module_path(key)
            except ValueError:
                continue
            series_count = self._available_series_count(module_dir)
            report = load_json(module_dir / "capture_report.json", {}) or {}
            audit = load_json(module_dir / "audit.json", {}) or {}
            status = audit.get("result") or report.get("status") or ("CAPTURED" if series_count else "NOT_CAPTURED")
            modules.append({
                "key": key,
                "region": region,
                "slug": slug,
                "title": row.get("title") or slug.replace("-", " ").title(),
                "description": row.get("description") or "",
                "modality": row.get("modality") or "Anatomy",
                "access": row.get("access") or "UNKNOWN",
                "source_url": row.get("url") or "",
                "icon_url": module_icon_url(key, row.get("url") or "", icons),
                "captured": series_count > 0,
                "series_count": series_count,
                "status": str(status),
            })
        captured = sum(1 for item in modules if item["captured"])
        return {
            "schema_version": 2,
            "language": "English",
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "module_count": len(modules),
            "captured_module_count": captured,
            "modules": modules,
        }

    def _structures(self, key: str) -> StructureIndex:
        normalised = self.module_path(key) / "normalised"
        path = normalised / "structure_details.json"
        rows_path = normalised / "structures.json"
        labels_path = normalised / "labels_by_slice.json"
        mtime = max((p.stat().st_mtime_ns if p.is_file() else 0) for p in [path,rows_path,labels_path])
        cached = self._structure_cache.get(key)
        if cached and cached.mtime_ns == mtime:
            return cached
        raw = load_json(path, {}) or {}
        indexed = structure_index(load_json(rows_path, []) or [])
        by_taxon, by_name = {}, defaultdict(list)
        if not indexed:
            indexed = {identity_key(x.get("capture_taxon_id",x.get("taxon_id")),x.get("capture_ta_id",x.get("ta_id"))):
                       {"taxon_id":x.get("capture_taxon_id",x.get("taxon_id")),"ta_id":x.get("capture_ta_id",x.get("ta_id")),"struct_id":x.get("capture_lookup_id",x.get("structure_id"))}
                       for x in raw.values() if isinstance(x,dict) and x.get("taxon_id") is not None}
        for scoped_key,row in indexed.items():
            taxon, ta, canonical = row["taxon_id"], row.get("ta_id"), row.get("struct_id")
            item = detail_for(raw,taxon,ta,canonical)
            # A legacy response may describe a newer terminology, but its canonical
            # structure ID must match. Never recover a conflicting taxon-only entry.
            if not item:
                legacy = raw.get(str(taxon),{})
                if canonical is not None and isinstance(legacy,dict) and legacy.get("capture_lookup_id",legacy.get("structure_id")) == canonical:
                    item = legacy
            if not item or item.get("success") is False:
                continue
            label, description = item.get("label") or {}, item.get("description") or {}
            if not label.get("current"):
                continue
            compact = {"identity_key":scoped_key,"taxon_id":str(taxon),"ta_id":ta,"structure_id":canonical,
                "name":label["current"],"latin":label.get("latin") or "",
                "description_html":description.get("content") or "",
                "description_text":strip_html(description.get("content")),
                "sources_html":description.get("sources") or "",
                "structure_page_link":item.get("structure_page_link") or ""}
            by_taxon[scoped_key] = compact
            by_name[normalize_name(compact["name"])].append(compact)
        result = StructureIndex(mtime,by_taxon,dict(by_name))
        self._structure_cache[key] = result
        return result

    @staticmethod
    def _scoped_definition(structures, taxon, ta=None):
        if ta is not None:
            return structures.by_taxon.get(identity_key(taxon,ta))
        matches = [v for v in structures.by_taxon.values() if str(v["taxon_id"])==str(taxon)]
        return matches[0] if len(matches)==1 else None

    def _points(self, key: str) -> PointIndex:
        path = self.module_path(key) / "normalised" / "points.json"
        mtime = path.stat().st_mtime_ns if path.is_file() else 0
        cached = self._point_cache.get(key)
        if cached and cached.mtime_ns == mtime:
            return cached
        raw = load_json(path, {}) or {}
        rows = raw.get("rows", []) if isinstance(raw, dict) else raw if isinstance(raw, list) else []
        by_slice: dict[str, list[dict]] = defaultdict(list)
        for row in rows:
            if isinstance(row, dict) and row.get("slice_id") is not None:
                by_slice[str(row["slice_id"])].append(row)
        result = PointIndex(mtime, dict(by_slice))
        self._point_cache[key] = result
        return result

    def _cross_references(self, key: str) -> CrossReferenceIndex:
        normalised = self.module_path(key) / "normalised"
        references_path = normalised / "cross_references.json"
        titles_path = normalised / "cross_reference_titles.json"
        slices_path = normalised / "slices.json"
        mtime = max(
            path.stat().st_mtime_ns if path.is_file() else 0
            for path in (references_path, titles_path, slices_path)
        )
        cached = self._cross_reference_cache.get(key)
        if cached and cached.mtime_ns == mtime:
            return cached
        slices = load_json(slices_path, []) or []
        series_for_slice = {
            str(row.get("id")): int(row.get("series_id"))
            for row in slices if isinstance(row, dict) and row.get("id") is not None and row.get("series_id") is not None
        }
        titles = load_json(titles_path, []) or []
        destination_by_source_order = {
            (int(row.get("series_id")), int(row.get("sort_order"))): int(row.get("destination"))
            for row in titles if isinstance(row, dict)
            and row.get("series_id") is not None and row.get("sort_order") is not None and row.get("destination") is not None
        }
        rows = load_json(references_path, []) or []
        by_slice: dict[str, list[dict]] = defaultdict(list)
        for row in rows:
            if not isinstance(row, dict) or row.get("slice_id") is None:
                continue
            slice_id = str(row["slice_id"])
            source_series_id = series_for_slice.get(slice_id)
            sort_order = int(row.get("sort_order") or 0)
            destination = destination_by_source_order.get((source_series_id, sort_order)) if source_series_id else None
            points: list[dict] = []
            for path in row.get("path") or []:
                try:
                    decoded = json.loads(path) if isinstance(path, str) else path
                except json.JSONDecodeError:
                    continue
                for point in decoded if isinstance(decoded, list) else []:
                    coordinates = point.get("pts") if isinstance(point, dict) else None
                    if isinstance(coordinates, list) and len(coordinates) >= 2:
                        points.append({"x": float(coordinates[0]), "y": float(coordinates[1])})
            if destination is not None and len(points) >= 2:
                by_slice[slice_id].append({
                    "destination_sort_order": destination,
                    "source_series_id": source_series_id,
                    "points": points,
                    "coordinate_space": {"width": 173, "height": 215},
                    "provenance": "captured_cross_references",
                })
        result = CrossReferenceIndex(mtime, dict(by_slice))
        self._cross_reference_cache[key] = result
        return result

    @staticmethod
    def _label_variants(value: object) -> list[str]:
        text = re.sub(r"\([^)]*virtual[^)]*\)", " ", str(value or ""), flags=re.IGNORECASE)
        variants = []
        for part in re.split(r"[;|/]", text):
            normal = normalize_name(part)
            if not normal or normal == "virtual":
                continue
            variants.append(normal)
            variants.append(normal.replace("extradural", "epidural"))
        return list(dict.fromkeys(variants))

    def _match_slice_definition(self, key, slice_id, label, structures, points_for_slice=None):
        if label.get("binding_verified") is True:
            definition = self._scoped_definition(structures,label.get("taxon_id"),label.get("ta_id"))
            ids = {str(x) for x in (label.get("point_ids") or [label.get("point_id")])}
            points_for_slice = self._points(key).by_slice.get(str(slice_id),[]) if points_for_slice is None else points_for_slice
            points = [p for p in points_for_slice
                      if str(p.get("id")) in ids
                      and str(p.get("ta_id"))==str(label.get("ta_id"))
                      and str(p.get("taxon_id"))==str(label.get("taxon_id"))
                      and str(p.get("filter_id"))==str(label.get("filter_id"))]
            if len(points)==len(ids):
                return definition, points[0] if points else None
            return None,None
        # Legacy/unbound labels may open an EXACT uniquely named definition, but
        # text/colour similarity must never invent a physical point or filter.
        variants = self._label_variants(label.get("text"))
        exact = {x["identity_key"]:x for variant in variants for x in structures.by_name.get(variant,[])}
        return (next(iter(exact.values())),None) if len(exact)==1 else (None,None)

    def _series_tree(self, module_dir: Path) -> list[dict]:
        rendered = module_dir / "rendered"
        series_rows: list[dict] = []
        if not rendered.is_dir():
            return series_rows
        metadata_rows = load_json(module_dir / "normalised" / "series.json", []) or []
        metadata_by_id = {
            str(row.get("id")): row for row in metadata_rows
            if isinstance(row, dict) and row.get("id") is not None
        }
        for series_dir in sorted((path for path in rendered.iterdir() if path.is_dir()), key=lambda path: path.name.casefold()):
            series_id, _, label = series_dir.name.partition("_")
            variants: list[dict] = []
            for variant_dir in sorted((path for path in series_dir.iterdir() if path.is_dir()), key=lambda path: path.name.casefold()):
                json_numbers = {
                    int(match.group(1)) for path in variant_dir.glob("slice_*.labels.json")
                    if (match := re.fullmatch(r"slice_(\d+)\.labels\.json", path.name))
                }
                png_numbers = {
                    int(match.group(1)) for path in variant_dir.glob("slice_*.png")
                    if (match := re.fullmatch(r"slice_(\d+)\.png", path.name))
                }
                available = sorted(json_numbers & png_numbers)
                variant_value, _, variant_label = variant_dir.name.partition("_")
                variants.append({
                    "directory": variant_dir.name,
                    "value": variant_value,
                    "label": variant_label.replace("_", " ") or variant_value,
                    "slices": available,
                    "slice_count": len(available),
                    "first_slice": available[0] if available else None,
                    "last_slice": available[-1] if available else None,
                })
            series_rows.append({
                "directory": series_dir.name,
                "id": series_id,
                "label": label.replace("_", " ") or series_dir.name,
                "sort_order": (metadata_by_id.get(series_id) or {}).get("sort_order"),
                "canvas_width": (metadata_by_id.get(series_id) or {}).get("canvas_width"),
                "canvas_height": (metadata_by_id.get(series_id) or {}).get("canvas_height"),
                "slices_width": (metadata_by_id.get(series_id) or {}).get("slices_width"),
                "slices_height": (metadata_by_id.get(series_id) or {}).get("slices_height"),
                "variants": variants,
                "slice_count": sum(item["slice_count"] for item in variants),
            })
        return series_rows

    def _anatomical_parts(self, key: str, module_dir: Path) -> tuple[list[dict], dict]:
        """Return the captured Anatomical Parts tree without the large membership arrays."""
        normalised = module_dir / "normalised"
        resolved_path = normalised / "filters_resolved.json"
        rows = load_json(resolved_path, None)
        source = "filters_resolved.json"
        if not isinstance(rows, list):
            rows = load_json(normalised / "filters.json", []) or []
            source = "filters.json"

        model = load_json(normalised / "anatomical_parts.json", {}) or {}
        status = load_json(normalised / "anatomical_parts_capture_status.json", {}) or {}
        local_names = defaultdict(set)
        if any(not str(row.get("name") or "").strip() for row in rows if isinstance(row, dict)):
            structures = load_json(normalised / "structures.json", []) or []
            definitions = self._structures(key)
            for structure in structures:
                definition = definitions.by_taxon.get(identity_key(structure.get("taxon_id"), structure.get("ta_id")))
                if definition and structure.get("translation_key") is not None:
                    local_names[str(structure["translation_key"])].add(definition["name"])
        compact_rows: list[dict] = []
        allowed = (
            "id", "name_id", "sort_order", "active", "category", "layer", "parents", "icon_id",
            "name", "name_language", "name_source", "children_ids", "descendant_ids", "closure_ids",
            "direct_point_count", "effective_point_count", "effective_taxon_count", "effective_slice_count",
        )
        for row in rows:
            if not isinstance(row, dict) or row.get("id") is None:
                continue
            compact = {field: row.get(field) for field in allowed if field in row}
            compact["name"] = str(row.get("name") or "").strip()
            if not compact["name"]:
                name_key = str(row.get("name_id"))
                candidates = local_names.get(name_key, set())
                # Never guess names from category codes or another module/terminology.
                if len(candidates) == 1:
                    compact["name"] = next(iter(candidates))
                    compact["name_source"] = "LOCAL_SCOPED_STRUCTURE_TRANSLATION_KEY"
            compact["name_resolved"] = bool(compact["name"])
            if not compact["name"]:
                compact["name"] = f"Name unavailable (filter {row['id']})"
                compact["name_source"] = "MISSING_CAPTURED_TRANSLATION"
            asset = row.get("icon_asset") if isinstance(row.get("icon_asset"), dict) else {}
            relative = str(asset.get("relative_file") or "").replace("\\", "/")
            parts = [part for part in relative.split("/") if part]
            if parts and all(SAFE_SEGMENT.fullmatch(part) for part in parts):
                compact["icon_url"] = "/data/" + "/".join(
                    urllib.parse.quote(part) for part in [*safe_key(key), "normalised", *parts]
                )
                compact["icon_sha256"] = asset.get("sha256")
            compact_rows.append(compact)

        indexed = {str(row["id"]): row for row in compact_rows}
        for row in compact_rows:
            row.setdefault("children_ids", [
                child.get("id") for child in compact_rows
                if str(child.get("parents") or 0) == str(row["id"])
            ])

        def descendants(filter_id: object, trail: frozenset[str] = frozenset()) -> list[object]:
            key_id = str(filter_id)
            if key_id in trail:
                return []
            result: list[object] = []
            for child_id in indexed.get(key_id, {}).get("children_ids") or []:
                if str(child_id) not in {str(item) for item in result}:
                    result.append(child_id)
                result.extend(descendants(child_id, trail | {key_id}))
            return list(dict.fromkeys(result))

        for row in compact_rows:
            row.setdefault("descendant_ids", descendants(row["id"]))
            row.setdefault("closure_ids", [row["id"], *row["descendant_ids"]])

        roots = model.get("roots") if isinstance(model.get("roots"), list) else []
        if not roots:
            roots = [row["id"] for row in compact_rows if not row.get("parents")]
        metadata = {
            "schema_version": model.get("schema_version") or 1,
            "source": source,
            "roots": roots,
            "interaction_model": model.get("interaction_model") or {
                "preview": "filter id plus descendant_ids",
                "highlight": "filter id plus descendant_ids",
                "point_membership_field": "point.filter_id",
                "overlay_membership_field": "filter.layer",
                "default_visibility_field": "filter.active",
            },
            "capture_status": status,
            "resolved_name_count": sum(row["name_resolved"] for row in compact_rows),
            "missing_name_filter_ids": [row["id"] for row in compact_rows if not row["name_resolved"]],
            "recovered_name_count": sum(row.get("name_source") in {
                "LOCAL_SCOPED_STRUCTURE_TRANSLATION_KEY"
            } for row in compact_rows),
        }
        return compact_rows, metadata

    def module(self, key: str) -> dict:
        module_dir = self.module_path(key)
        if not module_dir.is_dir():
            raise FileNotFoundError("Module data is unavailable")
        catalogue_row = next((item for item in self.catalogue()["modules"] if item["key"] == key), None)
        report = load_json(module_dir / "capture_report.json", {}) or {}
        audit = load_json(module_dir / "audit.json", {}) or {}
        filters, anatomical_parts = self._anatomical_parts(key, module_dir)
        structures = self._structures(key)
        series = self._series_tree(module_dir)
        return {
            "key": key,
            "title": (catalogue_row or {}).get("title") or key.split("/")[-1].replace("-", " ").title(),
            "region": key.split("/", 1)[0],
            "modality": (catalogue_row or {}).get("modality") or "Anatomy",
            "description": (catalogue_row or {}).get("description") or "",
            "source_url": (catalogue_row or {}).get("source_url") or "",
            "status": audit.get("result") or report.get("status") or "CAPTURED",
            "series": series,
            "filters": filters,
            "anatomical_parts": anatomical_parts,
            "structure_count": len(structures.by_taxon),
            "slice_count": sum(row["slice_count"] for row in series),
        }

    def slice(self, key: str, series: str, variant: str, number: int) -> dict:
        module_dir = self.module_path(key)
        folder = safe_child(module_dir / "rendered", series, variant)
        stem = f"slice_{number:04d}"
        record_path = folder / f"{stem}.labels.json"
        image_path = folder / f"{stem}.png"
        if not record_path.is_file() or not image_path.is_file():
            raise FileNotFoundError("The requested slice is incomplete")
        record = load_json(record_path)
        if not isinstance(record, dict):
            raise RuntimeError("The slice label file is invalid")
        structures = self._structures(key)
        labels = []
        slice_meta = record.get("slice") or {}
        slice_id = slice_meta.get("active_id", slice_meta.get("id")) if isinstance(slice_meta, dict) else None
        # One mtime-validated snapshot per request, not filesystem resolution
        # per label. All taxon/TA/point/filter/overlay checks remain intact.
        points_for_slice = self._points(key).by_slice.get(str(slice_id), [])
        previous_label: dict | None = None
        for index, label in enumerate(record.get("labels") or []):
            if not isinstance(label, dict):
                continue
            enriched = dict(label)
            definition, point = self._match_slice_definition(key, slice_id, label, structures, points_for_slice)
            continuation = not self._label_variants(label.get("text"))
            if continuation and previous_label and not record.get("exact_label_binding_schema_version"):
                same_style = (
                    previous_label.get("canvas") == label.get("canvas")
                    and str(previous_label.get("color")) == str(label.get("color"))
                    and abs(float(previous_label.get("x", 0)) - float(label.get("x", 0))) <= 4
                    and 0 < float(label.get("y", 0)) - float(previous_label.get("y", 0)) <= 32
                )
                if same_style:
                    definition = previous_label.get("definition")
                    point = previous_label.get("semantic_point")
            enriched["definition"] = definition
            enriched["semantic_point"] = point
            enriched["filter_id"] = label.get("filter_id") if label.get("binding_verified") and point else None
            enriched["label_index"] = index
            labels.append(enriched)
            previous_label = enriched
        hover_targets = []
        points_by_id = {str(p.get("id")): p for p in points_for_slice}
        for target in record.get("hover_targets") or []:
            if not isinstance(target, dict):
                continue
            enriched = dict(target)
            definition = self._scoped_definition(structures,target.get("taxon_id"),target.get("ta_id"))
            identities = target.get("semantic_identities") or []
            if target.get("hover_match_source") == "decrypted_point_api":
                semantic_items = identities or [target, *target.get("coincident_semantic_points", [])]
                identity_valid = semantic_identity_verified(target) and all(
                    (p := points_by_id.get(str(item.get("point_id")))) is not None
                    and all(str(p.get(k)) == str(item.get(k)) for k in ("taxon_id", "ta_id", "filter_id"))
                    and self._scoped_definition(structures, item.get("taxon_id"), item.get("ta_id")) is not None
                    for item in semantic_items
                )
                if not identity_valid:
                    enriched.update({"definition": None, "label": {"current": "Identity pending repair", "latin": ""},
                                     "tooltip_text": "Identity pending repair", "marker_name_verified": False,
                                     "semantic_verified": False, "hover_verified": False})
                    hover_targets.append(enriched)
                    continue
            if target.get("semantic_primary_ambiguous"):
                definitions = [self._scoped_definition(structures,x.get("taxon_id"),x.get("ta_id")) for x in identities]
                definitions = [x for x in definitions if x]
                names = list(dict.fromkeys(x["name"] for x in definitions))
                enriched["tooltip_text"] = " / ".join(names) if names else " / ".join(dict.fromkeys((x.get("label") or {}).get("current","") for x in identities))
                enriched["label"] = {"current":enriched["tooltip_text"],"latin":""}
                enriched["coincident_definitions"] = definitions
                definition = None
            elif target.get("hover_match_source") == "decrypted_point_api":
                if definition:
                    enriched["label"] = {"current":definition["name"],"latin":definition["latin"]}
                    enriched["tooltip_text"] = definition["name"]
                else:
                    # Quarantine stale semantic names until the scoped detail is repaired.
                    enriched["label"] = {"current":"Identity pending repair","latin":""}
                    enriched["tooltip_text"] = "Identity pending repair"
                    enriched["marker_name_verified"] = False
                    enriched["semantic_verified"] = False
            elif not definition:
                text = (target.get("label") or {}).get("current") or target.get("tooltip_text")
                candidates = structures.by_name.get(normalize_name(text),[])
                definition = candidates[0] if len(candidates)==1 else None
            enriched["definition"] = definition
            hover_targets.append(enriched)
        overlay = validate_overlay(module_dir, folder, record, load_overlay_plan(module_dir), deep=True)
        # Recheck the actual base file, not just a manifest claim, before rendering masks.
        if overlay["valid_layers"]:
            import hashlib
            if hashlib.sha256(image_path.read_bytes()).hexdigest() != (record.get("image") or {}).get("sha256"):
                overlay.update(status="PARTIAL", valid_layers=[], issues=["OVERLAY_BASE_FILE_HASH_MISMATCH"])
        for layer in overlay["valid_layers"]:
            layer["image_url"] = "/data/" + "/".join(urllib.parse.quote(p) for p in [*safe_key(key), *layer["relative_file"].split("/")])
        record["pixel_overlays"] = overlay
        record["labels"] = labels
        record["hover_targets"] = hover_targets
        record["cross_references"] = self._cross_references(key).by_slice.get(str(slice_id), [])
        record["image_url"] = "/data/" + "/".join(urllib.parse.quote(part) for part in [*safe_key(key), "rendered", series, variant, image_path.name])
        return record

    def structure(self, key: str, taxon: str, ta=None) -> dict:
        found = self._scoped_definition(self._structures(key),taxon,ta)
        if not found:
            raise FileNotFoundError("Structure definition is unavailable")
        return found

    def search(self, key: str, query: str, limit: int = 80, locale: str = "en") -> list[dict]:
        wanted = search_text(query)
        if not wanted:
            return []
        pack = load_pack(key, locale)
        rows = []
        for item in self._structures(key).by_taxon.values():
            translated = localized_definition(pack, item)
            haystack = search_text(f"{item['name']} {translated['name']} {item['latin']} {item['description_text']} {strip_html(translated['description_html'])} {translated['description_text']}")
            if wanted in haystack:
                score = 0 if search_text(translated["name"]).startswith(wanted) or search_text(item["name"]).startswith(wanted) else 1
                # Return the canonical source definition; the browser applies the
                # same reviewed pack so identities and source English stay intact.
                rows.append((score, item["name"].casefold(), item))
        rows.sort(key=lambda row: (row[0], row[1]))
        return [item for _, _, item in rows[:limit]]

    def data_file(self, relative_url: str) -> Path:
        decoded = [urllib.parse.unquote(part) for part in relative_url.split("/") if part]
        if len(decoded) < 3 or not all(SAFE_SEGMENT.fullmatch(part) and part not in (".", "..") for part in decoded):
            raise ValueError("Invalid data path")
        # Resolve image, overlay and filter-icon URLs through the same module
        # mapping as JSON APIs; fixing catalogue discovery alone is insufficient.
        module_dir = self.module_path("/".join(decoded[:2]))
        target = safe_child(module_dir, *decoded[2:])
        if not target.is_file():
            raise FileNotFoundError("Data file not found")
        return target


class ViewerHandler(http.server.SimpleHTTPRequestHandler):
    repository: AnatomyRepository

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, directory=str(VIEWER_DIR), **kwargs)

    def end_headers(self) -> None:
        parsed_request = urllib.parse.urlparse(self.path)
        request_path = parsed_request.path
        request_query = urllib.parse.parse_qs(parsed_request.query)
        if request_path.startswith("/data/") or (request_path.startswith("/assets/module-icons/") and request_path.endswith(".png")):
            cache_control = (
                "public, max-age=31536000, immutable"
                if "v" in request_query else "public, max-age=300"
            )
        elif request_path == "/api/slice":
            cache_control = (
                "private, max-age=31536000, immutable"
                if "rev" in request_query else "private, max-age=60"
            )
        else:
            cache_control = "no-store"
        self.send_header("Cache-Control", cache_control)
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Cross-Origin-Opener-Policy", "same-origin")
        self.send_header("Content-Security-Policy", "default-src 'self'; img-src 'self' data:; style-src 'self'; script-src 'self'; connect-src 'self';")
        super().end_headers()

    def log_message(self, format: str, *args: object) -> None:
        return

    def json_response(self, payload: object, status: int = 200) -> None:
        body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def error_response(self, status: int, message: str) -> None:
        self.json_response({"error": message, "status": status}, status)

    def do_GET(self) -> None:  # noqa: N802 - stdlib handler contract
        parsed = urllib.parse.urlparse(self.path)
        query = urllib.parse.parse_qs(parsed.query)
        try:
            if parsed.path == "/api/catalogue":
                return self.json_response(self.repository.catalogue())
            if parsed.path == "/api/languages":
                return self.json_response({"source_locale": "en", "languages": languages()})
            if parsed.path == "/api/translations":
                key = query.get("key", [""])[0]
                self.repository.module_path(key)
                return self.json_response(load_pack(key, query.get("lang", ["en"])[0]))
            if parsed.path == "/api/module":
                return self.json_response(self.repository.module(query.get("key", [""])[0]))
            if parsed.path == "/api/slice":
                return self.json_response(self.repository.slice(
                    query.get("key", [""])[0], query.get("series", [""])[0],
                    query.get("variant", [""])[0], int(query.get("slice", ["0"])[0]),
                ))
            if parsed.path == "/api/structure":
                return self.json_response(self.repository.structure(query.get("key", [""])[0], query.get("taxon", [""])[0], query.get("ta", [None])[0]))
            if parsed.path == "/api/search":
                return self.json_response({"results": self.repository.search(query.get("key", [""])[0], query.get("q", [""])[0], locale=query.get("lang", ["en"])[0])})
            if parsed.path.startswith("/data/"):
                path = self.repository.data_file(parsed.path.removeprefix("/data/"))
                self.send_response(200)
                self.send_header("Content-Type", mimetypes.guess_type(path.name)[0] or "application/octet-stream")
                self.send_header("Content-Length", str(path.stat().st_size))
                self.end_headers()
                with path.open("rb") as source:
                    while chunk := source.read(1024 * 1024):
                        self.wfile.write(chunk)
                return
            return super().do_GET()
        except (BrokenPipeError, ConnectionAbortedError, ConnectionResetError):
            # Decoded-image prefetch is intentionally low priority. Browsers may
            # cancel it during a series switch or tab close; that is not a data error.
            return
        except FileNotFoundError as error:
            self.error_response(404, str(error))
        except (ValueError, TypeError) as error:
            self.error_response(400, str(error))
        except Exception as error:  # keep malformed/in-progress data from terminating the server
            self.error_response(500, f"Viewer data error: {error}")


def build_server(data_root: Path, port: int) -> http.server.ThreadingHTTPServer:
    repository = AnatomyRepository(data_root)
    repository.validate()
    handler = type("BoundViewerHandler", (ViewerHandler,), {"repository": repository})
    return http.server.ThreadingHTTPServer(("127.0.0.1", port), handler)


def self_test(data_root: Path) -> int:
    server = build_server(data_root, 0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    port = server.server_address[1]
    try:
        def request(path: str) -> tuple[int, bytes, str]:
            with urllib.request.urlopen(f"http://127.0.0.1:{port}{path}", timeout=30) as response:
                return response.status, response.read(), response.headers.get_content_type()

        catalogue_status, catalogue_body, _ = request("/api/catalogue")
        catalogue = json.loads(catalogue_body)
        captured = next(item for item in catalogue["modules"] if item["captured"])
        module_status, module_body, _ = request("/api/module?" + urllib.parse.urlencode({"key": captured["key"]}))
        module = json.loads(module_body)
        series = next(row for row in module["series"] if any(item["slice_count"] for item in row["variants"]))
        variant = next(item for item in series["variants"] if item["slice_count"])
        slice_number = variant["slices"][0]
        slice_query = urllib.parse.urlencode({"key": captured["key"], "series": series["directory"], "variant": variant["directory"], "slice": slice_number})
        slice_status, slice_body, _ = request("/api/slice?" + slice_query)
        capture = json.loads(slice_body)
        image_status, image_body, image_type = request(capture["image_url"])
        viewer_status, viewer_body, _ = request("/")
        assert all(value == 200 for value in (catalogue_status, module_status, slice_status, image_status, viewer_status))
        assert catalogue["module_count"] >= 1 and module["slice_count"] >= 1
        assert capture.get("image", {}).get("width") and capture.get("labels") is not None
        assert image_type == "image/png" and len(image_body) > 1000 and b"anatomyViewport" in viewer_body
        print("RADIOLOGY-ATLAS-SELF-TEST")
        print(f"catalogue=HTTP_{catalogue_status},modules_{catalogue['module_count']},captured_{catalogue['captured_module_count']}")
        print(f"module=HTTP_{module_status},key_{captured['key']},series_{len(module['series'])},slices_{module['slice_count']}")
        print(f"slice=HTTP_{slice_status},number_{slice_number},labels_{len(capture.get('labels') or [])},hover_{len(capture.get('hover_targets') or [])}")
        print(f"image=HTTP_{image_status},bytes_{len(image_body)},type_{image_type}")
        print(f"viewer=HTTP_{viewer_status},bytes_{len(viewer_body)}")
        print("RESULT=PASS")
        return 0
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Open the Radiology Atlas multi-module offline viewer.")
    parser.add_argument("--data-root", type=Path, default=DEFAULT_DATA_ROOT)
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--no-browser", action="store_true")
    parser.add_argument("--self-test", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.self_test:
        return self_test(args.data_root)
    try:
        server = build_server(args.data_root, args.port)
    except OSError:
        # A fixed port may already be occupied or reserved on Windows.  Falling
        # back to an ephemeral localhost port keeps the one-click launcher usable.
        server = build_server(args.data_root, 0)
    port = server.server_address[1]
    url = f"http://127.0.0.1:{port}/"
    print("RADIOLOGY_ATLAS=READY")
    print(f"DATA_ROOT={Path(args.data_root).resolve()}")
    print(f"URL={url}")
    print("Press Ctrl+C to stop the viewer.")
    if not args.no_browser:
        threading.Timer(0.35, webbrowser.open, args=(url,)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nRADIOLOGY_ATLAS=STOPPED")
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
