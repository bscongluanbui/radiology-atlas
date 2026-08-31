"""Terminology-scoped anatomy identity; never conflate equal taxon numbers."""
from __future__ import annotations

IDENTITY_SCHEMA_VERSION = 2


def identity_key(taxon_id, ta_id=None):
    return f"{ta_id}:{taxon_id}" if ta_id is not None else str(taxon_id)


def detail_for(details, taxon_id, ta_id=None, struct_id=None):
    """Read a scoped record, or an unambiguous compatible legacy record.

    A failed scoped request must not fall back to an unrelated legacy record.
    Response IDs can use a newer terminology: capture_* records the requested
    identity, while structure_id/capture_lookup_id proves the canonical target.
    """
    key = identity_key(taxon_id, ta_id)
    row = details.get(key)
    if row is None and key != str(taxon_id):
        row = details.get(str(taxon_id))
    if not isinstance(row, dict) or row.get("success") is False:
        return {}
    actual_struct = row.get("capture_lookup_id", row.get("structure_id"))
    # Legacy endpoints may return the modern terminology for a correctly requested
    # canonical structure. Its matching struct_id proves the identity; the
    # response's modern taxon number must not invalidate that proof.
    if ("capture_identity_key" not in row and struct_id is not None
            and actual_struct is not None and str(actual_struct) == str(struct_id)):
        return row
    actual_ta = row.get("capture_ta_id", row.get("ta_id"))
    actual_taxon = row.get("capture_taxon_id", row.get("taxon_id"))
    if ta_id is not None and actual_ta is not None and str(actual_ta) != str(ta_id):
        return {}
    if actual_taxon is not None and str(actual_taxon) != str(taxon_id):
        return {}
    if struct_id is not None and actual_struct is not None and str(actual_struct) != str(struct_id):
        return {}
    return row


def structure_index(rows):
    """Duplicate parent rows are fine; conflicting canonical IDs are not."""
    result = {}
    for row in rows:
        if not isinstance(row, dict) or row.get("taxon_id") is None:
            continue
        key = identity_key(row["taxon_id"], row.get("ta_id"))
        if key in result and result[key].get("struct_id") != row.get("struct_id"):
            raise ValueError(f"CONFLICTING_CANONICAL_STRUCTURE: {key}")
        result[key] = row
    return result


def canonical_detail_issues(structures, details):
    issues = []
    for key, row in structure_index(structures).items():
        detail = detail_for(details, row["taxon_id"], row.get("ta_id"), row.get("struct_id"))
        if not detail or not (detail.get("label") or {}).get("current"):
            raw = details.get(key, details.get(str(row["taxon_id"]), {}))
            issues.append({"identity_key": key, "taxon_id": row["taxon_id"],
                           "ta_id": row.get("ta_id"), "expected_struct_id": row.get("struct_id"),
                           "actual_struct_id": raw.get("capture_lookup_id", raw.get("structure_id")) if isinstance(raw, dict) else None})
    return issues


def semantic_identity_verified(item):
    """Accept a single proven point or a proven exact-coordinate identity set."""
    import math
    if not isinstance(item, dict) or item.get("semantic_verified") is not True:
        return False
    error = item.get("semantic_fit_error_px")
    if not isinstance(error, (int, float)) or not math.isfinite(error) or not 0 <= error <= 2.25:
        return False
    if not str(item.get("identity_source") or "").startswith("DECRYPTED_API_COORDINATE"):
        return False
    if item.get("point_id") is not None and not item.get("semantic_primary_ambiguous"):
        return True
    identities = item.get("semantic_identities") or []
    return (item.get("semantic_primary_ambiguous") is True and len(identities) > 1
            and len({str(x.get("point_id")) for x in identities}) == len(identities)
            and all(x.get("point_id") is not None and x.get("taxon_id") is not None
                    and x.get("semantic_verified") is True and (x.get("label") or {}).get("current")
                    and isinstance(x.get("x"), (int, float)) and math.isfinite(x["x"])
                    and isinstance(x.get("y"), (int, float)) and math.isfinite(x["y"])
                    for x in identities)
            and len({(round(x["x"], 6), round(x["y"], 6)) for x in identities}) == 1)


def verified_interactive_point_ids(bindings, targets):
    """Count anatomical points, not text rows: one label can name two points.

    Callers must first validate bindings against labels/API as applicable. Saved
    summary counters and unverified markers are never used as coverage evidence.
    """
    result = set()
    for binding in bindings:
        if (not isinstance(binding, dict) or binding.get("binding_verified") is not True
                or binding.get("taxon_id") is None or binding.get("filter_id") is None):
            continue
        result.update(str(p) for p in binding.get("point_ids") or [binding.get("point_id")] if p is not None)
    for target in targets:
        if not semantic_identity_verified(target) or target.get("marker_name_verified") is not True:
            continue
        identities = target.get("semantic_identities") or [target, *target.get("coincident_semantic_points", [])]
        result.update(str(p["point_id"]) for p in identities
                      if isinstance(p, dict) and p.get("point_id") is not None
                      and p.get("semantic_verified") is True and (p.get("label") or {}).get("current"))
    return result
