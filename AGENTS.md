# Radiology Atlas — Codex Instructions

## Priorities

For every task, optimize in this order:

1. Anatomical/data correctness.
2. Backward compatibility.
3. Security/authorization.
4. Viewer performance with bounded resources.
5. Small, maintainable diff and minimal context usage.

Make the smallest correct change. Do not turn a focused task into cleanup/refactoring.

## Context budget

Use repository context deliberately:

- Start from the user request, this file, the owning symbol/file, and its nearest tests.
- Search exact symbols/routes/DOM IDs/error strings before opening large files.
- Read direct callers/callees only as needed; stop exploring once the dependency boundary is clear.
- Do not recursively scan the repo for a local change.
- Do not bulk-read large/non-code trees unless explicitly needed:
  - `offline_anatomy_viewer/translations/vi/**`
  - `offline_anatomy_viewer/assets/module-icons/*.png`
  - `docker/static/images/**`
  - local `imaios_data/**` / `all_modules/**`
  - translation work/checkpoint/backup directories
- `offline_anatomy_viewer/app.js`, `server.py`, and `docker/portal.py` are large: locate the relevant function first.
- Reuse facts already established in the current task; do not repeatedly reopen unchanged files.
- If a change crosses subsystems or changes a schema/contract, make a short plan before editing.

## Repository map

- `offline_anatomy_viewer/`: shared local + Docker viewer.
  - `server.py`: read-only anatomy repository/API logic.
  - `app.js`: viewer state, rendering, interaction, preload/cache integration.
  - `anatomy_language.{py,js}`: localization runtime.
  - `request_queue.js`, `resource_cache.js`, `mobile_gestures.js`: focused frontend subsystems.
  - `translate_*_openai.py`, `sync_language_pack.py`, `export_language_template.py`: translation workflow.
- `anatomy_identity.py`: terminology-scoped anatomy identity/verification.
- `overlay_capture.*`, `overlay_runtime.js`: overlay bindings/runtime.
- `docker/portal.py`: Flask production gateway.
- `docker/auth_store.py`, `access_policy.py`: accounts/authorization.
- `docker/cache_store.py`: bounded server cache.
- `docker/templates/`, `docker/static/`: website/admin/login UI.
- `docker/tests/`: synthetic regression tests.
- `.github/workflows/publish-viewer.yml`: CI/release source of truth.

Do not create a second Docker-specific viewer; Docker intentionally imports the shared viewer.

## Hard anatomy/data invariants

Captured English anatomy data is source data and is read-only unless the task explicitly concerns capture/data repair.

Never silently alter to make code “work”:

- English source strings or Latin terminology
- `ta_id`, `taxon_id`, canonical structure IDs
- point/filter IDs, `filter.layer`, memberships
- label/marker coordinates or leader/bracket/stub geometry
- overlay transforms/bindings
- series/slice identity

Missing/ambiguous/unverified data must use existing fallback/unavailable behavior. Never invent anatomy.

### Scoped identity

- Use `anatomy_identity.identity_key()` semantics.
- When `ta_id` exists, identity is scoped as `ta_id:taxon_id`.
- Never merge structures by taxon number, visible name, color, or approximate position alone.
- Do not weaken canonical structure-ID checks.
- Fuzzy/legacy name matching must not create physical point/filter bindings.
- Only existing verified bindings/semantic identities are evidence.
- Prefer fail-closed/unavailable over guessing.

### Region keys

Keep public/module/translation keys distinct from capture directory names.

Example:

- key: `HEAD AND NECK/...`
- canonical capture directory: `HEAD_AND_NECK/...`

Do not rename translation folders to underscore capture slugs. Preserve legacy path compatibility already implemented.

## Translation invariants

Translations are presentation data, never replacements for English capture data.

Packs live at:

`offline_anatomy_viewer/translations/<locale>/<REGION>/<module>.json[.gz]`

Rules:

- Preserve exact `schema_version`, `locale`, `source_locale`, `module_key`, collection keys and bindings.
- Preserve every `source` value byte-for-byte where existing workflow expects exact source matching.
- Change only intended `translation` values/status/provenance.
- Never translate IDs, Latin terminology, geometry, filters/layers or bindings.
- Never edit source text to force a stale translation to display.
- Missing/stale/unreviewed translation falls back to English.
- Wrapped fragments/abbreviations need occurrence-specific translations unless an existing verified binding permits reuse.
- `.json` takes precedence over `.json.gz`; preserve this behavior.
- Do not decompress/reformat huge packs merely to inspect them.
- Prefer `sync_language_pack.py` for incremental source updates.
- Translation credentials come from environment variables only; never persist real secrets.
- Keep translation jobs checkpointed/resumable and source-hash/binding validated.
- A technical `reviewed`/publishing flag is not proof of medical review.

## Performance contracts

Do not casually refactor cache/preload/request behavior. Preserve unless explicitly changing it:

- current/foreground slice work outranks background preload;
- duplicate in-flight resources are reused where implemented;
- preload is active-series/variant scoped, not whole-library;
- caches and concurrency remain bounded;
- stale work is cancelled/invalidated on module/series/revision changes;
- language switching does not unnecessarily flush image/series caches;
- data refresh still exposes updated captured files;
- caching must not bypass mtime/source or TA/taxon/point/filter/overlay validation.

Never “optimize” by removing anatomy verification or by making caches unbounded.

For cache/preload work, inspect `docker/PERFORMANCE.md` and matching tests first.

## Security contracts

Do not weaken without an explicit task and regression coverage:

- authentication on protected viewer/API/data resources;
- Root/Admin/Standard and module/region authorization;
- CSRF on state-changing requests;
- Secure + HttpOnly session cookies;
- trusted-host and safe local-redirect checks;
- `private, no-store` for authenticated resources;
- CSP/security headers;
- read-only `/data`;
- persistent `/state`;
- bounded cache/session retention.

Avoid inline JS/handlers in templates; existing CSP expects external scripts.

Never commit `.env`, passwords/API keys/tunnel tokens, SQLite state, private keys/certs, secret-bearing caches/backups, or real external anatomy/capture data. Use synthetic fixtures.

## Editing workflow

Before editing:

1. Locate the owning implementation.
2. Check direct callers/API/DOM contracts.
3. Locate the nearest regression test.
4. Check whether identity, translation, security, cache, or deployment invariants are involved.

While editing:

- Keep the patch scoped and follow existing patterns.
- Preserve public/data/API contracts unless explicitly changing them.
- No unrelated refactor, rename, mass formatting, or dependency upgrade.
- Do not modify large/generated/data files for a code-only task.
- Prefer shared implementations over duplicated local/Docker logic.
- Do not overwrite/revert unrelated working-tree changes.

Before finishing:

- inspect the diff;
- run targeted tests first;
- expand validation only when the change crosses boundaries;
- verify no secrets/unintended large files were added.

## Testing

Use the narrowest relevant test first.

Python pattern:

```bash
python -m unittest discover -s docker/tests -p test_<area>.py -v
```

Main CI Python gate:

```bash
python -m unittest discover -s docker/tests -p test_distribution.py -v
```

Relevant Node tests:

```bash
node docker/tests/test_resource_cache.cjs
node docker/tests/test_request_queue.cjs
node docker/tests/test_unified_preload.cjs
node docker/tests/test_site_filters.cjs
node docker/tests/test_viewer_navigation.cjs
node docker/tests/test_anatomy_language.cjs
node docker/tests/test_mobile_gestures.cjs
```

Broad viewer checks when appropriate:

```bash
python offline_anatomy_viewer/server.py --self-test
python offline_anatomy_viewer/verify_viewer.py
```

For Docker/release changes, follow `.github/workflows/publish-viewer.yml` as the authoritative CI sequence. Do not overwrite a developer's real `docker/.env` to reproduce CI.

Do not run full Docker/multi-arch builds for a small CSS/text/local-only change.

## Final response

Keep completion reports short:

1. What changed.
2. Files changed.
3. Tests/checks run and result.
4. Anything not run or remaining risk.

Do not provide a long walkthrough unless requested.
