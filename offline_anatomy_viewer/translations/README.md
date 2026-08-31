# Anatomy language packs

This directory belongs to the **viewer**, not the capture pipeline. No anatomical
translations have been supplied yet. The default source language is English;
`vi` is available in **Menu → Anatomy language** with source-English fallback.
Latin terminology and anatomical IDs are never translated. UI chrome is separate
from the anatomical-content language choice.

## Location and schema

One pack per module: `translations/<locale>/<REGION>/<module-slug>.json`.
The registry `languages.json` adds future languages without editing viewer code.

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

Each collection maps stable keys to entries:

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

Fill `translation` and mark an entry `status: "reviewed"` after checking the
anatomical meaning. Only reviewed, non-empty string fields whose `source` exactly
matches the current source field are used. Missing, empty, draft, stale-source or
invalid-pack fields fall back independently to English. Do not replace the source
values to force a stale translation to apply. Keep HTML formatting minimal;
the viewer sanitizes definition content. Name and label values render as text.

Language changes preserve filter IDs, marker coordinates, selected identity, slice,
zoom/pan and the preloaded image/JSON cache. Search supports both source names and
translated names/descriptions, Vietnamese with or without accents, and Latin.
Use Refresh or reselect a language after installing a pack to reload it.

## Export source-only slots

From the workspace root:

```powershell
python .\offline_anatomy_viewer\export_language_template.py --key BRAIN/mri-brain --locale vi --include-labels --output .\mri-brain.vi.draft.json
```

The exporter creates empty `draft` slots for current definitions, filters and,
optionally, every captured label occurrence. It never overwrites an existing file.
After translation and review, copy/merge the pack into the module location above.
Do not publish unreviewed generated translations as reviewed anatomical data.
