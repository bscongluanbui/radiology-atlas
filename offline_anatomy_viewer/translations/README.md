# Anatomy language packs

This directory belongs to the **viewer**, not the capture pipeline. Vietnamese
packs are stored separately from the English capture data. The default source language is English;
The menu has three modes: **English**, **Tiếng Việt**, and **Song ngữ**.
`vi` uses source-English fallback. **Song ngữ** shows English above Vietnamese
in labels, tooltips, structure lists, Anatomical Parts and definition headings.
Definition paragraphs and references use an English block followed by a Vietnamese
block. Both rows belong to the same anatomical identity, not separate structures.
An untranslated secondary row says **Chưa có bản dịch**; it never presents English
as if it were a Vietnamese translation. The mri-brain Vietnamese pack is bundled.
Latin terminology and anatomical IDs are never translated. UI chrome is separate
from the anatomical-content language choice.

## Location and schema

One pack per module: `translations/<locale>/<REGION>/<module-slug>.json`.
The registry `languages.json` currently registers `en` and `vi`. `en-vi` is a
viewer display mode, **not** a pack locale: it loads the same `vi` pack as Tiếng Việt.
The registry supports adding a future language without changing anatomy IDs.

```json
{
  "schema_version": 1,
  "locale": "vi",
  "source_locale": "en",
  "module_key": "BRAIN/mri-brain",
  "structures": {},
  "filters": {},
  "labels": {},
  "texts": {}
}
```

Each collection maps stable keys to entries (legacy row-level review remains supported):

```json
{
  "status": "draft",
  "source": {"name": "Exact original source name"},
  "translation": {"name": ""}
}
```

- **structures**: exact `identity_key` returned by the viewer, including terminology
  (`ta_id`) and taxon scope. Fields: `name`, `description_html`, `description_text`,
  `sources_html`. If both translated description forms exist, HTML takes precedence.
- **filters**: module-local filter ID, field `name`. Never translate `layer` or IDs.
- **labels** and **texts**: occurrence key encoded as a compact JSON array of
  `[seriesDirectory, variantDirectory, sliceId, canvas, labelIndex]`, field `text`.
  Missing optional string components use `""` on both Python and JavaScript sides.
  `labels` takes precedence; `texts` covers captured free-text annotations. These
  entries preserve fragment/line identity and do not alter coordinates or leaders.
- Native hover text without a scoped definition can use `texts` with key
  `["target", seriesDirectory, variantDirectory, sliceId, pointId, x, y]`. IDs in
  these keys are strings; x/y are numbers. The exporter also creates these slots.
- A whole exact source label may reuse its structure's translated `name` only
  when the source binding is verified. Wrapped fragments/abbreviations require
  explicit occurrence entries rather than guessing or repeating a full name.
- Text already baked into PNG pixels is not rewritten; this schema localizes the
  viewer's captured text/vector layer, tooltips, structure list, definitions,
  Anatomical Parts and search.

## Review and fallback

The user has enabled automatic approval: the AI translator marks completed fields
`field_status: "reviewed"` so they display immediately. This legacy flag is a
publishing switch, not a claim of medical review; provenance is recorded as
`approval_mode: "user_requested_automatic"` and `medical_review_performed: false`.
Only enabled, non-empty string fields whose `source` exactly
matches the current source field are used. Missing, empty, draft, stale-source or
invalid-pack fields fall back independently to English. Do not replace the source
values to force a stale translation to apply. Keep HTML formatting minimal;
the viewer sanitizes definition content. Name and label values render as text.

Language changes preserve filter IDs, marker coordinates, selected identity, slice,
zoom/pan and the preloaded image/JSON cache. Search supports both source names and
translated names/descriptions, Vietnamese with or without accents, and Latin.
Non-Latin letters are retained too. Module search matches description-only queries;
the slice list searches the visible structure names. In bilingual mode, Enter,
click/tap and mobile long-press on either row act on the same source label. A
long-press remains highlight-only, without opening Detail. Wrapped source-label
fragments keep their own occurrence slots; do not assign a whole name to each
fragment. Bilingual SVG text uses smaller two-line typography while keeping
the source anchor, marker and leader geometry unchanged.
Use Refresh or reselect a language after installing a pack to reload it.
Packs load alongside the first slice; a slow language response displays source
English temporarily rather than holding up image loading. Switching languages
never restarts series preloading or clears the warm image cache.

## Before translating and publishing

1. Export against the current module source, not against a renamed directory.
2. Translate only `translation` values; retain `module_key`, all keys, `source`,
   coordinates, Latin terms, terminology IDs, filters and layer membership.
3. The translator enables completed fields automatically, as requested.
   Use `--require-review` only to opt back into a manual publishing gate.
   Automated tests verify bindings/fallback, not medical correctness.
4. Check long names and wrapped labels on desktop and mobile with the actual pack.
   PNG text remains pixels; translating it requires a separate rendering project.
5. Commit reviewed packs under this directory with the viewer. The same files are
   included in local releases and the Docker image; rebuild/publish that image
   before pulling it on the server. Capture data remains unchanged and read-only.

Regression gate: `python -m unittest discover -s docker/tests -p test_distribution.py -v`
includes `test_anatomy_language.py` and its Node DOM/identity tests.

## Export source-only slots

From the workspace root:

```powershell
python .\offline_anatomy_viewer\export_language_template.py --key BRAIN/mri-brain --locale vi --include-labels --output .\mri-brain.vi.draft.json
```

The exporter creates empty `draft` slots for current definitions, filters and,
optionally, every captured label occurrence. It never overwrites an existing file.
After translation, copy/merge the completed pack into the module location above.
Do not publish unreviewed generated translations as reviewed anatomical data.

## Incremental updates

Use `sync_language_pack.py` instead of replacing an existing pack with a fresh
empty export. See **../TRANSLATION_UPDATES.md** for Windows/Linux commands.
It exports the current source, preserves unchanged translations/reviews, flags
changed fields, archives missing entries and creates a byte-exact previous-pack
backup in a new output directory. It never installs, translates or approves data.

Synchronized rows have `field_status` (`draft`, `needs_review`, `reviewed`) per
source field. These flags override the row's summary `status`. Changing only
`status` to `reviewed` does not approve pending fields. A changed translation
remains available as an editing suggestion, but the viewer shows source English
until that specific field is reviewed. Unchanged reviewed fields continue to work
in Tiếng Việt and Song ngữ. `history` preserves earlier changed/removed fields;
top-level `archived` preserves removed keys and is ignored by the viewer.

Current exports declare `complete_collections`. Definitions-only synchronization
leaves existing labels/texts untouched. Missing coverage metadata requires a new
export rather than assuming a partial snapshot is a complete dataset. Optional
source `binding` records identify canonical structures and captured occurrences;
a changed binding (or first binding on a legacy row) requires renewed review,
even if its text happens to be the same. Names alone are never a merge key.

## AI-assisted Vietnamese translation (mri-brain)

Use the OpenAI-compatible translator without storing credentials in the repository:

```powershell
$env:NO_PROXY="192.168.31.100"; $env:no_proxy="192.168.31.100"
$env:TRANSLATOR_BASE_URL="http://192.168.31.100:8045/v1"
$env:TRANSLATOR_API_KEY="<temporary-key>"
$env:TRANSLATOR_MODEL="gemini-3.8-flash-high"
python offline_anatomy_viewer/translate_with_openai.py --template <template-pack.json> --output offline_anatomy_viewer/translations/vi/BRAIN/mri-brain.json --checkpoint .translation-cache-mri-brain.json
```

The English `source` fields are preserved byte-for-byte at record level. Completed Vietnamese fields are enabled automatically (`reviewed`) under the user-requested publishing preference; no medical review is claimed. Pass `--require-review` to keep generated fields disabled. Source changes still become `needs_review` until retranslated. The checkpoint makes the process resumable and keeps occurrence IDs in their original positions.


## Translate a complete region (BRAIN)

`translate_region_openai.py` exports exact source-bound templates, reuses matching
completed translations, deduplicates text, sends bounded parallel requests to the
configured OpenAI-compatible endpoint, and checkpoints each completed batch.
The API key is read from `TRANSLATOR_API_KEY`; it is never written to output packs.
A rerun with the same work directory resumes interrupted requests. Use a new work
directory for a new source-data snapshot. All packs are validated before publishing.
English files are read-only; the run checks their SHA256 before installing outputs.

```powershell
# Reuse TRANSLATOR_BASE_URL / TRANSLATOR_API_KEY / TRANSLATOR_MODEL from the session.
python E:/coding/radiology/web/radiology-atlas-github/offline_anatomy_viewer/translate_region_openai.py --data-root E:/coding/radiology/web/imaios_data/all_modules --work-dir E:/coding/radiology/web/viewer_brain_vi_v63/work --output-root E:/coding/radiology/web/radiology-atlas-github/offline_anatomy_viewer/translations/vi --mirror-root E:/coding/radiology/web/offline_anatomy_viewer/translations/vi --workers 4
```

For one anatomical term, AI ranks the two closest meanings, with the best first.
The output uses `meaning 1 / meaning 2`; one precise meaning remains one.
Semicolons separating alternatives are normalized to `/`. More than two synonyms
are not displayed. Parenthetical qualifiers, numeric fractions and Latin identifiers
are preserved. Distinct grouped structures are not synonyms and use commas when
necessary. The two-meaning rule does not truncate description paragraphs or references.
Completion automatically enables fields per the user's publishing preference.
The same Vietnamese packs are mirrored into the local viewer and included by the
existing Dockerfile when the next image is built; no capture-data changes are needed.

## Translate the remaining catalogue

`translate_catalogue_openai.py` selects the catalogue's exact module keys and
excludes BRAIN by default, preserving its installed translations. It preserves
space-named region keys (for example `HEAD AND NECK`); do not rename these pack
folders to capture-data directory slugs such as `HEAD_AND_NECK`.

Large new repository packs use lossless `.json.gz` storage to keep each file
manageable for GitHub/image delivery. The viewer supports both `.json` and
`.json.gz` with the same schema/API; an existing `.json` takes precedence.
The local mirror remains ordinary JSON, including for an already-running viewer.
Compression does not change source strings, translations, or anatomical keys.
Incremental synchronization accepts either format and retains a byte-exact
`previous.json.gz` backup for compressed input; its editable result is `pack.json`.

The translator uses organ-specific context, not brain-specific definitions of
ambiguous terms such as ventricle, sinus or cortex. Deduplication stays within
each region. Synonyms use at most two ranked meanings separated by ` / `;
descriptions remain complete and bibliographic references stay original.

```powershell
# Set the existing TRANSLATOR_BASE_URL, TRANSLATOR_API_KEY and TRANSLATOR_MODEL
# in the current process environment. The tool does not persist the key.
python E:/coding/radiology/web/radiology-atlas-github/offline_anatomy_viewer/translate_catalogue_openai.py --data-root E:/coding/radiology/web/imaios_data/all_modules --work-dir E:/coding/radiology/web/viewer_all_vi_v65/work --output-root E:/coding/radiology/web/radiology-atlas-github/offline_anatomy_viewer/translations/vi --mirror-root E:/coding/radiology/web/offline_anatomy_viewer/translations/vi --workers 6
```

Reuse the same work directory to resume pending requests. A new source-data
snapshot needs a new work directory. Source-file hashes and file lists are
checked before installation. Every output retains exact source fields, identity
keys and bindings. The `previous` directory and `install_manifest.json` journal
retain original packs before replacement. No image, capture JSON or English
metadata file is changed by this command.
