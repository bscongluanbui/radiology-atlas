"""API and browser interaction verification for the multi-module offline viewer."""

from __future__ import annotations

import asyncio
import json
import os
import threading
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from playwright.async_api import async_playwright

from server import DEFAULT_DATA_ROOT, build_server


TEST_DATA_ROOT = Path(os.environ.get("ANATOMY_DATA_ROOT", DEFAULT_DATA_ROOT)).resolve()
PREVIEW_PATH = Path(__file__).resolve().parent / "viewer_preview.png"
FILTER_PREVIEW_PATH = Path(__file__).resolve().parent / "filter_hierarchy_preview.png"
HIGHLIGHT_PREVIEW_PATH = Path(__file__).resolve().parent / "filter_highlight_preview.png"
DOCK_PREVIEW_PATH = Path(__file__).resolve().parent / "left_dock_preview.png"
MPR_PREVIEW_PATH = Path(__file__).resolve().parent / "mpr_toolbar_preview.png"


async def verify() -> int:
    server = build_server(TEST_DATA_ROOT, 0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    port = server.server_address[1]
    url = f"http://127.0.0.1:{port}/"
    try:
        with urllib.request.urlopen(f"{url}api/catalogue", timeout=30) as response:
            catalogue_status = response.status
            catalogue = json.loads(response.read())
        module_query = urllib.parse.urlencode({"key": "BRAIN/mri-brain"})
        with urllib.request.urlopen(f"{url}api/module?{module_query}", timeout=30) as response:
            module_status = response.status
            module = json.loads(response.read())
        assert module_status == 200
        assert module["anatomical_parts"]["source"] == "filters_resolved.json"
        assert module["anatomical_parts"]["interaction_model"]["point_membership_field"] == "point.filter_id"
        assert len(module["filters"]) == 37 and all(item.get("name") for item in module["filters"])
        assert {item["name"] for item in module["filters"]} >= {"Telencephalon", "Cerebral sulci", "Meninges"}
        api_series = next(row for row in module["series"] if row["slice_count"])
        api_variant = next(row for row in api_series["variants"] if row["slice_count"])
        slice_query = urllib.parse.urlencode({
            "key": module["key"], "series": api_series["directory"],
            "variant": api_variant["directory"], "slice": api_variant["slices"][0],
        })
        with urllib.request.urlopen(f"{url}api/slice?{slice_query}", timeout=30) as response:
            slice_cache_control = response.headers.get("Cache-Control")
            api_capture = json.loads(response.read())
        with urllib.request.urlopen(f"{url}{api_capture['image_url']}", timeout=30) as response:
            image_cache_control = response.headers.get("Cache-Control")
            response.read(1)
        with urllib.request.urlopen(f"{url}api/slice?{slice_query}&rev=viewer-test", timeout=30) as response:
            versioned_slice_cache_control = response.headers.get("Cache-Control")
            response.read(1)
        separator = "&" if "?" in api_capture["image_url"] else "?"
        with urllib.request.urlopen(f"{url}{api_capture['image_url']}{separator}v=viewer-test", timeout=30) as response:
            versioned_image_cache_control = response.headers.get("Cache-Control")
            response.read(1)
        assert slice_cache_control == "private, max-age=60"
        assert image_cache_control == "public, max-age=300"
        assert versioned_slice_cache_control == "private, max-age=31536000, immutable"
        assert versioned_image_cache_control == "public, max-age=31536000, immutable"
        with urllib.request.urlopen(f"{url}{module['filters'][0]['icon_url']}", timeout=30) as response:
            icon_status = response.status
            icon_type = response.headers.get_content_type()
            icon_bytes = len(response.read())
        assert icon_status == 200 and icon_type == "image/png" and icon_bytes > 0
        traversal_status = 0
        try:
            urllib.request.urlopen(f"{url}data/%2e%2e/module_catalogue.json", timeout=10)
        except urllib.error.HTTPError as error:
            traversal_status = error.code
        assert catalogue_status == 200 and catalogue["captured_module_count"] >= 1
        assert traversal_status in {400, 404}

        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch(headless=True)
            page = await browser.new_page(viewport={"width": 1600, "height": 1000})
            page_errors: list[str] = []
            page.on("pageerror", lambda error: page_errors.append(str(error)))
            await page.goto(url, wait_until="domcontentloaded")
            await page.locator("#app[aria-busy='false']").wait_for(timeout=60_000)
            await page.wait_for_function("document.querySelector('#anatomyImage').complete && document.querySelector('#anatomyImage').naturalWidth > 0")
            await page.wait_for_timeout(300)

            # The left menu opens by default and takes its own space, not a modal scrim.
            assert await page.locator("#optionsMenu").get_attribute("aria-hidden") == "false"
            assert await page.locator("#menuScrim").count() == 0
            initial_menu = await page.locator("#optionsMenu").bounding_box()
            initial_viewport = await page.locator("#anatomyViewport").bounding_box()
            assert initial_menu["x"] == 0 and initial_menu["width"] >= 260
            assert abs(initial_viewport["x"] - initial_menu["x"] - initial_menu["width"]) <= 1
            assert (await page.locator("#mprPanel").bounding_box())["width"] == 0
            assert await page.locator("#optionsMenu").evaluate("node => getComputedStyle(node).backdropFilter") == "none"
            assert await page.locator("#optionsMenuButton").get_attribute("aria-expanded") == "true"
            left_controls = page.locator(".left-topbar-controls .tool-button")
            assert await left_controls.count() == 2
            menu_button_box = await page.locator("#optionsMenuButton").bounding_box()
            detail_button_box = await page.locator("#detailDrawerButton").bounding_box()
            assert menu_button_box and detail_button_box and detail_button_box["x"] > menu_button_box["x"]
            assert detail_button_box["y"] < initial_menu["y"]
            assert await page.locator("#detailPanel").is_visible()
            await page.locator("#detailDrawerButton").click()
            await page.wait_for_function("document.querySelector('#app').classList.contains('hide-details')")
            assert await page.locator("#detailDrawerButton").get_attribute("aria-expanded") == "false"
            assert await page.locator("#detailPanel").is_hidden()
            await page.locator("#detailDrawerButton").click()
            await page.wait_for_function("!document.querySelector('#app').classList.contains('hide-details')")
            assert await page.locator("#detailPanel").is_visible()
            await page.locator("#optionsMenuCloseButton").click()
            assert await page.locator("#optionsMenu").is_hidden()

            await page.locator("#moduleCatalogueButton").click()
            await page.locator("#moduleCataloguePopover").wait_for(state="visible", timeout=5_000)
            module_buttons = await page.locator(".module-button:not(.disabled)").count()
            assert await page.locator("#moduleCatalogueButton").get_attribute("aria-expanded") == "true"
            await page.locator("#moduleCatalogueButton").click()
            await page.locator("#moduleCataloguePopover").wait_for(state="hidden", timeout=5_000)
            series_options = await page.locator("#toolbarWeightingSelect option").count()
            label_count = await page.locator(".annotation-label").count()
            target_count = await page.locator(".annotation-target, .hover-dot").count()
            structure_count = await page.locator(".structure-row").count()
            assert module_buttons >= 1 and series_options >= 2
            assert label_count >= 1 and target_count >= 1 and structure_count >= 1

            assert await page.locator("#seriesTree").count() == 0
            await page.locator("#optionsMenuButton").click()
            await page.locator("#optionsMenu.open").wait_for(timeout=5_000)
            menu_box = await page.locator("#optionsMenu").bounding_box()
            luxury_badge_text = await page.locator(".luxury-ui-badge").inner_text()
            luxury_card_count = await page.locator("#optionsMenu .options-section-card").count()
            luxury_card_style = await page.locator("#optionsMenu .options-section-card").first.evaluate(
                "node => ({radius: getComputedStyle(node).borderRadius, border: getComputedStyle(node).borderTopWidth, background: getComputedStyle(node).backgroundColor, shadow: getComputedStyle(node).boxShadow})"
            )
            luxury_topbar_style = await page.locator(".luxury-nav").evaluate(
                "node => ({border: getComputedStyle(node).borderBottomWidth, background: getComputedStyle(node).backgroundImage, shadow: getComputedStyle(node).boxShadow})"
            )
            luxury_toolbar_style = await page.locator(".luxury-toolbar").evaluate(
                "node => ({border: getComputedStyle(node).borderBottomWidth, background: getComputedStyle(node).backgroundImage, shadow: getComputedStyle(node).boxShadow})"
            )
            assert menu_box and menu_box["width"] >= 260 and menu_box["x"] == 0
            assert luxury_badge_text == "LOCAL" and luxury_card_count == 2
            assert float(luxury_card_style["radius"].removesuffix("px")) >= 10
            assert luxury_card_style["border"] != "0px" and luxury_card_style["shadow"] != "none"
            assert luxury_topbar_style["border"] != "0px" and luxury_topbar_style["background"] != "none"
            assert luxury_toolbar_style["border"] != "0px" and luxury_toolbar_style["shadow"] != "none"
            menu_filter_count = await page.locator("#menuFilterList .menu-filter-row").count()
            menu_filter_group_count = await page.locator("#menuFilterList .menu-filter-group").count()
            menu_group_names = await page.locator("#menuFilterList .menu-filter-group-expand b").all_inner_texts()
            default_checked_filters = await page.locator("#menuFilterList .menu-filter-row input:checked").count()
            captured_filter_icons = await page.locator("#menuFilterList .menu-filter-icon img").count()
            highlight_button_count = await page.locator("#menuFilterList .filter-highlight-button").count()
            assert menu_filter_count >= 1
            assert 1 < menu_filter_group_count < menu_filter_count
            assert {"Telencephalon", "Cerebellum", "Brainstem", "Veins / Venous sinuses"}.issubset(set(menu_group_names))
            assert default_checked_filters == menu_filter_count
            assert captured_filter_icons == len(module["filters"]) and highlight_button_count == len(module["filters"])
            assert await page.locator("#menuFilterList .menu-filter-children:visible").count() == 0
            await page.locator('#menuFilterList .menu-filter-group[data-group-name="Telencephalon"] .menu-filter-group-expand').click()
            assert await page.locator('#menuFilterList .menu-filter-group[data-group-name="Telencephalon"] .menu-filter-children:visible').count() == 1
            telencephalon_names = await page.locator('#menuFilterList .menu-filter-group[data-group-name="Telencephalon"] .menu-filter-row b').all_inner_texts()
            assert {"Cerebral sulci", "Frontal lobe", "Brodmann areas"}.issubset(set(telencephalon_names))
            await page.locator('#menuFilterList .menu-filter-group[data-group-name="Telencephalon"] .menu-filter-icon img').first.wait_for(state="visible")
            assert await page.locator('#menuFilterList .menu-filter-group[data-group-name="Telencephalon"] .menu-filter-icon img').first.evaluate("image => image.complete && image.naturalWidth > 0")
            await page.screenshot(path=str(FILTER_PREVIEW_PATH), full_page=True)
            assert menu_box and menu_box["x"] == 0
            assert await page.locator("#optionsMenuButton").get_attribute("aria-expanded") == "true"
            await page.locator("#sourceFilterDefaultsButton").click()
            source_default_labels = await page.locator(".annotation-label").count()
            assert source_default_labels < label_count
            await page.locator("#showAllAnatomyButton").click()
            assert await page.locator(".annotation-label").count() == label_count
            assert "labels shown" in await page.locator("#anatomyNameStatus").inner_text()

            await page.locator("#menuOrientationToggle").evaluate(
                "(input, checked) => { input.checked = checked; input.dispatchEvent(new Event('change', {bubbles: true})); }", False
            )
            assert await page.locator("#app").evaluate("node => node.classList.contains('hide-orientation')")
            await page.locator("#menuOrientationToggle").evaluate(
                "(input, checked) => { input.checked = checked; input.dispatchEvent(new Event('change', {bubbles: true})); }", True
            )
            await page.locator("#menuLeadersToggle").evaluate(
                "(input, checked) => { input.checked = checked; input.dispatchEvent(new Event('change', {bubbles: true})); }", False
            )
            assert await page.locator("#annotationLayer").evaluate("node => node.classList.contains('leaders-hidden')")
            await page.locator("#menuLeadersToggle").evaluate(
                "(input, checked) => { input.checked = checked; input.dispatchEvent(new Event('change', {bubbles: true})); }", True
            )
            await page.locator("#menuFilmstripToggle").evaluate(
                "(input, checked) => { input.checked = checked; input.dispatchEvent(new Event('change', {bubbles: true})); }", False
            )
            assert await page.locator("#app").evaluate("node => node.classList.contains('hide-filmstrip')")
            await page.locator("#menuFilmstripToggle").evaluate(
                "(input, checked) => { input.checked = checked; input.dispatchEvent(new Event('change', {bubbles: true})); }", True
            )
            assert await page.locator(".viewer-toolbar #mprToggleButton").count() == 1
            assert await page.locator(".workspace > #mprPanel .mpr-toggle-button").count() == 0
            assert await page.locator("#menuMprToggle").count() == 0
            await page.locator(".viewer-toolbar #mprToggleButton").click()
            await page.wait_for_function("document.querySelector('#app').classList.contains('mpr-open')")
            await page.locator("#mprContent").wait_for(state="visible", timeout=5_000)
            open_mpr = await page.locator("#mprPanel").bounding_box()
            open_menu = await page.locator("#optionsMenu").bounding_box()
            open_viewport = await page.locator("#anatomyViewport").bounding_box()
            assert abs(open_mpr["x"] - open_menu["width"]) <= 1
            assert abs(open_viewport["x"] - open_mpr["x"] - open_mpr["width"]) <= 1
            assert await page.locator("#mprToggleButton").get_attribute("aria-expanded") == "true"
            await page.locator("#mprToggleButton").click()
            assert await page.locator("#mprContent").is_hidden()
            assert await page.locator("#mprToggleButton").get_attribute("aria-expanded") == "false"
            assert (await page.locator("#mprPanel").bounding_box())["width"] == 0
            await page.locator("#mprToggleButton").focus()
            await page.keyboard.press("Enter")
            await page.locator("#mprContent").wait_for(state="visible")
            await page.locator("#mprCloseButton").click()
            assert await page.locator("#mprContent").is_hidden()
            assert await page.locator("#mprToggleButton").evaluate("node => node === document.activeElement")
            await page.locator("#mprToggleButton").click()
            mpr_cards = await page.locator("#mprViews .mpr-card").count()
            mpr_cross_references = await page.locator("#mprViews .mpr-cross-reference polyline").count()
            assert mpr_cards >= 2 and mpr_cross_references >= 2
            await page.locator("#optionsMenuCloseButton").click()
            await page.wait_for_timeout(250)
            assert not await page.locator("#optionsMenu").evaluate("node => node.classList.contains('open')")
            await page.wait_for_timeout(280)
            mpr_width_before = (await page.locator("#mprPanel").bounding_box())["width"]
            resize_box = await page.locator("#mprResizeHandle").bounding_box()
            await page.mouse.move(resize_box["x"] + resize_box["width"] / 2, resize_box["y"] + 120)
            await page.mouse.down()
            await page.mouse.move(resize_box["x"] + resize_box["width"] / 2 + 72, resize_box["y"] + 120, steps=6)
            await page.mouse.up()
            await page.wait_for_timeout(80)
            mpr_width_after = (await page.locator("#mprPanel").bounding_box())["width"]
            assert mpr_width_after >= mpr_width_before + 65
            await page.locator("#mprFitWidthButton").click()
            await page.wait_for_function(
                "getComputedStyle(document.querySelector('#mprViews .mpr-frame img')).transform === 'none'"
            )
            fit_frame_width = (await page.locator("#mprViews .mpr-frame").first.bounding_box())["width"]
            fit_image_width = (await page.locator("#mprViews .mpr-frame img").first.bounding_box())["width"]
            assert abs(fit_frame_width - fit_image_width) <= 1
            assert await page.locator("#mprFitWidthButton").get_attribute("aria-pressed") == "true"
            await page.screenshot(path=str(MPR_PREVIEW_PATH), full_page=True)

            await page.locator("#menuSelectAllFilters").evaluate(
                "(input, checked) => { input.checked = checked; input.dispatchEvent(new Event('change', {bubbles: true})); }", False
            )
            assert await page.locator("#menuFilterList input:checked").count() == 0
            assert await page.locator("#annotationLayer .annotation-label").count() == 0
            assert await page.locator("#annotationLayer .annotation-line").count() == 0
            assert await page.locator("#annotationLayer").is_hidden()
            assert await page.locator("#annotationLayer .annotation-target, #annotationLayer .hover-dot").count() == 0
            assert await page.locator("#structureList .structure-row").count() == 0
            await page.locator("#menuSelectAllFilters").evaluate(
                "(input, checked) => { input.checked = checked; input.dispatchEvent(new Event('change', {bubbles: true})); }", True
            )
            assert await page.locator("#menuFilterList input:not(:checked)").count() == 0
            all_filter_label_count = await page.locator("#annotationLayer .annotation-label").count()
            assert await page.locator("#annotationLayer").is_visible()
            assert all_filter_label_count >= label_count

            await page.locator("#toolbarWeightingSelect").select_option(index=1)
            await page.locator("#sliceMeta", has_text="CORONAL").wait_for(timeout=20_000)
            selected_weighting = await page.locator("#toolbarWeightingSelect option:checked").inner_text()
            await page.locator("#toolbarWeightingSelect").select_option(index=0)
            await page.locator("#sliceMeta", has_text="AXIAL").wait_for(timeout=20_000)

            first_label = page.locator(".annotation-label").first
            label_name = await first_label.get_attribute("aria-label")
            await first_label.click(force=True)
            await page.locator("#definitionPanel h3").wait_for(timeout=10_000)
            definition_name = await page.locator("#definitionPanel h3").inner_text()
            assert definition_name
            await page.screenshot(path=str(PREVIEW_PATH), full_page=True)
            await page.locator("#definitionPanel .definition-close-button").click()
            await page.locator("#definitionPanel .definition-placeholder").wait_for(timeout=5_000)
            assert await page.locator("#definitionPanel h3").count() == 0
            assert await page.locator("#annotationLayer .is-selected").count() == 0
            definition_close = "PASS"

            initial_slice = int(await page.locator("#sliceSlider").input_value())
            initial_zoom = await page.locator("#zoomValue").inner_text()
            initial_cross_reference = await page.locator("#mprViews .mpr-cross-reference polyline").first.get_attribute("points")
            await page.evaluate("window.__stableFilterNode = document.querySelector('#menuFilterList .menu-filter-row')")
            await page.dispatch_event("#anatomyViewport", "wheel", {"deltaY": 120, "deltaMode": 0})
            await page.wait_for_function(
                "value => Number(document.querySelector('#sliceSlider').value) === value + 1", arg=initial_slice
            )
            scrolled_slice = int(await page.locator("#sliceSlider").input_value())
            scrolled_zoom = await page.locator("#zoomValue").inner_text()
            await page.wait_for_function(
                "value => document.querySelector('#mprViews .mpr-cross-reference polyline')?.getAttribute('points') !== value",
                arg=initial_cross_reference,
            )
            scrolled_cross_reference = await page.locator("#mprViews .mpr-cross-reference polyline").first.get_attribute("points")
            assert scrolled_zoom == initial_zoom
            assert await page.evaluate("window.__stableFilterNode === document.querySelector('#menuFilterList .menu-filter-row')")
            await page.wait_for_function(
                """() => {
                    const d = window.viewerSliceCacheDiagnostics?.();
                    return d && d.seriesPreloadTotal > 0 && d.seriesPreloadReady;
                }""",
                timeout=90_000,
            )
            cache_diagnostics = await page.evaluate("window.viewerSliceCacheDiagnostics()")
            assert cache_diagnostics["captureLimit"] >= cache_diagnostics["seriesPreloadTotal"]
            assert cache_diagnostics["resourceLimit"] >= cache_diagnostics["seriesPreloadTotal"]
            assert cache_diagnostics["imageLimit"] == 24
            assert cache_diagnostics["readyCaptures"] == cache_diagnostics["seriesPreloadTotal"]
            assert cache_diagnostics["readyResources"] == cache_diagnostics["seriesPreloadTotal"]
            assert 2 <= cache_diagnostics["readyImages"] <= cache_diagnostics["imageLimit"]
            assert cache_diagnostics["seriesPreloadFailed"] == 0

            await page.locator("#zoomModeButton").click()
            zoom_slice_before = int(await page.locator("#sliceSlider").input_value())
            zoom_before = await page.locator("#zoomValue").inner_text()
            await page.dispatch_event("#anatomyViewport", "wheel", {"deltaY": -120, "deltaMode": 0})
            await page.wait_for_function(
                "value => document.querySelector('#zoomValue').textContent !== value", arg=zoom_before
            )
            zoom_slice_after = int(await page.locator("#sliceSlider").input_value())
            zoom_after = await page.locator("#zoomValue").inner_text()
            assert zoom_slice_before == zoom_slice_after and zoom_before != zoom_after

            await page.locator("#labelsButton").click()
            labels_hidden = await page.locator("#annotationLayer").evaluate("node => node.classList.contains('labels-hidden')")
            assert labels_hidden
            await page.locator("#labelsButton").click()
            assert await page.locator("#filmstrip .filmstrip-button").count() >= 2
            assert await page.locator("#scrollModeButton").is_visible()
            assert await page.locator("#panModeButton").is_visible()
            assert await page.locator("#fullscreenButton").is_visible()

            await page.locator("#sliceSlider").evaluate(
                "slider => { slider.value = '15'; slider.dispatchEvent(new Event('input', {bubbles: true})); }"
            )
            await page.wait_for_function(
                "() => document.querySelector('#sliceSlider').value === '15' && document.querySelector('#sliceNumber').textContent === '0015' && document.querySelector('#loadingState').hidden && !document.querySelector('#app').classList.contains('slice-fetching')"
            )
            await page.wait_for_function("document.querySelectorAll('#annotationLayer .annotation-label[data-filter-id=\"10914\"]').length > 0")
            meninges_labels_before = await page.locator('#annotationLayer .annotation-label[data-filter-id="10914"]').count()
            meninges_leaders_before = await page.locator('#annotationLayer .annotation-line[data-filter-id="10914"]').count()
            await page.locator("#optionsMenuButton").click()
            await page.locator("#optionsMenu.open").wait_for(timeout=5_000)
            meninges_group = page.locator('#menuFilterList .menu-filter-group[data-group-name="Meninges"]')
            meninges_row = page.locator('#menuFilterList .menu-filter-row[data-filter-id="10914"]')
            await page.locator("#optionsMenu .options-section-heading").first.hover()
            await page.locator('#menuFilterList .menu-filter-row[data-filter-id="10914"] input').evaluate(
                "input => { input.checked = false; input.dispatchEvent(new Event('change', {bubbles: true})); }"
            )
            assert await page.locator('#annotationLayer .annotation-label[data-filter-id="10914"]').count() == 0
            assert await page.locator('#annotationLayer .annotation-line[data-filter-id="10914"]').count() == 0
            await meninges_row.hover()
            await page.wait_for_function(
                "count => document.querySelectorAll('#annotationLayer .annotation-label[data-filter-id=\"10914\"]').length === count",
                arg=meninges_labels_before,
            )
            preview_labels = await page.locator('#annotationLayer .annotation-label[data-filter-id="10914"]').count()
            await page.locator("#optionsMenu .options-section-heading").first.hover()
            await page.wait_for_function("document.querySelectorAll('#annotationLayer .annotation-label[data-filter-id=\"10914\"]').length === 0")
            await page.locator('#menuFilterList .menu-filter-row[data-filter-id="10914"] input').evaluate(
                "input => { input.checked = true; input.dispatchEvent(new Event('change', {bubbles: true})); }"
            )
            assert await page.locator('#annotationLayer .annotation-label[data-filter-id="10914"]').count() == meninges_labels_before
            highlight_button = page.locator('#menuFilterList .menu-filter-row[data-filter-id="10914"] .filter-highlight-button')
            await highlight_button.hover()
            await page.wait_for_function("document.querySelectorAll('#annotationLayer .is-filter-highlighted').length > 0")
            highlighted_labels = await page.locator('#annotationLayer .annotation-label.is-filter-highlighted[data-filter-id="10914"]').count()
            muted_labels = await page.locator('#annotationLayer .annotation-label.is-filter-muted').count()
            assert highlighted_labels == meninges_labels_before and muted_labels > 0
            filter_scroll_before = await page.locator("#optionsMenu").evaluate("node => node.scrollTop")
            await highlight_button.click()
            highlight_button = page.locator('#menuFilterList .menu-filter-row[data-filter-id="10914"] .filter-highlight-button')
            filter_scroll_after = await page.locator("#optionsMenu").evaluate("node => node.scrollTop")
            assert abs(filter_scroll_after - filter_scroll_before) <= 1
            assert await highlight_button.get_attribute("aria-pressed") == "true"
            await page.mouse.move(700, 180)
            assert await page.locator('#annotationLayer .annotation-label.is-filter-highlighted[data-filter-id="10914"]').count() == meninges_labels_before
            await page.screenshot(path=str(HIGHLIGHT_PREVIEW_PATH), full_page=True)
            await highlight_button.click()
            assert await page.locator("#annotationLayer .is-filter-highlighted").count() == 0
            await page.locator("#optionsMenuCloseButton").click()
            await page.wait_for_timeout(280)
            assert not await page.locator("#optionsMenu").evaluate("node => node.classList.contains('open')")
            hidden_target = page.locator(".hover-dot").first
            await hidden_target.wait_for(timeout=20_000)
            hidden_name = await hidden_target.get_attribute("aria-label")
            await hidden_target.hover(force=True)
            await page.locator("#anatomyTooltip:not([hidden])").wait_for(timeout=5_000)
            hidden_tooltip = await page.locator("#anatomyTooltip strong").inner_text()
            assert hidden_name == hidden_tooltip

            # Keep a clear-image screenshot at a representative brain slice.
            await page.evaluate("async () => { await setSlicePosition(60); setAllAnatomyVisible(); setMprVisible(false); openOptionsMenu({focus:false}); }")
            await page.wait_for_timeout(300)
            await page.locator("#menuFilterList .menu-filter-group[data-group-name='Telencephalon'] .menu-filter-group-expand").evaluate("node => { if (node.getAttribute('aria-expanded') !== 'true') node.click(); }")
            await page.locator("#optionsMenu").evaluate("node => node.scrollTop=0")
            await page.mouse.move(1000, 160)
            await page.screenshot(path=str(DOCK_PREVIEW_PATH), full_page=True)
            responsive_sizes = []
            for width in (1600, 1280, 800, 640):
                await page.set_viewport_size({"width": width, "height": 1000})
                await page.wait_for_timeout(300)
                dock = await page.locator("#optionsMenu").bounding_box()
                viewport = await page.locator("#anatomyViewport").bounding_box()
                assert dock["x"] == 0 and viewport["x"] >= dock["width"] - 1, (width, dock, viewport)
                assert viewport["width"] > 0 and viewport["x"] + viewport["width"] <= width + 1, (width, viewport)
                label_bounds = await page.locator(".annotation-label").evaluate_all("nodes => nodes.map(node => { const r=node.getBoundingClientRect(); return {left:r.left,right:r.right}; })")
                assert all(b["left"] >= viewport["x"] - 1 and b["right"] <= viewport["x"] + viewport["width"] + 1 for b in label_bounds), (width, label_bounds)
                assert await page.locator("#menuScrim").count() == 0
                assert abs(viewport["x"] - dock["width"]) <= 1
                await page.locator("#mprToggleButton").click()
                reference_panel = await page.locator("#mprPanel").bounding_box()
                mpr_viewport = await page.locator("#anatomyViewport").bounding_box()
                assert abs(reference_panel["x"] - dock["width"]) <= 1
                assert abs(mpr_viewport["x"] - reference_panel["x"] - reference_panel["width"]) <= 1
                assert mpr_viewport["width"] > 0 and mpr_viewport["x"] + mpr_viewport["width"] <= width + 1
                await page.locator("#mprToggleButton").click()
                assert await page.locator("#mprContent").is_hidden()
                assert (await page.locator("#mprPanel").bounding_box())["width"] == 0
                responsive_sizes.append(width)

            await page.set_viewport_size({"width": 1600, "height": 1000})
            name_checks = []
            for key in ("SPINE/mri-cervical-spine", "SPINE/mri-lumbar-spine", "SPINE/ct-lumbar-spine"):
                await page.evaluate("key => selectModule(key)", key)
                audit = await page.evaluate("""() => ({
                    key: state.module.key, total: state.module.filters.length,
                    named: state.module.anatomical_parts.resolved_name_count,
                    recovered: state.module.anatomical_parts.recovered_name_count,
                    missing: state.module.anatomical_parts.missing_name_filter_ids,
                    names: state.module.filters.map(f => filterDisplayName(f)),
                    groupCount: filterHierarchyGroups().length,
                    rootCount: state.module.anatomical_parts.roots.length,
                    blankLabels: state.capture.labels.filter(l=>!String(l.text||'').trim()).length,
                    unboundLabels: state.capture.labels.filter(l=>l.filter_id == null).length
                })""")
                assert all(audit["names"]) and audit["groupCount"] == audit["rootCount"]
                assert audit["named"] + len(audit["missing"]) == audit["total"]
                assert 0 <= audit["recovered"] <= audit["named"]
                assert audit["blankLabels"] == 0
                assert await page.locator("#filterMetadataStatus").is_visible() == bool(audit["missing"])
                assert ("no verified part link" in await page.locator("#anatomyNameStatus").inner_text()) == bool(audit["unboundLabels"])
                name_checks.append({k:v for k,v in audit.items() if k != 'names'})
            (Path(__file__).resolve().parent / "name_audit.json").write_text(json.dumps(name_checks, indent=2), encoding="utf-8")
            await page.evaluate("key => selectModule(key)", "BRAIN/mri-brain")
            assert await page.locator("#filterMetadataStatus").is_hidden()
            assert not page_errors, page_errors
            await browser.close()

        print("RADIOLOGY-ATLAS-BROWSER-TEST")
        print(f"catalogue=HTTP_{catalogue_status},captured_{catalogue['captured_module_count']},traversal_HTTP_{traversal_status}")
        print(f"navigation=top_catalogue_modules_{module_buttons},toolbar_series_options_{series_options},legacy_tree_removed_true")
        print(f"luxury_ui=drawer_width_{menu_box['width']:.0f},badge_{luxury_badge_text},cards_{luxury_card_count},radius_{luxury_card_style['radius']},topbar_border_{luxury_topbar_style['border']},toolbar_shadow_PASS")
        print(f"left_controls=menu_and_details_adjacent,details_toggle_hide_show_PASS")
        print(f"options_menu=left_docked_default_open,scrim_absent,image_unobscured,filter_groups_{menu_filter_group_count},leaf_filters_{menu_filter_count},all_names_default_{default_checked_filters}/{menu_filter_count},icons_{captured_filter_icons},highlight_buttons_{highlight_button_count}")
        print(f"responsive=no_overlap_and_no_clipped_names_widths_{'_'.join(map(str,responsive_sizes))},MPR_open_close_PASS")
        print(f"names=source_default_labels_{source_default_labels},show_all_labels_{label_count},spine_recovered_{sum(x['recovered'] for x in name_checks)},spine_explicit_missing_{sum(len(x['missing']) for x in name_checks)},no_category_guess_PASS")
        print(f"dock_preview={DOCK_PREVIEW_PATH}")
        print(f"anatomical_parts=source_filters_resolved.json,names_37/37,icon_HTTP_{icon_status}_{icon_type}_{icon_bytes}B,select_all_PASS,meninges_hide_{meninges_labels_before}->0,preview_0->{preview_labels}->0,highlight_{highlighted_labels},muted_{muted_labels},pinned_toggle_PASS,scroll_{filter_scroll_before}->{filter_scroll_after}")
        print(f"mpr=toolbar_button_no_sidebar,docked_on_demand,no_image_overlap,mouse_keyboard_close_PASS,cards_{mpr_cards},captured_cross_references_{mpr_cross_references},width_{mpr_width_before:.0f}->{mpr_width_after:.0f},fit_width_{fit_image_width:.0f}/{fit_frame_width:.0f},line_{initial_cross_reference}->{scrolled_cross_reference}")
        print(f"mpr_preview={MPR_PREVIEW_PATH}")
        print(f"weighting_dropdown={selected_weighting},series_change_AXIAL_CORONAL_AXIAL")
        print(f"anatomy=default_labels_{label_count},all_filter_labels_{all_filter_label_count},targets_{target_count},structures_{structure_count}")
        print(f"preview={PREVIEW_PATH}")
        print(f"filter_preview={FILTER_PREVIEW_PATH}")
        print(f"highlight_preview={HIGHLIGHT_PREVIEW_PATH}")
        print(f"definition={label_name}->{definition_name},close_{definition_close}")
        print(f"wheel_scroll=slice_{initial_slice}->{scrolled_slice},zoom_{initial_zoom}->{scrolled_zoom}")
        print(f"slice_cache=full_series_{cache_diagnostics['seriesPreloadCompleted']}/{cache_diagnostics['seriesPreloadTotal']},captures_{cache_diagnostics['readyCaptures']}/{cache_diagnostics['captureLimit']},resources_{cache_diagnostics['readyResources']}/{cache_diagnostics['resourceLimit']},decoded_LRU_{cache_diagnostics['readyImages']}/{cache_diagnostics['imageLimit']},filter_DOM_stable_PASS,HTTP_short_{slice_cache_control.replace(' ', '')}_{image_cache_control.replace(' ', '')},HTTP_versioned_{versioned_slice_cache_control.replace(' ', '')}_{versioned_image_cache_control.replace(' ', '')}")
        print(f"zoom_mode=slice_{zoom_slice_before}->{zoom_slice_after},zoom_{zoom_before}->{zoom_after}")
        print(f"hidden_marker_hover={hidden_name}->{hidden_tooltip}")
        print("tools=menu,scroll,pan,zoom,fit,labels,targets,brightness,contrast,cine,fullscreen")
        print("page_errors=0")
        print("RESULT=PASS")
        return 0
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


if __name__ == "__main__":
    raise SystemExit(asyncio.run(verify()))
