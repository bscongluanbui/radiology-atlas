# Radiology Atlas Offline Viewer

A multi-module English anatomy and radiology viewer that reads the captured data in `imaios_data/all_modules` without copying it or requiring internet access.

## Module thumbnails (v43)

The catalogue includes 84 original thumbnails from the [official IMAIOS e-Anatomy catalogue](https://www.imaios.com/en/e-anatomy), stored in `assets/module-icons/` (about 5.3 MiB total). No internet request is needed at runtime. The manifest records each module key, exact source URL, thumbnail URL and SHA-256; artwork and rights remain with IMAIOS.

`/api/catalogue` returns a local `icon_url` only when both the module key and source URL match the manifest and the asset exists. The 48px thumbnails preserve their original aspect ratio, load lazily, decode asynchronously and use a content-versioned browser cache. A missing or failed icon shows the existing modality badge instead. Anatomy labels, capture data, translations and series preloading are unchanged.

Restart the viewer server and reload the page after updating. When copying the viewer to another machine, include the `assets` folder.

## Detail sidebar (v44)

The toolbar's **Detail** button replaces **This slice / Search**. The two tabs inside the full sidebar retain their names and functionality.

Clicking an anatomical label or marker while Detail is hidden temporarily opens just its anatomical definition in the same sidebar. The **×** button or Escape dismisses this temporary panel and restores the closed layout; it does not save the full sidebar as open. With Detail already open, selection updates its normal definition area and **×** clears that definition without closing the list/search panel. Label identity, source definitions and language fallback remain unchanged.

## Left-button drag tools (v45)

Select a toolbar tool, then hold the left mouse button and drag on the image or a label:

- **Scroll:** drag down for later slices, up for earlier slices (8 screen pixels per slice).
- **Pan:** move the image in the drag direction.
- **Zoom:** drag up to enlarge and down to reduce, anchored at the starting pointer position.

Movement is batched once per animation frame. Slice dragging uses the existing cached latest-frame loader. Releasing the mouse applies its last queued movement; pointer cancellation, loss of focus, tool changes and series changes end the gesture. Right-button drags are ignored. A click or small jitter still opens the label's Detail, while an actual drag does not accidentally select a label. Mouse-wheel controls remain available.

## Structure selection and interface wording (v46)

The library uses **Available** / **All modules** and an available/total **MODULES** count. Display controls use **Overlays** without the former subtitle. Missing names/definitions remain explicitly unavailable, rather than being invented; internal data keys and capture files are unchanged.

Click a label, marker or structure row to emphasize its label, leader, connected bracket/stub and target. Other structures fade to 16% opacity but remain clickable. Selection uses scoped anatomy identities and the existing label-to-stroke ownership, never colour alone. Duplicate occurrences of the same scoped structure focus together; unknown identities stay local to their current slice. A selection absent from the visible slice does not dim all anatomy.

The **×** in Detail clears this emphasis. A selected structure takes visual priority over group hover/pinning; closing its definition restores the group highlight. Filters and Labels/Leaders/Targets switches still control visibility. Overlay masks remain anatomical-group masks, not individual-structure selections. Drag tools, Detail auto-open, translations and series caching are unchanged.

Restart the viewer server and reload with **Ctrl+F5** to refresh frontend wording and server-provided missing-name messages.

## Toggle selection and module discovery (v47)

Click the selected label, marker or structure row again to clear its highlight and definition; a third click selects it again. Enter/Space behave the same way. A temporary Detail panel closes, while an already-open full Detail panel remains open. Scoped anatomy IDs are compared; anonymous labels and markers only toggle their own occurrence. Programmatic definition refreshes remain idempotent.

The catalogue region name is not always the on-disk directory name: **HEAD AND NECK** uses `HEAD_AND_NECK`, **WHOLE BODY** uses `WHOLE_BODY`, and other multi-word regions follow the same rule. The viewer now resolves this consistently for module metadata, image/overlay/icon URLs and source definitions. Legacy space-named directories still work. API/module/language keys keep their original display-region spelling; no capture files are moved or changed.

A module becomes available when at least one image/label slice pair exists, not merely an empty series folder. Opening the module catalogue rescans availability without resetting the selected image, current structure, filters, or preloaded series cache. The **Refresh** button still explicitly reloads the active module to pick up newly completed slices or repaired data. An in-progress module only exposes the slice pairs already present.

Dark/black labels from illustration modules use light text against the dark viewer. This is presentation-only: original label colours, leader colours, positions and anatomy bindings are preserved. A selected black leader uses teal contrast on screen and returns to black when deselected.

After this update, stop the old viewer server, launch it again and reload the browser with **Ctrl+F5**. Reloading the webpage alone does not update the running Python server.

## Start

Double-click `START_OFFLINE_VIEWER.bat`, or run:

```powershell
python .\offline_anatomy_viewer\server.py --data-root .\imaios_data\all_modules
```

The local viewer normally opens at `http://127.0.0.1:8765/`. If that port is occupied or reserved, the launcher selects a free localhost port and prints the exact URL. Press `Ctrl+C` in the terminal to stop it.

### macOS one-click start

Keep `offline_anatomy_viewer` and `imaios_data` as sibling folders, then double-click:

```text
START_OFFLINE_VIEWER_MAC.command
```

The launcher locates its own folder, finds `../imaios_data/all_modules`, selects `python3`, starts the local server, and opens the default browser automatically. Keep its Terminal window open while studying; press `Control+C` to stop the viewer.

Python 3.9 or newer is required. If macOS reports that the file is not executable after copying it from another filesystem, run this once in Terminal and then use Finder normally:

```bash
chmod +x "/path/to/offline_anatomy_viewer/START_OFFLINE_VIEWER_MAC.command"
```

An alternate captured-data location can be selected before launch with `ANATOMY_DATA_ROOT`. `PYTHON_BIN` can similarly select a specific Python executable.

## Main controls

- **Scroll** is the default mode: the mouse wheel changes slices. After the first frame opens, the viewer preloads the complete active series into its versioned HTTP cache with four low-priority workers. All slice JSON for that series remains in the in-memory cache, while a larger bounded LRU window of 24 decoded images (16 ahead and 7 behind) avoids retaining hundreds of full RGBA frames. A fast wheel burst is coalesced to the latest requested position. The previous diagnostic frame stays painted while an uncached slice loads; the full-screen spinner is reserved for initial series loading. Opening another series cancels the old queue and replaces these caches with the new series.
- **Zoom** makes the mouse wheel zoom; the `−`, `FIT`, and `+` buttons always work.
- **Pan** lets you drag the current image.
- **Labels** toggles visible English text and leader geometry.
- **Targets** toggles visible-label markers and verified hidden markers.
- The top-left **Menu** button toggles the left anatomical-parts dock. The adjacent **Detail** button toggles the structure panel and its two tabs. Both controls remain in the left side of the top bar, even when their panels are hidden. The menu opens by default and reserves its own space, without covering or dimming the image. **FIT** includes the captured annotation bounds so long names stay inside the viewport.
- The interface uses a shadcn-inspired luxury dark system: layered zinc cards, fine borders, restrained gold highlights, strong focus rings, and elevated menu/tool surfaces while keeping turquoise for active medical-viewer controls.
- **Series** is integrated into the main toolbar. Its compact dropdown switches planes, variants, pulse sequences, and weightings.
- Click the current module name beside the viewer title to open the searchable module catalogue dropdown.
- **MPR** in the viewer toolbar opens captured orthogonal reference planes in an on-demand reference panel; the permanent MPR sidebar rail has been removed. The panel reserves its own space without covering or dimming the main image. Drag its right-edge resize handle, or focus the handle and use the arrow keys, to change panel width. Double-click the handle to restore 320 px.
- **FIT WIDTH** shows each complete captured reference image fitted to the available MPR width; **CROP VIEW** returns to the focused anatomical crop. The blue line stays mapped from the captured cross-reference payload in both views.
- Display switches independently show or hide labels, leader lines, markers, orientation markers, filmstrip, anatomy panel, and image-adjustment controls. MPR is controlled directly from the toolbar.
- **Anatomical parts** follows the module's captured parent-child filter hierarchy and names, without guessing from category codes. Expand a group to see its captured child layers. Hover to preview its annotations; use the adjacent double-circle button to highlight or pin the group.
- Group and layer switches now affect visible English labels, their leader lines and marker dots, verified hidden markers, and the current-slice structure list. **Select all** controls every captured leaf layer.
- Label brackets and short connector stubs follow the same filter as their captured label/connected leader, so disabling anatomical parts also removes those strokes. Shared captured strokes remain while any associated label is enabled; unknown anatomy identities are not guessed.
- All captured layers are enabled when a module opens. **Show all names** also restores label visibility and clears temporary highlights; **Source defaults** explicitly applies the original `filter.active` selection. The status line distinguishes visible labels, named hover markers, and labels without a verified part link. Unlinked label text remains visible.
- Some older captures lack translated filter names. The server recovers a name only from an unambiguous translation-key match to a validated structure in the same module. Remaining entries say **Name unavailable (filter ID)**, and the menu reports their count. Complete `filters_resolved.json` metadata is needed to replace those placeholders; captured data is never rewritten by the viewer.
- **Display options** is a collapsible section below the anatomical-parts list.
- Click any label, target, or structure-list row to open its English definition, Latin term, references, and identifiers. Use the **×** button in the definition card to clear the selection.
- Use the top module catalogue and toolbar Series selector to navigate modules, planes, and variants.
- Brightness, contrast, cine, filmstrip, fullscreen, keyboard shortcuts, local preferences, and live catalogue refresh are included.

The viewer scans current capture output on launch and on **Refresh**, so newly completed series appear without rebuilding the app.
Refresh also advances the viewer's data revision and clears its bounded slice caches, so newly captured files replace older in-memory frames.

## Verification

```powershell
python .\offline_anatomy_viewer\server.py --self-test
python .\offline_anatomy_viewer\verify_viewer.py
```


## Official pixel overlay support (V39)
The default viewer now renders validated transparent group/layer masks with opacity, filter visibility and group highlighting. The toolbar MPR and topbar panel controls remain unchanged. Masks are bound to the saved base image hash and affine placement, not inferred from marker colour.

Old core-PASS captures can still lack masks. Run `python "E:\coding\radiology\web\audit_capture_data.py" --repair --repair-overlays --login-timeout 1800` to fill missing layers. Add `--module-exact mri-brain` to target one module. Valid layers are skipped; layer-only repair preserves existing core. Use `--repair --repair-deep` only when deeper optional/alignment repair is required. Restart the viewer server to load promoted code.

## Anatomy languages

Menu → Anatomy language offers English and Tiếng Việt. Vietnamese packs are empty placeholders for later translation; missing fields stay in source English. IDs, Latin terms, geometry and capture data remain unchanged. See `translations/README.md` for schema, exact-source review rules, and the source-only template exporter. Language switching does not flush the slice/image preload cache.

## Ubuntu Docker / ARM64 / AMD64

Bản server dùng chung mã viewer trong thư mục này. Xem ../docker/README.md để cấu hình DDNS + HTTPS, quản trị tài khoản, phân quyền module, cache có giới hạn và cập nhật/rollback.
Không thay đổi capture/data. Local launcher vẫn chạy độc lập trên loopback; Docker chỉ thêm gateway đăng nhập và cache ảnh RAM có giới hạn cho bản remote.
Đóng gói update từ thư mục gốc: python docker/release.py.
