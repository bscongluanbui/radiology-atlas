"use strict";

const SVG_NS = "http://www.w3.org/2000/svg";
const PREFERENCE_KEY = "radiology-atlas.preferences.v1";
function runtimeSetting(name, fallback, min, max) {
  const value = Number(window.viewerRuntime?.[name]);
  return Number.isFinite(value) && value >= min ? Math.min(max, Math.floor(value)) : fallback;
}
const LOW_MEMORY_DEVICE = Number(window.navigator?.deviceMemory) > 0 && Number(window.navigator.deviceMemory) <= 4;
const SLICE_CAPTURE_CACHE_FLOOR = 128;
const SLICE_IMAGE_CACHE_LIMIT = Math.min(LOW_MEMORY_DEVICE ? 16 : 64, runtimeSetting("decodedImages", 32, 8, 64));
const SLICE_DECODE_FORWARD = Math.min(Math.floor((SLICE_IMAGE_CACHE_LIMIT - 1) * 2 / 3), runtimeSetting("decodeForward", 20, 1, 48));
const SLICE_DECODE_BACKWARD = Math.min(SLICE_IMAGE_CACHE_LIMIT - SLICE_DECODE_FORWARD - 1, runtimeSetting("decodeBackward", 11, 0, 24));
const SLICE_DECODE_CONCURRENCY = runtimeSetting("decodeConcurrency", 2, 1, 4);
const SERIES_PRELOAD_CONCURRENCY = runtimeSetting("preloadConcurrency", 2, 1, 4);
const SERIES_PRELOAD_RETRIES = 1;
const sliceRequestQueue = new window.ViewerRequestQueue({ concurrency: 2, background: 1 });
const slicePriorityHints = new Map();
const DRAG_THRESHOLD = 4;
const DRAG_SLICE_PIXELS = 8;
const DRAG_ZOOM_RATE = 0.006;
const anatomyTouchItems = new WeakMap();
let touchGestures = null;

function isMobileViewer() {
  return Boolean(window.matchMedia?.("(pointer: coarse)").matches
    || window.matchMedia?.("(max-width: 720px)").matches);
}

function applyMobileDefaults() {
  if (!isMobileViewer()) return;
  for (const field of ["detailsVisible", "definitionPeek", "mprVisible", "filmstripVisible", "adjustmentsVisible", "menuPinned"]) state[field] = false;
  closeOptionsMenu({ force: true, focus: false });
  closeModuleCatalogue();
  el.optionsMenu.querySelectorAll("details[open]").forEach((node) => { node.open = false; });
}

const sliceCaptureCache = new Map();
const sliceImageCache = new Map();
const sliceResourceCache = new Map();
const sliceCacheStats = {
  captureHits: 0, captureMisses: 0,
  imageHits: 0, imageMisses: 0,
  resourceHits: 0, resourceMisses: 0,
};
let sliceLoadWorker = null;
let pendingSliceResetView = false;
let decodePrefetchQueue = [];
let decodePrefetchActive = 0;
let seriesPreloadGeneration = 0;
let seriesPreloadSession = null;
let lastSliceDirection = 1;

const state = {
  anatomyLanguage: "en",
  languagePack: null,
  languageRequest: 0,
  searchRequest: 0,
  catalogue: null,
  catalogueRequest: 0,
  catalogueLoading: false,
  module: null,
  series: null,
  variant: null,
  slicePosition: 0,
  capture: null,
  capturedOnly: true,
  interactionMode: "scroll",
  labelsVisible: true,
  overlaysVisible: true,
  overlayOpacity: 70,
  leadersVisible: true,
  targetsVisible: true,
  orientationVisible: true,
  filmstripVisible: true,
  mprVisible: false,
  mprWidth: 320,
  mprFitWidth: false,
  mprResizing: false,
  detailsVisible: true,
  definitionPeek: false, // Temporary label detail; not saved as a sidebar preference.
  adjustmentsVisible: true,
  menuPinned: false,
  activeFilters: new Set(),
  previewFilterIds: new Set(),
  highlightFilterIds: new Set(),
  pinnedHighlightFilterId: null,
  expandedFilterGroups: new Set(),
  selectedStructure: null,
  selectionHighlightOnly: false,
  structureMode: "slice",
  searchResults: [],
  zoom: 1,
  fitZoom: 1,
  panX: 0,
  panY: 0,
  dragging: false,
  dragStart: null,
  dragFrame: 0,
  suppressDragClick: false,
  wheelDelta: 0,
  wheelTargetPosition: null,
  wheelFrame: 0,
  requestToken: 0,
  dataRevision: Date.now(),
  seriesRevision: 0,
  cineTimer: null,
  brightness: 100,
  contrast: 100,
};

const el = {};

function cacheElements() {
  [
    "anatomyLanguageSelect", "anatomyLanguageStatus",
    "app", "moduleCatalogueButton", "moduleCataloguePopover", "detailDrawerButton", "studyEyebrow", "studyTitle", "globalSearch",
    "menuOverlaysToggle", "overlayOpacitySlider", "overlayOpacityNumber", "overlayCoverageStatus",
    "dataBadge", "helpButton", "mprPanel", "mprToggleButton", "mprContent", "mprCloseButton", "mprFitWidthButton", "mprResizeHandle", "mprViews", "detailPanel",
    "moduleCount", "moduleSearch", "capturedFilter", "allModulesFilter", "refreshLibraryButton", "moduleTree",
    "optionsMenuButton", "optionsMenu", "optionsMenuPinButton", "optionsMenuCloseButton",
    "anatomyNameStatus", "filterMetadataStatus", "showAllAnatomyButton", "sourceFilterDefaultsButton",
    "toolbarWeightingSelect", "menuLabelsToggle", "menuLeadersToggle", "menuTargetsToggle",
    "menuOrientationToggle", "menuFilmstripToggle", "menuDetailsToggle",
    "menuAdjustmentsToggle", "menuSelectAllFilters", "menuFilterList", "scrollModeButton", "panModeButton",
    "zoomModeButton", "zoomOutButton", "fitButton", "zoomInButton", "zoomValue", "labelsButton",
    "targetsButton", "brightnessSlider", "contrastSlider", "resetImageButton", "sliceMeta", "fullscreenButton",
    "anatomyViewport", "sceneAnchor", "scene", "anatomyImage", "annotationLayer", "anatomyTooltip",
    "emptyState", "loadingState", "errorState", "errorMessage", "captureStatus", "previousSliceButton",
    "cineButton", "nextSliceButton", "sliceNumber", "sliceTotal", "sliceSlider", "sliceIdLabel", "filmstrip", "preloadStatus",
    "structureSearch", "sliceStructuresTab", "searchStructuresTab", "structureListTitle", "structureCount",
    "structureList", "structureEmpty", "definitionPanel", "filterCount", "filterList", "shortcutDialog",
  ].forEach((id) => { el[id] = document.getElementById(id); });
  el.menuOverlaysToggle.addEventListener("change", () => {
    state.overlaysVisible = el.menuOverlaysToggle.checked; renderOverlay(); savePreferences();
  });
  const setOverlayOpacity = (event) => {
    state.overlayOpacity = Math.min(100, Math.max(0, Number(event.target.value)));
    el.overlayOpacityNumber.value = String(state.overlayOpacity);
    el.overlayOpacitySlider.value = String(state.overlayOpacity);
    renderOverlay(); savePreferences();
  };
  el.overlayOpacitySlider.addEventListener("input", setOverlayOpacity);
  el.overlayOpacitySlider.addEventListener("change", setOverlayOpacity);
  el.overlayOpacityNumber.addEventListener("input", setOverlayOpacity);
  el.overlayOpacitySlider.addEventListener("keydown", (event) => {
    const steps = {ArrowLeft:-1, ArrowDown:-1, ArrowRight:1, ArrowUp:1};
    if (!(event.key in steps) && event.key !== "Home" && event.key !== "End") return;
    event.preventDefault();
    event.target.value = String(event.key === "Home" ? 0 : event.key === "End" ? 100
      : state.overlayOpacity + steps[event.key]);
    setOverlayOpacity(event);
  });
}

async function api(path, query = {}, { cache = "no-store", signal, priority = "auto" } = {}) {
  const url = new URL(path, location.origin);
  Object.entries(query).forEach(([key, value]) => url.searchParams.set(key, String(value)));
  const response = await fetch(url, { cache, signal, priority });
  if (response.status === 401 && window.viewerRuntime?.remote) {
    window.viewerResourceCache?.clear();
    location.assign("/login");
    throw new Error("Phiên đăng nhập đã hết hạn");
  }
  const body = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(body.error || `${response.status} ${response.statusText}`);
  return body;
}

function touchCache(cache, key) {
  const entry = cache.get(key);
  if (!entry) return null;
  cache.delete(key); cache.set(key, entry);
  return entry;
}

function trimCache(cache, limit) {
  while (cache.size > limit) cache.delete(cache.keys().next().value);
}

function cachedPromise(cache, key, limit, factory, statPrefix) {
  const existing = touchCache(cache, key);
  if (existing) {
    sliceCacheStats[`${statPrefix}Hits`] += 1;
    return existing.promise;
  }
  sliceCacheStats[`${statPrefix}Misses`] += 1;
  const entry = { status: "pending", promise: null };
  entry.promise = Promise.resolve().then(factory).then((value) => {
    entry.status = "ready";
    return value;
  }).catch((error) => {
    if (cache.get(key) === entry) cache.delete(key);
    throw error;
  });
  cache.set(key, entry); trimCache(cache, limit);
  return entry.promise;
}

function currentSeriesCacheLimit() {
  return Math.max(SLICE_CAPTURE_CACHE_FLOOR, (state.variant?.slices?.length || 0) + 8);
}

function activeSeriesKey() {
  if (!state.module || !state.series || !state.variant) return "";
  return [
    state.dataRevision, state.seriesRevision, state.module.key,
    state.series.directory, state.variant.directory,
  ].join("|");
}

function clearSliceCaches({ advanceDataRevision = true } = {}) {
  sliceRequestQueue.clear(); slicePriorityHints.clear();
  sliceCaptureCache.clear(); sliceImageCache.clear(); sliceResourceCache.clear();
  window.viewerResourceCache?.clear();
  decodePrefetchQueue = [];
  seriesPreloadGeneration += 1;
  seriesPreloadSession = null;
  state.seriesRevision += 1;
  if (advanceDataRevision) state.dataRevision = Date.now();
  updatePreloadStatus();
}

function versionedDataUrl(url) {
  if (!url) return "";
  return `${url}${url.includes("?") ? "&" : "?"}v=${state.dataRevision}`;
}

function sliceDescriptor(position = state.slicePosition) {
  const number = state.variant?.slices?.[position];
  if (!state.module || !state.series || !state.variant || number == null) return null;
  const descriptor = {
    moduleKey: state.module.key, seriesDirectory: state.series.directory,
    variantDirectory: state.variant.directory, position, number, revision: state.dataRevision,
    seriesRevision: state.seriesRevision, seriesKey: activeSeriesKey(), preloadAttempts: 0,
  };
  descriptor.key = [descriptor.seriesKey, descriptor.number].join("|");
  return descriptor;
}

function descriptorBelongsToActiveSeries(descriptor) {
  return Boolean(descriptor && descriptor.seriesKey === activeSeriesKey());
}

function descriptorIsCurrent(descriptor) {
  const current = sliceDescriptor();
  return Boolean(current && descriptor && current.key === descriptor.key);
}

function fetchSliceCapture(descriptor, { prefetch = false, priority = prefetch ? 2 : 0 } = {}) {
  slicePriorityHints.set(descriptor.key, Math.min(priority, slicePriorityHints.get(descriptor.key) ?? priority));
  sliceRequestQueue.promote(descriptor.key, priority);
  return cachedPromise(sliceCaptureCache, descriptor.key, currentSeriesCacheLimit(), () =>
    sliceRequestQueue.schedule(descriptor.key, (signal) => api("/api/slice", {
      key: descriptor.moduleKey, series: descriptor.seriesDirectory,
      variant: descriptor.variantDirectory, slice: descriptor.number, rev: descriptor.revision, prefetch: prefetch ? 1 : 0,
    }, { cache: "default", signal, priority: priority === 0 ? "high" : "low" }), slicePriorityHints.get(descriptor.key) ?? priority), "capture");
}

function markSliceResourceReady(url) {
  if (sliceResourceCache.has(url)) return;
  sliceResourceCache.set(url, { status: "ready", promise: Promise.resolve(true) });
  trimCache(sliceResourceCache, currentSeriesCacheLimit());
}

function warmSliceImageBytes(url) {
  // Completion markers may outlive an LRU/TTL eviction of actual bytes.
  if (window.viewerResourceCache && !window.viewerResourceCache.has(url)) sliceResourceCache.delete(url);
  return cachedPromise(sliceResourceCache, url, currentSeriesCacheLimit(), async () => {
    if (window.viewerResourceCache) {
      await window.viewerResourceCache.load(url, { priority: 2 });
      return true;
    }
    const response = await fetch(url, { cache: "force-cache", priority: "low" });
    if (!response.ok) throw new Error(`${response.status} ${response.statusText}`);
    // Consuming the response commits the versioned file to the browser HTTP cache.
    // The temporary buffer is not retained in JavaScript, so a full series does
    // not occupy decoded RGBA memory for every slice.
    await response.arrayBuffer();
    return true;
  }, "resource");
}

function decodeSliceImage(url, { lowPriority = false } = {}) {
  if (!lowPriority) window.viewerResourceCache?.promote(url, 0);
  return cachedPromise(sliceImageCache, url, SLICE_IMAGE_CACHE_LIMIT, async () => {
    const image = new Image();
    image.decoding = "async";
    if (lowPriority) image.fetchPriority = "low";
    image.src = window.viewerResourceCache
      ? await window.viewerResourceCache.source(url, { priority: lowPriority ? 1 : 0 })
      : url;
    if (image.decode) await image.decode();
    else await new Promise((resolve, reject) => {
      image.addEventListener("load", resolve, { once: true });
      image.addEventListener("error", reject, { once: true });
    });
    markSliceResourceReady(url);
    return image;
  }, "image");
}

window.viewerSliceCacheDiagnostics = () => ({
  captureLimit: currentSeriesCacheLimit(), imageLimit: SLICE_IMAGE_CACHE_LIMIT,
  resourceLimit: currentSeriesCacheLimit(),
  decodeForward: SLICE_DECODE_FORWARD, decodeBackward: SLICE_DECODE_BACKWARD,
  decodeConcurrency: SLICE_DECODE_CONCURRENCY, preloadConcurrency: SERIES_PRELOAD_CONCURRENCY,
  metadataRequests: sliceRequestQueue.diagnostics(),
  captures: sliceCaptureCache.size, images: sliceImageCache.size,
  resources: sliceResourceCache.size,
  encoded: window.viewerResourceCache?.diagnostics() || null,
  readyCaptures: [...sliceCaptureCache.values()].filter((entry) => entry.status === "ready").length,
  readyImages: [...sliceImageCache.values()].filter((entry) => entry.status === "ready").length,
  readyResources: [...sliceResourceCache.values()].filter((entry) => entry.status === "ready").length,
  prefetchActive: decodePrefetchActive, prefetchQueued: decodePrefetchQueue.length,
  seriesPreloadKey: seriesPreloadSession?.key || "",
  seriesPreloadTotal: seriesPreloadSession?.total || 0,
  seriesPreloadCompleted: seriesPreloadSession?.completed || 0,
  seriesPreloadFailed: seriesPreloadSession?.failed || 0,
  seriesPreloadActive: seriesPreloadSession?.active || 0,
  seriesPreloadQueued: seriesPreloadSession?.queue.length || 0,
  seriesPreloadReady: Boolean(seriesPreloadSession
    && seriesPreloadSession.completed === seriesPreloadSession.total
    && seriesPreloadSession.failed === 0),
  ...sliceCacheStats,
});

function loadPreferences() {
  try {
    const saved = JSON.parse(localStorage.getItem(PREFERENCE_KEY) || "{}");
    state.anatomyLanguage = saved.anatomyLanguage || "en";
    state.capturedOnly = saved.capturedOnly !== false;
    state.labelsVisible = saved.labelsVisible !== false;
    state.overlaysVisible = saved.overlaysVisible !== false;
    state.overlayOpacity = Number.isFinite(Number(saved.overlayOpacity)) ? Math.min(100, Math.max(0, Number(saved.overlayOpacity))) : 70;
    state.leadersVisible = saved.leadersVisible !== false;
    state.targetsVisible = saved.targetsVisible !== false;
    state.orientationVisible = saved.orientationVisible !== false;
    state.filmstripVisible = saved.filmstripVisible !== false;
    state.mprVisible = saved.mprVisible === true;
    state.mprWidth = Number(saved.mprWidth) || 320;
    state.mprFitWidth = saved.mprFitWidth === true;
    state.detailsVisible = saved.detailsVisible !== false;
    state.adjustmentsVisible = saved.adjustmentsVisible !== false;
    state.menuPinned = saved.menuPinned === true;
    state.brightness = Number(saved.brightness) || 100;
    state.contrast = Number(saved.contrast) || 100;
    return saved;
  } catch (_) { return {}; }
}

function savePreferences() {
  try {
    localStorage.setItem(PREFERENCE_KEY, JSON.stringify({
      anatomyLanguage: state.anatomyLanguage,
      capturedOnly: state.capturedOnly,
      labelsVisible: state.labelsVisible,
      overlaysVisible: state.overlaysVisible,
      overlayOpacity: state.overlayOpacity,
      leadersVisible: state.leadersVisible,
      targetsVisible: state.targetsVisible,
      orientationVisible: state.orientationVisible,
      filmstripVisible: state.filmstripVisible,
      mprVisible: state.mprVisible,
      mprWidth: state.mprWidth,
      mprFitWidth: state.mprFitWidth,
      detailsVisible: state.detailsVisible,
      adjustmentsVisible: state.adjustmentsVisible,
      menuPinned: state.menuPinned,
      brightness: state.brightness,
      contrast: state.contrast,
      moduleKey: state.module?.key || null,
      seriesDirectory: state.series?.directory || null,
      variantDirectory: state.variant?.directory || null,
      sliceNumber: currentSliceNumber(),
    }));
  } catch (_) {}
}

function normalizeText(value) {
  return AnatomyLanguage.searchText(value);
}

function localizedValue(collection, key, field, original) {
  return AnatomyLanguage.resolve(state.languagePack, collection, key, field, original);
}

function definitionValue(definition) {
  return localizedValue("structures", definition?.identity_key, "name", definition?.name || "");
}

function labelValue(label) {
  const slice = state.capture?.slice || {};
  const key = AnatomyLanguage.labelKey(state.series?.directory, state.variant?.directory,
    slice.active_id ?? slice.id, label.canvas, label.label_index);
  const translated = localizedValue("labels", key, "text", label.text);
  if (translated.translated) return translated;
  const freeText = localizedValue("texts", key, "text", label.text);
  if (freeText.translated) return freeText;
  // Only whole, exact source names may reuse a scoped structure translation.
  // Captured wrapped fragments/abbreviations need their own occurrence entry.
  return label.binding_verified === true && label.definition?.name === label.text
    ? definitionValue(label.definition) : { text: label.text, translated: false };
}

function anatomySourceName(item) {
  return item.target?.semantic_primary_ambiguous && item.target.coincident_definitions?.length && !item.definition
    ? item.target.coincident_definitions.map((row) => row.name).join(" / ") : item.name;
}

function anatomyValue(item) {
  if (item.definition) return definitionValue(item.definition);
  if (item.target?.semantic_primary_ambiguous && item.target.coincident_definitions?.length) {
    const values = item.target.coincident_definitions.map(definitionValue);
    return { text: values.map((row) => row.text).join(" / "), translated: values.some((row) => row.translated) };
  }
  if (item.label) return labelValue(item.label);
  if (item.target) {
    const slice = state.capture?.slice || {};
    const key = AnatomyLanguage.targetKey(state.series?.directory, state.variant?.directory,
      slice.active_id ?? slice.id, item.target);
    return localizedValue("texts", key, "text", item.name);
  }
  return { text: item.name, translated: false };
}

function anatomyDisplayName(item) {
  return AnatomyLanguage.lines(state.anatomyLanguage, anatomySourceName(item), anatomyValue(item)).map((row) => row.text).join("\n");
}

function setLanguageName(node, original, value) {
  node.replaceChildren();
  const lines = AnatomyLanguage.lines(state.anatomyLanguage, original, value);
  node.classList.toggle("bilingual-name", lines.length === 2);
  lines.forEach((row) => {
    const span = document.createElement("span"); span.lang = row.lang;
    span.className = `anatomy-name-line${row.missing ? " translation-missing" : ""}`;
    span.textContent = row.text; node.append(span);
  });
}

function setAnatomyName(node, item) {
  setLanguageName(node, anatomySourceName(item), anatomyValue(item));
}

function repaintAnatomyLanguage() {
  hideTooltip();
  renderFilters(); renderOverlay();
  if (state.structureMode === "slice") renderSliceStructures(); else searchStructures();
  // A language repaint must not reset selection, peek visibility or hold-only mode.
  if (state.selectedStructure && !state.selectionHighlightOnly) renderDefinition(state.selectedStructure);
}

async function loadAnatomyLanguage({ repaint = true } = {}) {
  const token = ++state.languageRequest;
  const language = state.anatomyLanguage;
  const moduleKey = currentModuleKey();
  state.languagePack = null;
  el.anatomyLanguageSelect.value = language;
  el.anatomyLanguageStatus.textContent = language === "en" ? "Original anatomical content" : "Loading anatomy language…";
  if (repaint) repaintAnatomyLanguage();
  if (!moduleKey) return;
  let pack;
  try { pack = await api("/api/translations", {key: moduleKey, lang: AnatomyLanguage.locale(language)}); }
  catch (_) { pack = {status: "unavailable"}; }
  if (token !== state.languageRequest || language !== state.anatomyLanguage || moduleKey !== currentModuleKey()) return;
  state.languagePack = pack;
  const reviewed = ["structures", "filters", "labels", "texts"].reduce((count, key) => count
    + Object.values(pack[key] || {}).reduce((total, row) => total + Object.keys(row?.source || {}).filter((field) =>
      AnatomyLanguage.fieldStatus(row, field) === "reviewed" && typeof row?.translation?.[field] === "string" && row.translation[field].trim()).length, 0), 0);
  el.anatomyLanguageStatus.textContent = language === "en" ? "Original anatomical content"
    : language === "en-vi" ? "English ở trên · Tiếng Việt ở dưới. Mục chưa dịch được đánh dấu rõ."
    : language === "vi" ? reviewed
      ? "Tiếng Việt · Mục chưa dịch giữ nguyên bản gốc."
      : "Chưa có bản dịch tiếng Việt · Đang hiển thị bản gốc."
    : "Missing translations use the original content.";
  if (repaint) repaintAnatomyLanguage();
}

async function initializeAnatomyLanguages() {
  const config = await api("/api/languages");
  el.anatomyLanguageSelect.replaceChildren();
  config.languages.forEach((language) => {
    const option = document.createElement("option"); option.value = language.code;
    option.textContent = language.label; el.anatomyLanguageSelect.append(option);
  });
  const bilingual = config.languages.some((row) => row.code === "vi");
  if (bilingual) {
    const option = document.createElement("option"); option.value = "en-vi";
    option.textContent = "Song ngữ"; el.anatomyLanguageSelect.append(option);
  }
  if (!(bilingual && state.anatomyLanguage === "en-vi") && !config.languages.some((row) => row.code === state.anatomyLanguage)) state.anatomyLanguage = "en";
  el.anatomyLanguageSelect.value = state.anatomyLanguage;
}

function colourKey(value) {
  const text = String(value || "#80a0aa").trim().toLowerCase().replace(/\s/g, "");
  const rgb = text.match(/^rgb\((\d+),(\d+),(\d+)\)$/);
  if (rgb) return `#${rgb.slice(1).map((part) => Number(part).toString(16).padStart(2, "0")).join("")}`;
  return text.startsWith("#") ? text : `#${text}`;
}

function pad(value, length = 4) { return String(value).padStart(length, "0"); }
function currentSliceNumber() { return state.variant?.slices?.[state.slicePosition] ?? null; }
function currentModuleKey() { return state.module?.key || ""; }

async function loadCatalogue({ restore = false, libraryOnly = false } = {}) {
  if (libraryOnly && state.catalogueLoading) return;
  const token = ++state.catalogueRequest;
  state.catalogueLoading = true;
  try {
    if (!libraryOnly) {
      clearSliceCaches();
      el.dataBadge.textContent = "SCANNING";
      el.dataBadge.classList.remove("status-ok");
    }
    const catalogue = await api("/api/catalogue", { _: Date.now() });
    if (token !== state.catalogueRequest) return;
    state.catalogue = catalogue;
    renderModuleTree();
    el.dataBadge.textContent = `${catalogue.captured_module_count}/${catalogue.module_count} MODULES`;
    el.dataBadge.title = "";
    el.dataBadge.classList.add("status-ok");
    el.moduleCount.textContent = String(state.capturedOnly ? catalogue.captured_module_count : catalogue.module_count);
    // Opening the catalogue updates availability, not the active frame/series.
    if (libraryOnly) return;
    if (restore && !state.module) {
      const saved = loadPreferences();
      // The website may open a specific authorized module; all viewer controls
      // and the local launcher's saved-module behavior otherwise stay unchanged.
      const requestedKey = typeof window !== "undefined" ? window.viewerRuntime?.moduleKey : null;
      const preferred = catalogue.modules.find((item) => item.captured && item.key === (requestedKey || saved.moduleKey));
      if (requestedKey && !preferred) throw new Error("Module đã chọn chưa sẵn sàng. Hãy quay lại Anatomy.");
      const fallback = catalogue.modules.find((item) => item.captured);
      if (preferred || fallback) await selectModule((preferred || fallback).key, { restore: !requestedKey || saved.moduleKey === requestedKey });
    } else if (state.module) {
      const refreshed = catalogue.modules.find((item) => item.key === state.module.key);
      if (refreshed?.captured) await selectModule(refreshed.key, { restore: true, preserveSlice: true });
    }
  } finally {
    if (token === state.catalogueRequest) state.catalogueLoading = false;
  }
}

function filteredModules() {
  const query = normalizeText(el.moduleSearch.value);
  return (state.catalogue?.modules || []).filter((item) => {
    if (state.capturedOnly && !item.captured) return false;
    return !query || normalizeText(`${item.region} ${item.title} ${item.modality}`).includes(query);
  });
}

function renderModuleTree() {
  const rows = filteredModules();
  const regions = new Map();
  rows.forEach((item) => {
    if (!regions.has(item.region)) regions.set(item.region, []);
    regions.get(item.region).push(item);
  });
  el.moduleTree.replaceChildren();
  regions.forEach((items, region) => {
    const group = document.createElement("section");
    group.className = "region-group";
    const heading = document.createElement("button");
    heading.type = "button";
    heading.className = "region-heading";
    heading.setAttribute("aria-expanded", "true");
    const chevron = document.createElement("span"); chevron.className = "chevron"; chevron.textContent = "▶";
    const name = document.createElement("strong"); name.textContent = region;
    const count = document.createElement("small"); count.textContent = String(items.length);
    heading.append(chevron, name, count);
    const container = document.createElement("div"); container.className = "region-items";
    items.forEach((item) => container.append(moduleButton(item)));
    heading.addEventListener("click", () => {
      const expanded = heading.getAttribute("aria-expanded") === "true";
      heading.setAttribute("aria-expanded", String(!expanded));
      container.hidden = expanded;
    });
    group.append(heading, container);
    el.moduleTree.append(group);
  });
  el.moduleCount.textContent = String(rows.length);
  if (!rows.length) {
    const empty = document.createElement("p"); empty.className = "pane-hint";
    empty.textContent = "No modules match this filter."; el.moduleTree.append(empty);
  }
}

function moduleButton(item) {
  const button = document.createElement("button");
  button.type = "button";
  button.className = `module-button${state.module?.key === item.key ? " active" : ""}${item.captured ? "" : " disabled"}`;
  button.setAttribute("role", "treeitem");
  button.setAttribute("aria-selected", String(state.module?.key === item.key));
  button.disabled = !item.captured;
  const icon = createModuleIcon(item);
  const copy = document.createElement("span"); copy.className = "module-copy";
  const title = document.createElement("strong"); title.textContent = item.title;
  const meta = document.createElement("small"); meta.textContent = item.captured ? `${item.modality} · ${item.series_count} series` : `${item.modality} · Not available`;
  copy.append(title, meta);
  const status = document.createElement("span"); status.className = `module-state${item.captured ? " ready" : ""}`;
  button.append(icon, copy, status);
  if (item.captured) button.addEventListener("click", async () => {
    await selectModule(item.key);
    closeModuleCatalogue();
  });
  return button;
}

function createModuleIcon(item) {
  const icon = document.createElement("span");
  icon.className = "module-icon";
  // The adjacent title/modality already labels this decorative thumbnail.
  icon.setAttribute("aria-hidden", "true");
  const fallback = document.createElement("span");
  fallback.className = "module-icon-fallback";
  fallback.textContent = modalityAbbreviation(item.modality);
  icon.append(fallback);
  if (/^\/assets\/module-icons\/[a-z0-9-]+\.png\?v=[0-9a-f]{16}$/.test(item.icon_url || "")) {
    const image = document.createElement("img");
    image.className = "module-thumbnail";
    image.alt = "";
    image.width = 48; image.height = 48;
    image.loading = "lazy";
    image.decoding = "async";
    image.addEventListener("load", () => { fallback.hidden = true; }, { once: true });
    image.addEventListener("error", () => { image.remove(); fallback.hidden = false; }, { once: true });
    image.src = item.icon_url;
    icon.append(image);
  }
  return icon;
}

function modalityAbbreviation(value) {
  const text = String(value || "AN").toUpperCase();
  if (text.includes("ILLUSTR")) return "ILL";
  if (text.includes("ANGIO")) return "ANG";
  return text.slice(0, 3);
}

async function selectModule(key, options = {}) {
  cancelDrag();
  stopCine();
  const token = ++state.requestToken;
  el.loadingState.hidden = false;
  el.emptyState.hidden = true;
  el.errorState.hidden = true;
  try {
    const module = await api("/api/module", { key, _: Date.now() });
    if (token !== state.requestToken) return;
    state.module = module;
    state.capture = null;
    state.selectedStructure = null;
    // A full occurrence pack can be several MiB. Load it alongside the first
    // slice, using source English until it arrives, rather than blocking images.
    const languageTask = loadAnatomyLanguage({repaint: false});
    // Show the captured labels on first open. Source defaults remain an explicit option.
    state.activeFilters = new Set((module.filters || []).map((item) => String(item.id)));
    state.previewFilterIds = new Set();
    state.highlightFilterIds = new Set();
    state.pinnedHighlightFilterId = null;
    state.expandedFilterGroups = new Set();
    el.studyEyebrow.textContent = `${module.region} · ${module.modality}`;
    el.studyTitle.textContent = module.title;
    renderModuleTree();
    renderSeriesSelectors();
    renderFilters();
    clearDefinition();
    const saved = loadPreferences();
    let chosenSeries = null;
    let chosenVariant = null;
    for (const series of module.series) {
      for (const variant of series.variants) {
        if (!variant.slice_count) continue;
        if (options.restore && series.directory === saved.seriesDirectory && variant.directory === saved.variantDirectory) {
          chosenSeries = series; chosenVariant = variant;
        } else if (!chosenSeries) { chosenSeries = series; chosenVariant = variant; }
      }
    }
    if (!chosenSeries) throw new Error("This module has no complete image/label slice pair yet.");
    const wantedSlice = options.preserveSlice ? currentSliceNumber() : (options.restore ? saved.sliceNumber : null);
    await selectVariant(chosenSeries.directory, chosenVariant.directory, { wantedSlice, resetView: true });
    languageTask.then(() => {
      if (token === state.requestToken && state.capture) repaintAnatomyLanguage();
    });
    closeModuleCatalogue();
  } catch (error) {
    if (token !== state.requestToken) return;
    showError(error.message);
  }
}

function seriesOptionValue(series, variant) {
  return JSON.stringify([series.directory, variant.directory]);
}

function renderSeriesSelectors() {
  const selectors = [el.toolbarWeightingSelect];
  selectors.forEach((select) => select.replaceChildren());
  let available = 0;
  (state.module?.series || []).forEach((series) => {
    const variants = series.variants.filter((variant) => variant.slice_count);
    if (!variants.length) return;
    variants.forEach((variant) => {
      available += 1;
      selectors.forEach((select) => {
        const option = document.createElement("option");
        option.value = seriesOptionValue(series, variant);
        option.textContent = variants.length === 1 && /^default$/i.test(variant.label)
          ? `${series.label} - ${variant.slice_count} slices`
          : `${series.label} - ${variant.label} (${variant.slice_count} slices)`;
        select.append(option);
      });
    });
  });
  selectors.forEach((select) => {
    select.disabled = available === 0;
    if (state.series && state.variant) select.value = seriesOptionValue(state.series, state.variant);
  });
}

async function selectVariant(seriesDirectory, variantDirectory, { wantedSlice = null, resetView = false } = {}) {
  const series = state.module.series.find((item) => item.directory === seriesDirectory);
  const variant = series?.variants.find((item) => item.directory === variantDirectory);
  if (!series || !variant?.slice_count) return;
  cancelDrag();
  const switchingSeries = state.series?.directory !== series.directory
    || state.variant?.directory !== variant.directory;
  state.series = series;
  state.variant = variant;
  if (switchingSeries) clearSliceCaches({ advanceDataRevision: false });
  const wantedPosition = wantedSlice == null ? -1 : variant.slices.indexOf(Number(wantedSlice));
  state.slicePosition = wantedPosition >= 0 ? wantedPosition : 0;
  renderSeriesSelectors();
  await showCurrentSlice({ resetView });
}

async function applySliceDescriptor(descriptor, { resetView = false } = {}) {
  const capture = await fetchSliceCapture(descriptor);
  if (!descriptorIsCurrent(descriptor)) return false;
  const decodedImage = await decodeSliceImage(versionedDataUrl(capture.image_url));
  if (!descriptorIsCurrent(descriptor)) return false;

  // Keep the previous frame painted until the replacement has decoded. Assigning
  // a warm image URL now becomes an atomic frame swap instead of a blank/spinner.
  el.anatomyImage.src = decodedImage.src;
  state.capture = capture;
  const width = Number(capture.image?.width) || decodedImage.naturalWidth || 1890;
  const height = Number(capture.image?.height) || decodedImage.naturalHeight || 1091;
  el.scene.style.width = `${width}px`;
  el.scene.style.height = `${height}px`;
  el.annotationLayer.setAttribute("viewBox", `0 0 ${width} ${height}`);
  // Filter controls depend on the module/selection, not on the current slice.
  // Rebuilding the complete hierarchy on every wheel step caused avoidable jank.
  renderOverlay();
  renderSliceStructures();
  updateTimeline();
  renderFilmstrip();
  renderCaptureStatus();
  if (state.mprVisible) renderMprPanel();
  // Keep zoom/pan stable between frames. Per-slice refitting made an otherwise
  // fast series appear to jump because label bounds vary from slice to slice.
  if (resetView) requestAnimationFrame(fitView);
  else applyTransform();
  savePreferences();
  preloadNeighbours(lastSliceDirection);
  return true;
}

async function showCurrentSlice({ resetView = false } = {}) {
  if (!sliceDescriptor()) return;
  pendingSliceResetView ||= resetView;
  el.emptyState.hidden = true;
  el.errorState.hidden = true;
  hideTooltip();
  el.app.classList.add("slice-fetching");
  el.app.setAttribute("aria-busy", "true");
  // Full-screen loading is reserved for the initial frame or a series change.
  // Normal scrolling leaves the last diagnostic frame visible.
  el.loadingState.hidden = !(pendingSliceResetView || !state.capture);
  if (sliceLoadWorker) return sliceLoadWorker;

  sliceLoadWorker = (async () => {
    while (true) {
      const descriptor = sliceDescriptor();
      const shouldReset = pendingSliceResetView;
      pendingSliceResetView = false;
      if (!descriptor) return;
      try {
        await applySliceDescriptor(descriptor, { resetView: shouldReset });
      } catch (error) {
        if (descriptorIsCurrent(descriptor)) throw error;
      }
      if (descriptorIsCurrent(descriptor)) return;
    }
  })().catch((error) => showError(error.message)).finally(() => {
    sliceLoadWorker = null;
    el.loadingState.hidden = true;
    el.app.classList.remove("slice-fetching");
    el.app.setAttribute("aria-busy", "false");
  });
  return sliceLoadWorker;
}

function showError(message) {
  el.loadingState.hidden = true;
  el.emptyState.hidden = true;
  el.errorState.hidden = false;
  el.errorMessage.textContent = message;
  el.app.setAttribute("aria-busy", "false");
}

function pointDistance(left, right) {
  return Math.hypot(Number(left?.x) - Number(right?.x), Number(left?.y) - Number(right?.y));
}

function pathLength(geometry) {
  const points = geometry?.points || [];
  let length = 0;
  for (let index = 1; index < points.length; index += 1) length += pointDistance(points[index - 1], points[index]);
  return length;
}

function geometryPath(geometry) {
  return (geometry.points || []).map((point) => {
    if (point.op === "C") return `C ${point.cp1x} ${point.cp1y} ${point.cp2x} ${point.cp2y} ${point.x} ${point.y}`;
    if (point.op === "Q") return `Q ${point.cpx} ${point.cpy} ${point.x} ${point.y}`;
    if (point.op === "Z") return "Z";
    return `${point.op === "M" ? "M" : "L"} ${point.x} ${point.y}`;
  }).join(" ");
}

function targetForLabel(label) {
  const candidates = (state.capture?.geometry || []).filter((geometry) =>
    geometry.canvas === label.canvas && colourKey(geometry.stroke_color) === colourKey(label.color) && pathLength(geometry) > 80
  ).map((geometry) => {
    const points = geometry.points || [];
    const start = points[0]; const end = points[points.length - 1];
    const startDistance = pointDistance(label, start); const endDistance = pointDistance(label, end);
    return { anchorDistance: Math.min(startDistance, endDistance), target: startDistance <= endDistance ? end : start };
  }).filter((item) => item.target && item.anchorDistance < 110).sort((a, b) => a.anchorDistance - b.anchorDistance);
  return candidates[0]?.target || null;
}

function structureFromLabel(label) {
  const definition = label.definition || null;
  return {
    key: definition?.taxon_id ? `taxon:${definition.identity_key || `${definition.ta_id ?? "legacy"}:${definition.taxon_id}`}` : `label:${label.label_index}:${normalizeText(label.text)}`,
    taxonId: definition?.taxon_id || null,
    name: definition?.name || label.text || "Anatomical label",
    latin: definition?.latin || "",
    definition,
    color: colourKey(label.color),
    kind: "Label",
    filterId: label.filter_id == null ? null : String(label.filter_id),
    filterIds: label.filter_id == null ? [] : [String(label.filter_id)],
    label,
  };
}

function targetVerified(target) {
  if (target.hover_only !== true) return false;
  const semantic = target.hover_confidence === "VERIFIED_DECRYPTED_POINT_ID" && target.semantic_verified === true && (target.point_id != null || (target.semantic_primary_ambiguous === true && (target.semantic_identities || []).length > 1)) && Number(target.semantic_fit_error_px) <= 2.25;
  const native = target.hover_confidence === "VERIFIED_NATIVE_TOOLTIP_2X" && target.marker_name_verified === true && Number(target.hover_confirmation_count) >= 2;
  return semantic || native;
}

function targetFilterEnabled(target) {
  if (target.semantic_primary_ambiguous) return (target.semantic_identities || []).some((x) => filterEnabled(x.filter_id));
  return filterEnabled(target.filter_id);
}

function structureFromTarget(target) {
  const label = target.label && typeof target.label === "object" ? target.label : {};
  const definition = target.definition || null;
  const taxon = definition?.taxon_id || target.taxon_id || null;
  const filterIds = target.semantic_primary_ambiguous
    ? (target.semantic_identities || []).map((item) => item.filter_id).filter((id) => id != null).map(String)
    : target.filter_id == null ? [] : [String(target.filter_id)];
  return {
    key: taxon ? `taxon:${definition?.identity_key || `${target.ta_id ?? "legacy"}:${taxon}`}` : `target:${target.point_id || `${target.x}:${target.y}`}`,
    taxonId: taxon,
    name: target.semantic_primary_ambiguous ? target.tooltip_text : (definition?.name || label.current || target.tooltip_text || "Anatomical target"),
    latin: definition?.latin || label.latin || "",
    definition,
    color: colourKey(target.fill_color || target.color || "#ffffff"),
    kind: "Target",
    filterId: target.filter_id == null ? null : String(target.filter_id),
    filterIds,
    target,
  };
}

function sliceStructures() {
  const map = new Map();
  (state.capture?.labels || []).filter(labelFilterEnabled).forEach((label) => {
    const item = structureFromLabel(label);
    if (!map.has(item.key)) map.set(item.key, item);
  });
  (state.capture?.hover_targets || []).filter(targetVerified).filter(targetFilterEnabled).forEach((target) => {
    const item = structureFromTarget(target);
    if (!map.has(item.key)) map.set(item.key, item);
  });
  return [...map.values()].sort((a, b) => anatomyDisplayName(a).localeCompare(anatomyDisplayName(b), AnatomyLanguage.locale(state.anatomyLanguage)));
}

function filterEnabled(filterId) {
  return filterId == null || state.activeFilters.has(String(filterId)) || state.previewFilterIds.has(String(filterId));
}

function labelFilterEnabled(label) { return filterEnabled(label?.filter_id); }

function filterInteractionClass(filterIds) {
  if (!state.highlightFilterIds.size) return "";
  const highlighted = (filterIds || []).some((id) => state.highlightFilterIds.has(String(id)));
  return highlighted ? " is-filter-highlighted" : " is-filter-muted";
}

function overlaySelectionKey(labels, targets) {
  const selection = state.selectedStructure;
  if (!selection) return null;
  // Only focus anatomy represented on this frame. Unscoped labels/markers are
  // occurrence-local: matching text or an index on a new slice is not identity.
  const matches = (item) => item.key === selection.key && (item.key.startsWith("taxon:")
    || (item.label && item.label === selection.label) || (item.target && item.target === selection.target));
  const labelPresent = (state.labelsVisible || state.targetsVisible)
    && labels.some((label) => matches(structureFromLabel(label)));
  const targetPresent = state.targetsVisible && targets.some((target) => matches(structureFromTarget(target)));
  return labelPresent || targetPresent ? selection.key : null;
}

function annotationInteractionClass(structureKeys, filterIds, selectionKey) {
  // A clicked structure takes visual priority over group hover/pinning. Group
  // visibility is still applied before rendering, and resumes when selection clears.
  if (selectionKey) return structureKeys.includes(selectionKey) ? " is-selected" : " is-selection-muted";
  return filterInteractionClass(filterIds);
}

function setFilterData(node, filterId) {
  if (filterId != null) node.dataset.filterId = String(filterId);
}

function labelForGeometry(geometry) {
  if (pathLength(geometry) <= 80) return null;
  const points = geometry?.points || [];
  const start = points[0]; const end = points[points.length - 1];
  if (!start || !end) return null;
  const candidates = (state.capture?.labels || []).filter((label) =>
    geometry.canvas === label.canvas && colourKey(geometry.stroke_color) === colourKey(label.color)
  ).map((label) => ({ label, distance: Math.min(pointDistance(label, start), pointDistance(label, end)) }))
    .filter((item) => item.distance < 110).sort((left, right) => left.distance - right.distance);
  return candidates[0]?.label || null;
}

const geometryLabelCache = new WeakMap();

function geometryLabelGroups(capture) {
  if (geometryLabelCache.has(capture)) return geometryLabelCache.get(capture);
  const geometries = capture.geometry || [];
  const labels = capture.labels || [];
  const groups = new Map(geometries.map((geometry) => [geometry, new Set()]));
  const styleKey = (geometry) => `${geometry.canvas}|${colourKey(geometry.stroke_color)}`;
  const pathKey = (geometry) => `${styleKey(geometry)}|${geometryPath(geometry)}`;
  const byPath = new Map();
  geometries.forEach((geometry) => {
    const key = pathKey(geometry);
    if (!byPath.has(key)) byPath.set(key, []);
    byPath.get(key).push(geometry);
  });
  // Reuse the captured label-to-stroke relation; do not infer an anatomical ID.
  (capture.annotations || []).forEach((annotation) => {
    if (!annotation.leader) return;
    const label = labels.find((item) => item.label_index === annotation.label_index && item.canvas === annotation.canvas);
    if (!label) return;
    (byPath.get(pathKey(annotation.leader)) || []).forEach((geometry) => groups.get(geometry).add(label));
  });
  geometries.forEach((geometry) => {
    if (groups.get(geometry).size) return;
    const label = labelForGeometry(geometry);
    if (label) groups.get(geometry).add(label);
  });
  const straight = (geometry) => geometry.points?.length === 2
    && geometry.points[0].op === "M" && geometry.points[1].op === "L";
  const onSegment = (point, geometry) => {
    const [a, b] = geometry.points;
    const dx = b.x - a.x; const dy = b.y - a.y;
    const lengthSquared = dx * dx + dy * dy;
    if (!lengthSquared) return pointDistance(point, a) <= .5;
    const t = Math.max(0, Math.min(1, ((point.x - a.x) * dx + (point.y - a.y) * dy) / lengthSquared));
    return Math.hypot(point.x - a.x - t * dx, point.y - a.y - t * dy) <= .5;
  };
  // A label bracket joins its short stub, which joins the label-side end of the
  // diagonal leader. Exact junctions (not nearby colours) transfer visibility.
  // Two passes cover bracket -> stub -> leader even without captured annotations.
  for (let pass = 0; pass < 2; pass += 1) {
    const pending = [];
    geometries.forEach((geometry) => {
      if (groups.get(geometry).size || !straight(geometry) || pathLength(geometry) > 80) return;
      const linked = new Set();
      geometries.forEach((other) => {
        const owners = groups.get(other);
        if (other === geometry || !owners.size || !straight(other) || styleKey(other) !== styleKey(geometry)) return;
        if (pathLength(other) <= 80) {
          if (other.points.some((point) => onSegment(point, geometry)) || geometry.points.some((point) => onSegment(point, other))) {
            owners.forEach((label) => linked.add(label));
          }
        } else {
          owners.forEach((label) => {
            const [a, b] = other.points;
            const anchor = pointDistance(label, a) <= pointDistance(label, b) ? a : b;
            if (onSegment(anchor, geometry)) linked.add(label);
          });
        }
      });
      if (linked.size) pending.push([geometry, linked]);
    });
    pending.forEach(([geometry, linked]) => groups.set(geometry, linked));
  }
  geometryLabelCache.set(capture, groups);
  return groups;
}

function assignCachedImage(image, url, { svg = false, priority = 2 } = {}) {
  const apply = (value) => { if (svg) image.setAttribute("href", value); else image.src = value; };
  if (!window.viewerResourceCache) { apply(url); return; }
  image.dataset.resourceUrl = url;
  window.viewerResourceCache.source(url, { priority }).then((value) => {
    if (image.dataset.resourceUrl === url && image.isConnected !== false) apply(value);
  }).catch((error) => {
    if (error.name !== "AbortError" && image.isConnected !== false && image.dataset.resourceUrl === url) {
      if (typeof Event !== "undefined") image.dispatchEvent?.(new Event("error"));
    }
  });
}

function renderPixelOverlays() {
  const coverage = state.capture?.pixel_overlays;
  const rows = coverage?.valid_layers || [];
  if (el.menuOverlaysToggle) el.menuOverlaysToggle.checked = state.overlaysVisible;
  if (el.overlayCoverageStatus) el.overlayCoverageStatus.textContent = !coverage ? "Overlay data unavailable"
    : coverage.status === "NOT_APPLICABLE" ? "No overlays on this slice"
    : `${rows.length}/${coverage.expected_count} verified layers${coverage.status === "PARTIAL" ? " · Repair needed" : ""}`;
  if (!state.overlaysVisible) return;
  rows.forEach((layer) => {
    // Group/layer membership only; never claim a layer is a single structure.
    if (layer.status !== "PASS" || !layer.image_url || !layer.filter_ids?.some(filterEnabled)) return;
    const transform = layer.transform;
    if (!Array.isArray(transform) || transform.length !== 6 || !transform.every(Number.isFinite)) return;
    const image = document.createElementNS(SVG_NS, "image");
    assignCachedImage(image, versionedDataUrl(layer.image_url), { svg: true, priority: 1 });
    image.setAttribute("width", String(layer.width)); image.setAttribute("height", String(layer.height));
    image.setAttribute("transform", `matrix(${transform.join(" ")})`);
    const muted = state.highlightFilterIds.size && !layer.filter_ids.some((id) => state.highlightFilterIds.has(String(id)));
    image.setAttribute("opacity", String((state.overlayOpacity / 100) * (muted ? 0.15 : 1)));
    image.setAttribute("class", "pixel-overlay"); image.dataset.layer = layer.layer;
    image.addEventListener("error", () => {
      image.remove();
      el.overlayCoverageStatus.textContent = "Overlay asset failed to load · Run repair";
    }, {once:true});
    el.annotationLayer.append(image);
  });
}

function renderOverlay() {
  el.annotationLayer.replaceChildren();
  updateAnatomyNameStatus();
  if (!state.capture) return;
  renderPixelOverlays();
  el.annotationLayer.classList.toggle("labels-hidden", !state.labelsVisible);
  el.annotationLayer.classList.toggle("leaders-hidden", !state.leadersVisible);
  el.annotationLayer.classList.toggle("targets-hidden", !state.targetsVisible);
  const labels = (state.capture.labels || []).filter(labelFilterEnabled);
  const targets = (state.capture.hover_targets || []).filter(targetVerified).filter(targetFilterEnabled);
  const selectionKey = overlaySelectionKey(labels, targets);
  const geometryLabels = geometryLabelGroups(state.capture);
  (state.capture.geometry || []).forEach((geometry) => {
    const associatedLabels = [...geometryLabels.get(geometry)];
    const visibleOwners = associatedLabels.filter(labelFilterEnabled);
    if (associatedLabels.length && !visibleOwners.length) return;
    const filterIds = [...new Set(visibleOwners.map((label) => label.filter_id).filter((id) => id != null).map(String))];
    // Keep the existing label-to-stroke ownership, including shared brackets and
    // stubs. Never highlight a different structure just because its colour matches.
    const structureKeys = visibleOwners.map((label) => structureFromLabel(label).key);
    const sourceColor = colourKey(geometry.stroke_color);
    const strokeColor = selectionKey && structureKeys.includes(selectionKey) && ["#000", "#000000"].includes(sourceColor)
      ? "#24d5c6" : sourceColor;
    const path = document.createElementNS(SVG_NS, "path");
    path.setAttribute("d", geometryPath(geometry));
    path.setAttribute("stroke", strokeColor);
    path.style.color = strokeColor;
    path.setAttribute("stroke-width", String(geometry.line_width || 1.5));
    path.setAttribute("class", `annotation-line${annotationInteractionClass(structureKeys, filterIds, selectionKey)}`);
    if (filterIds.length === 1) setFilterData(path, filterIds[0]);
    el.annotationLayer.append(path);
  });
  labels.forEach((label) => renderVisibleLabel(label, selectionKey));
  targets.forEach((target) => renderHoverTarget(target, selectionKey));
  // Remove the empty overlay from painting/hit testing when every part is off.
  el.annotationLayer.toggleAttribute("hidden", el.annotationLayer.childElementCount === 0);
}

function renderVisibleLabel(label, selectionKey = null) {
  if (!labelFilterEnabled(label)) return;
  const item = structureFromLabel(label);
  const selected = item.key === selectionKey;
  const text = document.createElementNS(SVG_NS, "text");
  text.setAttribute("x", label.x); text.setAttribute("y", label.y);
  // Illustration labels may be black outside a white source image. Only adjust
  // text presentation on the dark viewer; keep source colours for all bindings.
  const hex = item.color.replace(/^#([0-9a-f])([0-9a-f])([0-9a-f])$/i, "#$1$1$2$2$3$3");
  const rgb = hex.match(/^#([0-9a-f]{2})([0-9a-f]{2})([0-9a-f]{2})$/i);
  const darkText = rgb && rgb.slice(1).every((channel) => parseInt(channel, 16) < 64);
  const textColor = darkText ? "#e2e8f0" : item.color;
  text.setAttribute("fill", textColor);
  text.style.color = textColor;
  text.setAttribute("text-anchor", label.text_align === "right" ? "end" : label.text_align === "center" ? "middle" : "start");
  const interactionClass = annotationInteractionClass([item.key], item.filterIds, selectionKey);
  text.setAttribute("class", `annotation-label${interactionClass}`);
  text.setAttribute("role", "button"); text.setAttribute("tabindex", "0"); text.setAttribute("aria-label", anatomyDisplayName(item));
  text.setAttribute("aria-pressed", String(selected));
  const lines = AnatomyLanguage.lines(state.anatomyLanguage, label.text, labelValue(label));
  text.classList.toggle("bilingual-label", lines.length === 2);
  if (lines.length === 2) {
    const fontSize = AnatomyLanguage.labelFontSize(label, (state.capture?.labels || []).filter(labelFilterEnabled));
    text.style.fontSize = `${fontSize}px`;
    text.style.strokeWidth = `${fontSize * (selected ? .24 : .18)}px`;
  }
  lines.forEach((row, index) => {
    const line = document.createElementNS(SVG_NS, "tspan");
    line.setAttribute("x", label.x);
    line.setAttribute("dy", lines.length === 1 ? "0" : index === 0 ? "-0.35em" : "1.1em");
    line.setAttribute("lang", row.lang);
    if (row.missing) line.setAttribute("class", "translation-missing");
    line.textContent = row.text; text.append(line);
  });
  setFilterData(text, label.filter_id);
  bindAnatomyElement(text, item);
  el.annotationLayer.append(text);
  const target = targetForLabel(label);
  if (!target) return;
  const hit = document.createElementNS(SVG_NS, "circle");
  hit.setAttribute("cx", target.x); hit.setAttribute("cy", target.y); hit.setAttribute("r", "15");
  hit.setAttribute("class", "annotation-hit"); hit.setAttribute("aria-label", anatomyDisplayName(item));
  setFilterData(hit, label.filter_id);
  bindAnatomyElement(hit, item); el.annotationLayer.append(hit);
  const dot = document.createElementNS(SVG_NS, "circle");
  dot.setAttribute("cx", target.x); dot.setAttribute("cy", target.y); dot.setAttribute("r", selected ? "6" : "4");
  dot.setAttribute("class", `annotation-target${interactionClass}`); dot.style.color = item.color;
  setFilterData(dot, label.filter_id);
  dot.setAttribute("aria-label", anatomyDisplayName(item)); bindAnatomyElement(dot, item); el.annotationLayer.append(dot);
}

function renderHoverTarget(target, selectionKey = null) {
  if (!targetFilterEnabled(target)) return;
  const item = structureFromTarget(target);
  const selected = item.key === selectionKey;
  const hit = document.createElementNS(SVG_NS, "circle");
  hit.setAttribute("cx", target.x); hit.setAttribute("cy", target.y); hit.setAttribute("r", "15");
  hit.setAttribute("class", "hover-hit"); hit.setAttribute("aria-label", anatomyDisplayName(item));
  setFilterData(hit, target.filter_id);
  bindAnatomyElement(hit, item); el.annotationLayer.append(hit);
  const dot = document.createElementNS(SVG_NS, "circle");
  dot.setAttribute("cx", target.x); dot.setAttribute("cy", target.y); dot.setAttribute("r", selected ? "6" : String(Math.max(3, Number(target.radius) || 3)));
  dot.setAttribute("class", `hover-dot${annotationInteractionClass([item.key], item.filterIds, selectionKey)}`); dot.style.color = item.color;
  setFilterData(dot, target.filter_id);
  dot.setAttribute("aria-label", anatomyDisplayName(item)); bindAnatomyElement(dot, item); el.annotationLayer.append(dot);
}

function bindAnatomyElement(node, item) {
  anatomyTouchItems.set(node, item);
  const activate = (event) => { event.stopPropagation(); selectStructure(item, { toggle: true }); };
  node.addEventListener("click", activate);
  node.addEventListener("keydown", (event) => { if (event.key === "Enter" || event.key === " ") { event.preventDefault(); activate(event); } });
  node.addEventListener("pointerenter", (event) => showTooltip(event, item));
  node.addEventListener("pointermove", (event) => showTooltip(event, item));
  node.addEventListener("pointerleave", hideTooltip);
}

function anatomyItemAt(node) {
  // SVG tspans and nested bilingual HTML lines share their parent's identity.
  for (let current = node; current && current !== el.anatomyViewport; current = current.parentNode) {
    const item = anatomyTouchItems.get(current);
    if (item) return item;
  }
  return undefined;
}

function showTooltip(event, item) {
  if (state.dragging || event.pointerType === "touch") return;
  el.anatomyTooltip.replaceChildren();
  const strong = document.createElement("strong"); setAnatomyName(strong, item);
  el.anatomyTooltip.append(strong);
  if (item.latin) { const small = document.createElement("small"); small.textContent = item.latin; el.anatomyTooltip.append(small); }
  el.anatomyTooltip.hidden = false;
  const margin = 14; const rect = el.anatomyTooltip.getBoundingClientRect();
  el.anatomyTooltip.style.left = `${Math.min(event.clientX + margin, window.innerWidth - rect.width - 8)}px`;
  el.anatomyTooltip.style.top = `${Math.min(event.clientY + margin, window.innerHeight - rect.height - 8)}px`;
}

function hideTooltip() { el.anatomyTooltip.hidden = true; }

function renderSliceStructures() {
  if (state.structureMode !== "slice") return;
  renderStructureRows(sliceStructures());
  el.structureListTitle.textContent = "Structures on this slice";
}

function renderStructureRows(rows) {
  const query = normalizeText(el.structureSearch.value);
  // Module results already match names OR description text on the server.
  const filtered = state.structureMode === "search" ? rows : rows.filter((item) => !query || normalizeText(`${item.name} ${anatomyValue(item).text} ${item.latin}`).includes(query));
  el.structureList.replaceChildren();
  filtered.forEach((item) => {
    const button = document.createElement("button"); button.type = "button";
    button.className = `structure-row${state.selectedStructure?.key === item.key ? " active" : ""}${filterInteractionClass(item.filterIds || (item.filterId ? [item.filterId] : []))}`;
    button.setAttribute("role", "listitem");
    const colour = document.createElement("span"); colour.className = "structure-colour"; colour.style.color = item.color || "#8fa8b0";
    const name = document.createElement("span"); name.className = "structure-name";
    const strong = document.createElement("strong"); setAnatomyName(strong, item);
    const latin = document.createElement("small"); latin.textContent = item.latin || "Anatomical structure";
    name.append(strong, latin);
    const kind = document.createElement("span"); kind.className = "structure-kind"; kind.textContent = item.kind;
    button.append(colour, name, kind); button.addEventListener("click", () => selectStructure(item, { toggle: true }));
    el.structureList.append(button);
  });
  el.structureCount.textContent = String(filtered.length);
  el.structureEmpty.hidden = filtered.length > 0;
  if (!filtered.length) el.structureEmpty.textContent = query ? "No matching structures." : "No structures available on this slice.";
}

function selectStructure(item, { toggle = false, highlightOnly = false } = {}) {
  hideTooltip();
  const previous = state.selectedStructure;
  if (toggle && !highlightOnly && !state.selectionHighlightOnly && previous?.key === item.key && (item.key?.startsWith("taxon:")
    || (item.label && item.label === previous.label) || (item.target && item.target === previous.target))) {
    closeDefinition();
    return;
  }
  state.selectedStructure = item;
  state.selectionHighlightOnly = highlightOnly;
  if (highlightOnly) {
    // Long press focuses verified label/leader identity, without an annotation panel.
    state.detailsVisible = false;
    state.definitionPeek = false;
    syncDetailPanel();
    renderSliceStructures();
    renderOverlay();
    return;
  }
  const openingPeek = !state.detailsVisible && !state.definitionPeek;
  if (!state.detailsVisible) state.definitionPeek = true;
  renderDefinition(item);
  syncDetailPanel();
  if (state.structureMode === "slice") renderSliceStructures();
  else renderSearchRows();
  renderOverlay();
  if (openingPeek) requestAnimationFrame(() => { if (state.capture) fitView(); });
}

function safeFragment(markup, fallback = "No anatomical definition is available for this structure.") {
  const template = document.createElement("template");
  template.innerHTML = String(markup || `<p>${fallback}</p>`);
  template.content.querySelectorAll("script,style,iframe,img,video,audio,object,embed,form,input,button,svg,math,template,link,meta,base").forEach((node) => node.remove());
  const allowed = new Set(["P", "BR", "DIV", "SPAN", "STRONG", "B", "EM", "I", "U", "SUB", "SUP", "UL", "OL", "LI", "H2", "H3", "H4", "H5", "BLOCKQUOTE", "TABLE", "THEAD", "TBODY", "TR", "TH", "TD", "HR"]);
  template.content.querySelectorAll("*").forEach((node) => {
    [...node.attributes].forEach((attribute) => node.removeAttribute(attribute.name));
    if (!allowed.has(node.tagName)) node.replaceWith(...node.childNodes);
  });
  return template.content;
}

function definitionContent(definition, references = false) {
  const htmlField = references ? "sources_html" : "description_html";
  const html = localizedValue("structures", definition.identity_key, htmlField, definition[htmlField]);
  const text = references ? { translated: false } : localizedValue("structures", definition.identity_key, "description_text", definition.description_text);
  const translated = html.translated || text.translated;
  const container = document.createElement("div"); container.className = "definition-copy";
  const appendCopy = (target, useTranslation) => {
    const htmlValue = useTranslation && html.translated ? html.text : !useTranslation ? definition[htmlField] : "";
    const textValue = useTranslation && text.translated ? text.text : !useTranslation ? definition.description_text : "";
    if (htmlValue) target.append(safeFragment(htmlValue));
    else if (!references && textValue) {
      const p = document.createElement("p"); p.textContent = textValue; target.append(p);
    } else target.append(safeFragment("", references ? "No references available." : undefined));
  };
  if (state.anatomyLanguage === "en-vi") {
    for (const lang of ["en", "vi"]) {
      const section = document.createElement("section"); section.lang = lang; section.className = "definition-language-block";
      const title = document.createElement("h4"); title.textContent = lang === "en" ? "English" : "Tiếng Việt"; section.append(title);
      if (lang === "vi" && !translated) {
        const p = document.createElement("p"); p.className = "translation-missing"; p.textContent = "Chưa có bản dịch"; section.append(p);
      } else appendCopy(section, lang === "vi");
      container.append(section);
    }
  } else {
    container.lang = translated ? AnatomyLanguage.locale(state.anatomyLanguage) : "en";
    appendCopy(container, translated);
  }
  return container;
}

function renderDefinition(item) {
  const definition = item.definition || {};
  el.definitionPanel.replaceChildren();
  el.definitionPanel.scrollTop = 0;
  const close = document.createElement("button");
  close.type = "button"; close.className = "definition-close-button"; close.textContent = "×";
  close.setAttribute("aria-label", "Close anatomical definition"); close.title = "Close definition";
  close.addEventListener("click", closeDefinition);
  if (!item.definition && item.target?.semantic_primary_ambiguous && item.target.coincident_definitions?.length) {
    const title = document.createElement("h3"); title.textContent = "Overlapping structures";
    const note = document.createElement("p"); note.textContent = "These structures share the exact same marker position. Select a definition:";
    el.definitionPanel.append(close, title, note);
    item.target.coincident_definitions.forEach((definition) => {
      const button = document.createElement("button"); button.type = "button";
      setLanguageName(button, definition.name, definitionValue(definition)); button.classList.add("tool-button");
      button.addEventListener("click", () => {
        state.selectedStructure = {...item, definition, name: definition.name};
        renderDefinition(state.selectedStructure);
      });
      el.definitionPanel.append(button);
    });
    return;
  }
  const category = document.createElement("div"); category.className = "definition-category"; category.style.color = item.color || "#24d5c6"; category.textContent = `${item.kind} · Anatomical structure`;
  const heading = document.createElement("h3"); setAnatomyName(heading, item);
  const latin = document.createElement("p"); latin.className = "definition-latin"; latin.textContent = definition.latin || item.latin || "Latin term unavailable";
  const copy = definitionContent(definition);
  el.definitionPanel.append(close, category, heading, latin, copy);
  if (definition.sources_html) {
    const details = document.createElement("details"); details.className = "definition-sources";
    const summary = document.createElement("summary"); summary.textContent = "Scientific references";
    const sources = definitionContent(definition, true);
    details.append(summary, sources); el.definitionPanel.append(details);
  }
  const meta = document.createElement("div"); meta.className = "definition-meta";
  [["Taxon ID", definition.taxon_id || item.taxonId || "—"], ["Identity", item.kind], ["Slice", currentSliceNumber() ?? "—"], ["Series", state.series?.label || "—"]].forEach(([key, value]) => {
    const box = document.createElement("div"); const small = document.createElement("small"); small.textContent = key;
    const span = document.createElement("span"); span.textContent = String(value); box.append(small, span); meta.append(box);
  });
  el.definitionPanel.append(meta);
}

function closeDefinition() {
  clearDefinition();
  renderOverlay();
  if (state.structureMode === "slice") renderSliceStructures();
  else renderSearchRows();
}

function clearDefinition() {
  const closingPeek = state.definitionPeek;
  state.definitionPeek = false;
  state.selectedStructure = null;
  state.selectionHighlightOnly = false;
  if (closingPeek) {
    syncDetailPanel();
    requestAnimationFrame(() => { if (state.capture) fitView(); });
  }
  el.definitionPanel.innerHTML = '<div class="definition-placeholder"><span>＋</span><strong>Select an anatomical structure</strong><p>Click a label, marker, or structure name to view its definition in the selected language and Latin terminology.</p></div>';
}

async function searchStructures() {
  if (!state.module) return;
  const token = ++state.searchRequest;
  const moduleKey = currentModuleKey();
  const language = state.anatomyLanguage;
  const query = el.structureSearch.value.trim();
  if (!query) { state.searchResults = []; renderSearchRows(); return; }
  const body = await api("/api/search", { key: moduleKey, q: query, lang: AnatomyLanguage.locale(language) });
  if (token !== state.searchRequest || moduleKey !== currentModuleKey() || language !== state.anatomyLanguage) return;
  state.searchResults = body.results.map((definition) => ({
    key: `taxon:${definition.identity_key || `${definition.ta_id ?? "legacy"}:${definition.taxon_id}`}`, taxonId: definition.taxon_id, name: definition.name,
    latin: definition.latin, definition, color: "#38bdf8", kind: "Definition",
  }));
  renderSearchRows();
}

function renderSearchRows() {
  if (state.structureMode !== "search") return;
  renderStructureRows(state.searchResults);
  el.structureListTitle.textContent = "Module search results";
}

function setStructureMode(mode) {
  state.structureMode = mode;
  const slice = mode === "slice";
  el.sliceStructuresTab.classList.toggle("active", slice); el.searchStructuresTab.classList.toggle("active", !slice);
  el.sliceStructuresTab.setAttribute("aria-selected", String(slice)); el.searchStructuresTab.setAttribute("aria-selected", String(!slice));
  if (slice) renderSliceStructures(); else searchStructures();
}

function filterDisplayName(filter) {
  return AnatomyLanguage.lines(state.anatomyLanguage, filter.name || `Name unavailable (filter ${filter.id})`, filterValue(filter)).map((row) => row.text).join("\n");
}

function filterValue(filter) {
  return localizedValue("filters", filter.id, "name", filter.name || `Name unavailable (filter ${filter.id})`);
}

function setFilterName(node, filter) {
  setLanguageName(node, filter.name || `Name unavailable (filter ${filter.id})`, filterValue(filter));
}

function displayFilters() {
  return (state.module?.filters || []).filter((filter) => filterDisplayName(filter))
    .sort((left, right) => Number(left.sort_order || 0) - Number(right.sort_order || 0));
}

function filterIndex() {
  return new Map((state.module?.filters || []).map((filter) => [String(filter.id), filter]));
}

function filterClosure(filterOrId) {
  const filter = typeof filterOrId === "object" ? filterOrId : filterIndex().get(String(filterOrId));
  if (!filter) return new Set();
  const captured = Array.isArray(filter.closure_ids) && filter.closure_ids.length
    ? filter.closure_ids : [filter.id, ...(filter.descendant_ids || [])];
  return new Set(captured.map(String));
}

function flattenedFilterBranch(root, indexed) {
  const rows = [];
  const walk = (filter, depth, trail) => {
    const id = String(filter.id);
    if (trail.has(id)) return;
    const children = (filter.children_ids || []).map((childId) => indexed.get(String(childId))).filter(Boolean)
      .sort((left, right) => Number(left.sort_order || 0) - Number(right.sort_order || 0));
    const hasDirectMembership = Number(filter.direct_point_count || 0) > 0 || !children.length;
    if (hasDirectMembership) rows.push({ ...filter, treeDepth: depth });
    children.forEach((child) => walk(child, depth + 1, new Set([...trail, id])));
  };
  walk(root, 0, new Set());
  return rows;
}

function filterHierarchyGroups() {
  const all = displayFilters();
  const indexed = new Map(all.map((filter) => [String(filter.id), filter]));
  const capturedRoots = state.module?.anatomical_parts?.roots || [];
  const roots = capturedRoots.map((id) => indexed.get(String(id))).filter(Boolean);
  if (!roots.length) roots.push(...all.filter((filter) => Number(filter.parents || 0) === 0));
  roots.sort((left, right) => Number(left.sort_order || 0) - Number(right.sort_order || 0));
  const used = new Set();
  const groups = roots.map((root) => {
    const items = flattenedFilterBranch(root, indexed);
    filterClosure(root).forEach((id) => used.add(id));
    return { key: `filter-group-${root.id}`, name: filterDisplayName(root), root, items: items.length ? items : [{ ...root, treeDepth: 0 }] };
  });
  all.filter((filter) => !used.has(String(filter.id))).forEach((filter) => {
    groups.push({ key: `filter-group-${filter.id}`, name: filterDisplayName(filter), root: filter, items: [{ ...filter, treeDepth: 0 }] });
  });
  return groups;
}

function filterGroupSelection(group) {
  const selected = group.items.filter((filter) => state.activeFilters.has(String(filter.id))).length;
  return { selected, total: group.items.length, all: selected === group.items.length, none: selected === 0 };
}

function afterFilterChange() {
  // Touch browsers synthesize hover without mouse leave. A stale preview used
  // to override a disabled checkbox, making the switch look unresponsive.
  state.previewFilterIds.clear();
  if (state.pinnedHighlightFilterId && ![...state.highlightFilterIds].some((id) => state.activeFilters.has(id))) {
    state.pinnedHighlightFilterId = null;
    state.highlightFilterIds.clear();
  }
  if (state.selectedStructure?.filterId && !filterEnabled(state.selectedStructure.filterId)) clearDefinition();
  renderFilters(); renderOverlay(); renderSliceStructures();
}

function updateAnatomyNameStatus() {
  const labels = (state.capture?.labels || []).filter((label) => String(label.text || "").trim());
  const visible = state.labelsVisible ? labels.filter(labelFilterEnabled).length : 0;
  const markers = (state.capture?.hover_targets || []).filter(targetVerified).filter(targetFilterEnabled).length;
  const unbound = labels.filter((label) => label.filter_id == null).length;
  el.anatomyNameStatus.textContent = `${visible}/${labels.length} labels shown · ${markers} named markers (hover)`
    + (unbound ? ` · ${unbound} labels have no verified part link; their text remains visible.` : "");
}

function setAllAnatomyVisible({ sourceDefaults = false } = {}) {
  state.activeFilters = new Set((state.module?.filters || [])
    .filter((filter) => !sourceDefaults || Number(filter.active) === 1).map((filter) => String(filter.id)));
  state.labelsVisible = true;
  state.previewFilterIds.clear(); state.highlightFilterIds.clear(); state.pinnedHighlightFilterId = null;
  syncVisibilityControls(); afterFilterChange(); savePreferences();
}

function setFilterSelection(filterOrId, enabled) {
  filterClosure(filterOrId).forEach((id) => {
    const filter = filterIndex().get(id);
    if (!filter || Number(filter.direct_point_count || 0) > 0 || !(filter.children_ids || []).length) {
      if (enabled) state.activeFilters.add(id); else state.activeFilters.delete(id);
    }
  });
}

function toggleFilter(id, force = null) {
  const key = String(id);
  const enabled = force == null ? !state.activeFilters.has(key) : Boolean(force);
  if (enabled) state.activeFilters.add(key); else state.activeFilters.delete(key);
  afterFilterChange();
}

function toggleFilterGroup(group, force = null) {
  const selection = filterGroupSelection(group);
  const enabled = force == null ? !selection.all : Boolean(force);
  group.items.forEach((filter) => {
    const id = String(filter.id);
    if (enabled) state.activeFilters.add(id); else state.activeFilters.delete(id);
  });
  afterFilterChange();
}

function setPreviewFilter(filterOrId) {
  state.previewFilterIds = filterOrId == null ? new Set() : filterClosure(filterOrId);
  renderOverlay();
  renderSliceStructures();
  updateFilterInteractionState();
}

function setHighlightFilter(filterOrId, { pinned = false } = {}) {
  state.highlightFilterIds = filterOrId == null ? new Set() : filterClosure(filterOrId);
  if (pinned) state.pinnedHighlightFilterId = filterOrId == null ? null : String(typeof filterOrId === "object" ? filterOrId.id : filterOrId);
  renderOverlay();
  renderSliceStructures();
  updateFilterInteractionState();
}

function togglePinnedHighlight(filter) {
  const id = String(filter.id);
  if (state.pinnedHighlightFilterId === id) {
    state.pinnedHighlightFilterId = null;
    setHighlightFilter(null);
    return;
  }
  setFilterSelection(filter, true);
  state.pinnedHighlightFilterId = id;
  state.highlightFilterIds = filterClosure(filter);
  afterFilterChange();
}

function filterColoursOnSlice(filter) {
  const ids = filterClosure(filter);
  const colours = [];
  const add = (value) => {
    const colour = colourKey(value);
    if (colour && !colours.includes(colour)) colours.push(colour);
  };
  (state.capture?.labels || []).forEach((label) => { if (ids.has(String(label.filter_id))) add(label.color); });
  (state.capture?.hover_targets || []).forEach((target) => {
    const targetIds = target.semantic_primary_ambiguous ? (target.semantic_identities || []).map((item) => item.filter_id) : [target.filter_id];
    if (targetIds.some((id) => ids.has(String(id)))) add(target.fill_color || target.color);
  });
  return colours.slice(0, 3);
}

function makeFilterIcon(filter) {
  const icon = document.createElement("i"); icon.className = "menu-filter-icon";
  if (filter.icon_url) {
    const image = document.createElement("img"); image.alt = "";
    // Data icons need the same authenticated fetch/cache path as images. A raw
    // <img src=/data/...> cannot carry the viewer-session header on the website.
    assignCachedImage(image, versionedDataUrl(filter.icon_url), { priority: 1 });
    icon.append(image);
  } else icon.textContent = filterDisplayName(filter).slice(0, 3).toUpperCase();
  return icon;
}

function makeHighlightButton(filter) {
  const button = document.createElement("button"); button.type = "button"; button.className = "filter-highlight-button";
  button.dataset.highlightFilterId = String(filter.id);
  button.setAttribute("aria-label", `Highlight all structures in ${filterDisplayName(filter)}`);
  button.title = "Hover to highlight this anatomical part; click to pin";
  const colours = filterColoursOnSlice(filter);
  const left = document.createElement("i"); const right = document.createElement("i");
  left.style.setProperty("--filter-ring", colours[0] || "#8a8d91");
  right.style.setProperty("--filter-ring", colours[1] || colours[0] || "#c7c9cc");
  button.append(left, right);
  button.addEventListener("pointerenter", (event) => { if (event.pointerType !== "mouse") return; event.stopPropagation(); setHighlightFilter(filter); });
  button.addEventListener("pointerleave", (event) => {
    if (event.pointerType !== "mouse") return;
    event.stopPropagation();
    if (state.pinnedHighlightFilterId) setHighlightFilter(state.pinnedHighlightFilterId);
    else setHighlightFilter(null);
  });
  button.addEventListener("click", (event) => { event.preventDefault(); event.stopPropagation(); togglePinnedHighlight(filter); });
  return button;
}

function bindFilterPreview(node, filter) {
  node.dataset.previewFilterId = String(filter.id);
  node.addEventListener("pointerenter", (event) => { if (event.pointerType === "mouse") setPreviewFilter(filter); });
  node.addEventListener("pointerleave", (event) => { if (event.pointerType === "mouse") setPreviewFilter(null); });
}

function updateFilterInteractionState() {
  document.querySelectorAll("[data-preview-filter-id]").forEach((node) => {
    const closure = filterClosure(node.dataset.previewFilterId);
    node.classList.toggle("is-previewing", [...closure].some((id) => state.previewFilterIds.has(id)));
  });
  document.querySelectorAll("[data-highlight-filter-id]").forEach((node) => {
    const id = node.dataset.highlightFilterId;
    node.classList.toggle("is-highlighting", state.highlightFilterIds.has(id));
    node.classList.toggle("is-pinned", state.pinnedHighlightFilterId === id);
    node.setAttribute("aria-pressed", String(state.pinnedHighlightFilterId === id));
  });
}

function renderFilters() {
  const menuScrollTop = el.optionsMenu?.scrollTop || 0;
  const filters = displayFilters();
  const groups = filterHierarchyGroups();
  const metadata = state.module?.anatomical_parts || {};
  const missingNames = metadata.missing_name_filter_ids || [];
  el.filterMetadataStatus.hidden = !missingNames.length;
  el.filterMetadataStatus.textContent = `${missingNames.length} part names are unavailable in this module. Their IDs are shown explicitly; category codes are not used as anatomy names.`;
  el.filterCount.textContent = String(groups.length);
  el.filterList.replaceChildren();
  el.menuFilterList.replaceChildren();
  groups.forEach((group) => {
    const selection = filterGroupSelection(group);
    const button = document.createElement("button"); button.type = "button";
    button.className = "filter-button filter-group-button";
    button.setAttribute("aria-pressed", String(selection.all));
    button.dataset.state = selection.all ? "all" : selection.none ? "none" : "mixed";
    bindFilterPreview(button, group.root);
    const check = document.createElement("span"); check.className = "filter-check";
    const name = document.createElement("span"); setFilterName(name, group.root);
    const count = document.createElement("small"); count.textContent = `${selection.selected}/${selection.total}`;
    button.append(check, name, count);
    button.addEventListener("click", () => toggleFilterGroup(group));
    el.filterList.append(button);

    const section = document.createElement("section"); section.className = "menu-filter-group";
    section.dataset.groupKey = group.key; section.dataset.groupName = group.name;
    const standalone = group.items.length === 1 && String(group.items[0].id) === String(group.root.id)
      && !(group.root.children_ids || []).length;
    const header = document.createElement("div"); header.className = "menu-filter-group-header";
    if (standalone) {
      header.classList.add("menu-filter-row", "menu-filter-root-row");
      header.dataset.filterId = String(group.root.id);
      bindFilterPreview(header, group.root);
    }
    const expand = document.createElement("button"); expand.type = "button"; expand.className = "menu-filter-group-expand";
    const expanded = state.expandedFilterGroups.has(group.key);
    expand.setAttribute("aria-expanded", String(!standalone && expanded));
    const chevron = document.createElement("i"); chevron.className = "filter-chevron"; chevron.textContent = standalone ? "" : "›";
    const rootIcon = makeFilterIcon(group.root); rootIcon.classList.add("menu-filter-root-icon");
    const copy = document.createElement("span");
    const title = document.createElement("b"); setFilterName(title, group.root);
    title.title = group.name;
    const meta = document.createElement("small"); meta.textContent = `${selection.selected} of ${selection.total} visible`;
    copy.append(title, meta); expand.append(chevron, rootIcon, copy);
    if (!standalone) bindFilterPreview(expand, group.root);
    const groupHighlight = makeHighlightButton(group.root);
    const masterLabel = document.createElement("label"); masterLabel.className = "compact-switch"; masterLabel.title = `Show or hide all ${group.name} layers`;
    const master = document.createElement("input"); master.type = "checkbox"; master.checked = selection.all; master.indeterminate = !selection.all && !selection.none;
    master.setAttribute("aria-label", `Show or hide ${group.name}`);
    const masterControl = document.createElement("i"); masterControl.className = "switch-control";
    master.addEventListener("change", () => toggleFilterGroup(group, master.checked));
    masterLabel.append(master, masterControl); header.append(expand, groupHighlight, masterLabel);
    const children = document.createElement("div"); children.className = "menu-filter-children"; children.hidden = !expanded;
    if (!standalone) group.items.forEach((filter) => {
      const id = String(filter.id);
      const row = document.createElement("div"); row.className = "switch-row menu-filter-row"; row.dataset.filterId = id;
      row.style.setProperty("--filter-depth", String(filter.treeDepth || 0));
      bindFilterPreview(row, filter);
      const childCopy = document.createElement("span");
      const icon = makeFilterIcon(filter);
      const childTitle = document.createElement("b"); setFilterName(childTitle, filter);
      childTitle.title = filterDisplayName(filter);
      const childMeta = document.createElement("small");
      childMeta.textContent = filter.effective_taxon_count == null ? `Filter ${id}`
        : `${Number(filter.effective_taxon_count)} structures · ${Number(filter.effective_slice_count || 0)} slices`;
      childCopy.append(icon, childTitle, childMeta);
      const highlight = makeHighlightButton(filter);
      const input = document.createElement("input"); input.type = "checkbox"; input.checked = state.activeFilters.has(id); input.dataset.filterId = id;
      input.setAttribute("aria-label", `Show or hide ${filterDisplayName(filter)}`);
      const control = document.createElement("i"); control.className = "switch-control";
      const switchLabel = document.createElement("label"); switchLabel.className = "compact-switch";
      input.addEventListener("change", () => toggleFilter(id, input.checked));
      childCopy.addEventListener("click", () => toggleFilter(id));
      switchLabel.append(input, control);
      row.append(childCopy, highlight, switchLabel); children.append(row);
    });
    expand.addEventListener("click", () => {
      if (standalone) { toggleFilter(group.root.id); return; }
      if (expanded) state.expandedFilterGroups.delete(group.key); else state.expandedFilterGroups.add(group.key);
      renderFilters();
    });
    section.append(header); if (!standalone) section.append(children); el.menuFilterList.append(section);
  });
  const selected = filters.filter((filter) => state.activeFilters.has(String(filter.id))).length;
  el.menuSelectAllFilters.checked = filters.length > 0 && selected === filters.length;
  el.menuSelectAllFilters.indeterminate = selected > 0 && selected < filters.length;
  if (el.optionsMenu) el.optionsMenu.scrollTop = menuScrollTop;
  updateFilterInteractionState();
}

function updateTimeline({ pending = false } = {}) {
  const total = state.variant?.slices.length || 0;
  const number = currentSliceNumber();
  el.sliceSlider.disabled = !total; el.sliceSlider.min = "1"; el.sliceSlider.max = String(Math.max(1, total)); el.sliceSlider.value = String(state.slicePosition + 1);
  el.sliceNumber.textContent = number == null ? "—" : pad(number);
  el.sliceTotal.textContent = `/ ${total}`;
  el.sliceIdLabel.textContent = pending ? "SLICE ID LOADING" : `SLICE ID ${state.capture?.slice?.id || "—"}`;
  el.sliceMeta.textContent = `${state.series?.label?.toUpperCase() || "SERIES"} · ${state.variant?.label?.toUpperCase() || ""} · ${state.slicePosition + 1}/${total}`;
  el.previousSliceButton.disabled = state.slicePosition <= 0;
  el.nextSliceButton.disabled = state.slicePosition >= total - 1;
}

async function setSlicePosition(position, { fromWheel = false } = {}) {
  if (!fromWheel && state.wheelFrame) {
    cancelAnimationFrame(state.wheelFrame);
    state.wheelFrame = 0; state.wheelTargetPosition = null;
  }
  const total = state.variant?.slices.length || 0;
  const next = Math.max(0, Math.min(Number(position), total - 1));
  if (!total || next === state.slicePosition) return sliceLoadWorker;
  lastSliceDirection = Math.sign(next - state.slicePosition) || lastSliceDirection;
  state.slicePosition = next;
  updateTimeline({ pending: true });
  await showCurrentSlice();
}

function sliceImageUrl(number) {
  if (!state.module || !state.series || !state.variant) return "";
  return imageUrlFor(state.series, state.variant, number);
}

function imageUrlFor(series, variant, number) {
  if (!state.module || !series || !variant || number == null) return "";
  const [region, slug] = state.module.key.split("/");
  return versionedDataUrl(`/data/${[region, slug, "rendered", series.directory, variant.directory, `slice_${pad(number)}.png`].map(encodeURIComponent).join("/")}`);
}

function renderFilmstrip() {
  el.filmstrip.replaceChildren();
  if (!state.variant || !state.filmstripVisible) return;
  const start = Math.max(0, state.slicePosition - 3);
  const end = Math.min(state.variant.slices.length, state.slicePosition + 4);
  for (let index = start; index < end; index += 1) {
    const number = state.variant.slices[index]; const button = document.createElement("button"); button.type = "button";
    button.className = `filmstrip-button${index === state.slicePosition ? " active" : ""}`;
    button.setAttribute("aria-label", `Open slice ${number}`);
    const image = document.createElement("img"); image.alt = ""; image.loading = "lazy";
    assignCachedImage(image, sliceImageUrl(number));
    const label = document.createElement("small"); label.textContent = pad(number);
    button.append(image, label); button.addEventListener("click", () => setSlicePosition(index)); el.filmstrip.append(button);
  }
}

function seriesPreloadOrder(center = state.slicePosition, direction = 1) {
  const total = state.variant?.slices?.length || 0;
  const forward = direction >= 0 ? 1 : -1;
  const positions = total ? [Math.max(0, Math.min(center, total - 1))] : [];
  for (let distance = 1; positions.length < total; distance += 1) {
    const preferred = center + forward * distance;
    const opposite = center - forward * distance;
    if (preferred >= 0 && preferred < total) positions.push(preferred);
    if (opposite >= 0 && opposite < total) positions.push(opposite);
  }
  return positions;
}

function prioritizeSeriesPreload(direction = 1) {
  const session = seriesPreloadSession;
  if (!session || session.key !== activeSeriesKey() || !session.queue.length) return;
  const pending = new Map(session.queue.map((descriptor) => [descriptor.key, descriptor]));
  session.queue = seriesPreloadOrder(state.slicePosition, direction)
    .map((position) => sliceDescriptor(position))
    .map((descriptor) => descriptor && pending.get(descriptor.key))
    .filter(Boolean);
}

function pumpSeriesPreload() {
  const session = seriesPreloadSession;
  if (!session) return;
  while (session === seriesPreloadSession
      && session.active < SERIES_PRELOAD_CONCURRENCY && session.queue.length) {
    const descriptor = session.queue.shift();
    if (!descriptorBelongsToActiveSeries(descriptor)) continue;
    session.active += 1;
    fetchSliceCapture(descriptor, { prefetch: true })
      .then((capture) => session === seriesPreloadSession && descriptorBelongsToActiveSeries(descriptor)
        ? warmSliceImageBytes(versionedDataUrl(capture.image_url)) : null)
      .then(() => {
        if (session === seriesPreloadSession && descriptorBelongsToActiveSeries(descriptor)) {
          session.completed += 1;
        }
      })
      .catch(() => {
        if (session !== seriesPreloadSession || !descriptorBelongsToActiveSeries(descriptor)) return;
        if (descriptor.preloadAttempts < SERIES_PRELOAD_RETRIES) {
          descriptor.preloadAttempts += 1;
          session.queue.push(descriptor);
        } else session.failed += 1;
      })
      .finally(() => {
        if (session !== seriesPreloadSession) return;
        session.active -= 1;
        updatePreloadStatus();
        pumpSeriesPreload();
      });
  }
}

function ensureFullSeriesPreload(direction = 1) {
  const key = activeSeriesKey();
  if (!key) return;
  if (!seriesPreloadSession || seriesPreloadSession.key !== key) {
    const queue = seriesPreloadOrder(state.slicePosition, direction)
      .map((position) => sliceDescriptor(position)).filter(Boolean);
    seriesPreloadSession = {
      key, generation: ++seriesPreloadGeneration, queue,
      total: queue.length, completed: 0, failed: 0, active: 0,
    };
  } else prioritizeSeriesPreload(direction);
  updatePreloadStatus();
  pumpSeriesPreload();
}

function updatePreloadStatus() {
  if (!el.preloadStatus) return;
  const session = seriesPreloadSession;
  el.preloadStatus.textContent = session ? `Preload ${session.completed}/${session.total}${session.failed ? ` · ${session.failed} retry needed` : ""}` : "";
  el.preloadStatus.title = "Current series preload progress; LRU/TTL may evict older resources after download.";
}

function pumpDecodePrefetch() {
  while (decodePrefetchActive < SLICE_DECODE_CONCURRENCY && decodePrefetchQueue.length) {
    const descriptor = decodePrefetchQueue.shift();
    if (!descriptorBelongsToActiveSeries(descriptor)) continue;
    decodePrefetchActive += 1;
    fetchSliceCapture(descriptor, { prefetch: true, priority: 1 })
      .then((capture) => descriptorBelongsToActiveSeries(descriptor)
        ? decodeSliceImage(versionedDataUrl(capture.image_url), { lowPriority: true }) : null)
      .catch(() => {})
      .finally(() => { decodePrefetchActive -= 1; pumpDecodePrefetch(); });
  }
}

function preloadNeighbours(direction = 1) {
  const forward = direction >= 0 ? 1 : -1;
  const positions = [];
  for (let distance = 1; distance <= SLICE_DECODE_FORWARD; distance += 1) positions.push(state.slicePosition + forward * distance);
  for (let distance = 1; distance <= SLICE_DECODE_BACKWARD; distance += 1) positions.push(state.slicePosition - forward * distance);
  const queued = new Set();
  decodePrefetchQueue = positions.map((position) => sliceDescriptor(position)).filter((descriptor) => {
    if (!descriptor || queued.has(descriptor.key)) return false;
    queued.add(descriptor.key);
    return true;
  });
  pumpDecodePrefetch();
  ensureFullSeriesPreload(direction);
}

function renderCaptureStatus() {
  const status = state.capture?.capture_status || state.capture?.core_status || "";
  const incomplete = status && !["COMPLETE", "PASS"].includes(String(status).toUpperCase());
  el.captureStatus.hidden = !incomplete;
  if (incomplete) el.captureStatus.textContent = `DATA ${status} · available content shown`;
}

function fitView() {
  const width = Number(state.capture?.image?.width) || 1890;
  const height = Number(state.capture?.image?.height) || 1091;
  const bounds = el.anatomyViewport.getBoundingClientRect();
  // Captured text can extend outside the source image. Fit the image AND labels,
  // otherwise docking a panel clips long anatomical names at either edge.
  const overlay = el.annotationLayer.getBBox();
  const left = Math.min(0, overlay.x - 8);
  const top = Math.min(0, overlay.y - 8);
  const right = Math.max(width, overlay.x + overlay.width + 8);
  const bottom = Math.max(height, overlay.y + overlay.height + 8);
  state.fitZoom = Math.min((bounds.width - 34) / (right - left), (bounds.height - 34) / (bottom - top));
  state.zoom = Math.max(.05, state.fitZoom);
  state.panX = (width / 2 - (left + right) / 2) * state.zoom;
  state.panY = (height / 2 - (top + bottom) / 2) * state.zoom;
  applyTransform();
}

function applyTransform() {
  el.scene.style.transform = `translate(calc(-50% + ${state.panX}px), calc(-50% + ${state.panY}px)) scale(${state.zoom})`;
  el.zoomValue.textContent = `${Math.round((state.zoom / Math.max(state.fitZoom, .0001)) * 100)}%`;
  el.anatomyImage.style.filter = `brightness(${state.brightness}%) contrast(${state.contrast}%)`;
}

function changeZoom(factor, origin = null) {
  if (!state.capture) return;
  const previous = state.zoom;
  const min = Math.max(.03, state.fitZoom * .35); const max = Math.max(6, state.fitZoom * 12);
  state.zoom = Math.max(min, Math.min(max, state.zoom * factor));
  if (origin && previous !== state.zoom) {
    const rect = el.anatomyViewport.getBoundingClientRect();
    const dx = origin.clientX - (rect.left + rect.width / 2) - state.panX;
    const dy = origin.clientY - (rect.top + rect.height / 2) - state.panY;
    const ratio = state.zoom / previous;
    state.panX -= dx * (ratio - 1); state.panY -= dy * (ratio - 1);
  }
  applyTransform();
}

function setInteractionMode(mode) {
  endDrag();
  state.interactionMode = mode;
  [["scroll", el.scrollModeButton], ["pan", el.panModeButton], ["zoom", el.zoomModeButton]].forEach(([name, button]) => {
    const active = name === mode; button.classList.toggle("active", active); button.setAttribute("aria-pressed", String(active));
  });
  el.anatomyViewport.dataset.mode = mode;
}

function toggleLabels() {
  state.labelsVisible = !state.labelsVisible;
  syncVisibilityControls(); savePreferences();
}

function toggleTargets() {
  state.targetsVisible = !state.targetsVisible;
  syncVisibilityControls(); savePreferences();
}

function openModuleCatalogue() {
  el.moduleCataloguePopover.hidden = false;
  el.moduleCatalogueButton.setAttribute("aria-expanded", "true");
  requestAnimationFrame(() => el.moduleSearch.focus({ preventScroll: true }));
  loadCatalogue({ libraryOnly: true }).catch(() => {
    el.dataBadge.title = "Library refresh failed · use Refresh to retry";
  });
}

function closeModuleCatalogue() {
  el.moduleCataloguePopover.hidden = true;
  el.moduleCatalogueButton.setAttribute("aria-expanded", "false");
}

function toggleModuleCatalogue() {
  if (el.moduleCataloguePopover.hidden) openModuleCatalogue();
  else closeModuleCatalogue();
}

function setMprVisible(value) {
  state.mprVisible = Boolean(value);
  applyMprWidth(state.mprWidth);
  el.app.classList.toggle("mpr-open", state.mprVisible);
  el.mprContent.hidden = !state.mprVisible;
  el.mprResizeHandle.hidden = !state.mprVisible;
  el.mprToggleButton.setAttribute("aria-expanded", String(state.mprVisible));
  if (!state.mprVisible && el.mprPanel.contains(document.activeElement)) {
    el.mprToggleButton.focus({ preventScroll: true });
  }
  if (state.mprVisible) renderMprPanel();
  savePreferences();
  window.setTimeout(() => { if (state.capture) fitView(); }, 220);
}

function mprWidthBounds() {
  const minimum = 240;
  const menuWidth = el.optionsMenu.hidden ? 0 : el.optionsMenu.getBoundingClientRect().width;
  const detailsWidth = detailsPanelVisible() && window.innerWidth > 1000 ? el.detailPanel.getBoundingClientRect().width : 0;
  const viewerReserve = menuWidth + detailsWidth + 420;
  const maximum = Math.max(minimum, Math.min(620, window.innerWidth - viewerReserve));
  return { minimum, maximum };
}

function applyMprWidth(value, { persist = false } = {}) {
  const { minimum, maximum } = mprWidthBounds();
  state.mprWidth = Math.round(Math.max(minimum, Math.min(Number(value) || 320, maximum)));
  el.app.style.setProperty("--mpr-open-w", `${state.mprWidth}px`);
  el.mprResizeHandle.setAttribute("aria-valuemin", String(minimum));
  el.mprResizeHandle.setAttribute("aria-valuemax", String(maximum));
  el.mprResizeHandle.setAttribute("aria-valuenow", String(state.mprWidth));
  el.mprResizeHandle.setAttribute("aria-valuetext", `${state.mprWidth} pixels`);
  if (persist) savePreferences();
}

function startMprResize(event) {
  if (!state.mprVisible || event.button !== 0) return;
  event.preventDefault();
  state.mprResizing = true;
  el.app.classList.add("mpr-resizing");
  el.mprResizeHandle.setPointerCapture?.(event.pointerId);
}

function moveMprResize(event) {
  if (!state.mprResizing) return;
  event.preventDefault();
  applyMprWidth(event.clientX - el.mprPanel.getBoundingClientRect().left);
}

function endMprResize() {
  if (!state.mprResizing) return;
  state.mprResizing = false;
  el.app.classList.remove("mpr-resizing");
  savePreferences();
  if (state.capture) fitView();
}

function handleMprResizeKeyboard(event) {
  const { minimum, maximum } = mprWidthBounds();
  let next = state.mprWidth;
  if (event.key === "ArrowLeft") next -= event.shiftKey ? 40 : 12;
  else if (event.key === "ArrowRight") next += event.shiftKey ? 40 : 12;
  else if (event.key === "Home") next = minimum;
  else if (event.key === "End") next = maximum;
  else return;
  event.preventDefault();
  applyMprWidth(next, { persist: true });
  if (state.capture) fitView();
}

function setMprFitWidth(value) {
  state.mprFitWidth = Boolean(value);
  el.mprPanel.classList.toggle("fit-width", state.mprFitWidth);
  el.mprFitWidthButton.setAttribute("aria-pressed", String(state.mprFitWidth));
  el.mprFitWidthButton.textContent = state.mprFitWidth ? "CROP VIEW" : "FIT WIDTH";
  el.mprFitWidthButton.title = state.mprFitWidth
    ? "Return to cropped anatomical reference images"
    : "Fit complete reference images to panel width";
  if (state.mprVisible) renderMprPanel();
  savePreferences();
}

function parsedCrossReference(destinationSortOrder) {
  return (state.capture?.cross_references || []).find((row) =>
    Number(row.destination_sort_order) === Number(destinationSortOrder)
  ) || null;
}

function appendMprCrossReference(frame, image, reference, series) {
  const points = reference?.points || [];
  let svg = null; let line = null;
  if (points.length >= 2) {
    svg = document.createElementNS(SVG_NS, "svg");
    svg.setAttribute("class", "mpr-cross-reference");
    svg.setAttribute("preserveAspectRatio", "none");
    svg.setAttribute("aria-hidden", "true");
    line = document.createElementNS(SVG_NS, "polyline");
    svg.append(line); frame.append(svg);
  }
  const layout = () => {
    const sourceWidth = Number(reference?.coordinate_space?.width) || 173;
    const sourceHeight = Number(reference?.coordinate_space?.height) || 215;
    if (state.mprFitWidth && image.naturalWidth && image.naturalHeight) {
      const imageWidth = image.naturalWidth; const imageHeight = image.naturalHeight;
      frame.style.aspectRatio = `${imageWidth} / ${imageHeight}`;
      if (svg && line) {
        const sliceWidth = Number(series.slices_width) || imageWidth;
        const sliceHeight = Number(series.slices_height) || imageHeight;
        const left = Math.max(0, (imageWidth - sliceWidth) / 2);
        const top = Math.max(0, (imageHeight - sliceHeight) / 2);
        svg.setAttribute("viewBox", `0 0 ${imageWidth} ${imageHeight}`);
        line.setAttribute("points", points.map((point) => {
          const x = left + (Number(point.x) / sourceWidth) * sliceWidth;
          const y = top + (Number(point.y) / sourceHeight) * sliceHeight;
          return `${x},${y}`;
        }).join(" "));
      }
    } else {
      frame.style.removeProperty("aspect-ratio");
      if (svg && line) {
        svg.setAttribute("viewBox", `0 0 ${sourceWidth} ${sourceHeight}`);
        line.setAttribute("points", points.map((point) => `${Number(point.x)},${Number(point.y)}`).join(" "));
      }
    }
  };
  image.addEventListener("load", layout, { once: true });
  if (image.complete && image.naturalWidth) layout();
}

function renderMprPanel() {
  if (!state.mprVisible) return;
  el.mprViews.replaceChildren();
  if (!state.module || !state.series || !state.variant) return;
  const references = (state.module.series || []).filter((series) =>
    series.directory !== state.series.directory && series.variants.some((variant) => variant.slice_count)
  );
  references.forEach((series) => {
    const variant = series.variants.find((candidate) =>
      candidate.slice_count && normalizeText(candidate.label) === normalizeText(state.variant.label)
    ) || series.variants.find((candidate) => candidate.slice_count);
    if (!variant) return;
    const previewPosition = Math.floor((variant.slices.length - 1) / 2);
    const previewNumber = variant.slices[previewPosition];
    const crossReference = parsedCrossReference(series.sort_order);
    const card = document.createElement("button");
    card.type = "button";
    card.className = "mpr-card";
    card.dataset.series = series.directory;
    card.setAttribute("aria-label", `Open ${series.label} series`);
    const header = document.createElement("span"); header.className = "mpr-card-header";
    const title = document.createElement("strong"); title.textContent = series.label.toUpperCase();
    const meta = document.createElement("small");
    meta.textContent = crossReference ? "REFERENCE" : `${variant.slice_count} SLICES`;
    header.append(title, meta);
    const frame = document.createElement("span"); frame.className = "mpr-frame";
    const image = document.createElement("img"); image.alt = `${series.label} reference image`;
    image.loading = "lazy"; assignCachedImage(image, imageUrlFor(series, variant, previewNumber));
    frame.append(image);
    appendMprCrossReference(frame, image, crossReference, series);
    card.append(header, frame);
    card.addEventListener("click", () => selectVariant(series.directory, variant.directory, { wantedSlice: previewNumber, resetView: true }));
    el.mprViews.append(card);
  });
  if (!references.length) {
    const empty = document.createElement("p"); empty.className = "mpr-empty";
    empty.textContent = "No additional reference planes are available for this module.";
    el.mprViews.append(empty);
  }
}

function detailsPanelVisible() {
  return state.detailsVisible || state.definitionPeek;
}

function syncDetailPanel() {
  const visible = detailsPanelVisible();
  // Keep the full list/search preference off while a label's definition is shown.
  if (!visible && el.detailPanel.contains(document.activeElement)) {
    el.detailDrawerButton.focus({ preventScroll: true });
  }
  el.app.classList.toggle("hide-details", !visible);
  el.detailPanel.classList.toggle("definition-only", state.definitionPeek && !state.detailsVisible);
  el.detailPanel.classList.toggle("open", visible);
  el.detailPanel.setAttribute("aria-hidden", String(!visible));
  el.detailPanel.inert = !visible;
  el.menuDetailsToggle.checked = visible;
  el.detailDrawerButton.setAttribute("aria-expanded", String(visible));
  el.detailDrawerButton.classList.toggle("active", visible);
  el.detailDrawerButton.title = visible ? "Hide Detail" : "Show Detail";
}

function syncVisibilityControls() {
  el.labelsButton.classList.toggle("active", state.labelsVisible);
  el.labelsButton.setAttribute("aria-pressed", String(state.labelsVisible));
  el.targetsButton.classList.toggle("active", state.targetsVisible);
  el.targetsButton.setAttribute("aria-pressed", String(state.targetsVisible));
  el.annotationLayer.classList.toggle("labels-hidden", !state.labelsVisible);
  el.annotationLayer.classList.toggle("leaders-hidden", !state.leadersVisible);
  el.annotationLayer.classList.toggle("targets-hidden", !state.targetsVisible);
  el.app.classList.toggle("hide-orientation", !state.orientationVisible);
  el.app.classList.toggle("hide-filmstrip", !state.filmstripVisible);
  syncDetailPanel();
  el.app.classList.toggle("hide-adjustments", !state.adjustmentsVisible);
  el.menuLabelsToggle.checked = state.labelsVisible;
  el.menuLeadersToggle.checked = state.leadersVisible;
  el.menuTargetsToggle.checked = state.targetsVisible;
  el.menuOrientationToggle.checked = state.orientationVisible;
  el.menuFilmstripToggle.checked = state.filmstripVisible;
  el.menuAdjustmentsToggle.checked = state.adjustmentsVisible;
  el.optionsMenuPinButton.setAttribute("aria-pressed", String(state.menuPinned));
  el.optionsMenu.classList.toggle("pinned", state.menuPinned);
  updateAnatomyNameStatus();
}

function setVisibility(field, value) {
  if (field === "detailsVisible") state.definitionPeek = false;
  state[field] = Boolean(value);
  syncVisibilityControls();
  if (field === "filmstripVisible") renderFilmstrip();
  savePreferences();
  window.setTimeout(() => { if (state.capture) fitView(); }, 30);
}

function toggleDetailsPanel() {
  setVisibility("detailsVisible", !detailsPanelVisible());
}

function openOptionsMenu({ focus = true } = {}) {
  el.optionsMenu.hidden = false;
  el.optionsMenu.classList.add("open");
  el.app.classList.add("menu-open");
  el.optionsMenu.setAttribute("aria-hidden", "false");
  el.optionsMenuButton.setAttribute("aria-expanded", "true");
  if (focus) el.optionsMenuCloseButton.focus({ preventScroll: true });
  requestAnimationFrame(() => { applyMprWidth(state.mprWidth); if (state.capture) fitView(); });
}

function closeOptionsMenu({ force = false, focus = true } = {}) {
  if (state.menuPinned && !force) return;
  el.optionsMenu.classList.remove("open");
  el.optionsMenu.hidden = true;
  el.app.classList.remove("menu-open");
  el.optionsMenu.setAttribute("aria-hidden", "true");
  el.optionsMenuButton.setAttribute("aria-expanded", "false");
  state.previewFilterIds.clear();
  if (!state.pinnedHighlightFilterId) state.highlightFilterIds.clear();
  renderOverlay(); renderSliceStructures();
  if (focus) el.optionsMenuButton.focus({ preventScroll: true });
  requestAnimationFrame(() => { if (state.capture) fitView(); });
}

function toggleOptionsMenu() {
  if (el.optionsMenu.classList.contains("open")) closeOptionsMenu({ force: true });
  else openOptionsMenu();
}

function toggleMenuPin() {
  state.menuPinned = !state.menuPinned;
  syncVisibilityControls();
  savePreferences();
}

async function selectFromWeightingDropdown(select) {
  if (!select.value) return;
  try {
    const [seriesDirectory, variantDirectory] = JSON.parse(select.value);
    await selectVariant(seriesDirectory, variantDirectory, { resetView: true });
  } catch (error) { showError(`Series selection failed: ${error.message}`); }
}

function startCine() {
  if (state.cineTimer || !state.variant) return;
  el.cineButton.textContent = "Ⅱ"; el.cineButton.setAttribute("aria-pressed", "true");
  state.cineTimer = window.setInterval(() => {
    const next = state.slicePosition >= state.variant.slices.length - 1 ? 0 : state.slicePosition + 1;
    setSlicePosition(next);
  }, 240);
}

function stopCine() {
  if (state.cineTimer) window.clearInterval(state.cineTimer);
  state.cineTimer = null;
  if (el.cineButton) { el.cineButton.textContent = "▶"; el.cineButton.setAttribute("aria-pressed", "false"); }
}

function toggleCine() { if (state.cineTimer) stopCine(); else startCine(); }

function bindEvents() {
  // Only the website gateway provides this event; standalone viewer is unchanged.
  window.addEventListener("viewer-session-suspended", () => {
    stopCine(); cancelDrag(); touchGestures?.cancel(); hideTooltip();
    clearSliceCaches({ advanceDataRevision: false });
  });
  touchGestures = new window.ViewerTouchGestures(el.anatomyViewport, {
    ready: () => Boolean(state.capture),
    itemAt: anatomyItemAt,
    begin: () => {
      cancelDrag(); stopCine(); hideTooltip(); state.suppressDragClick = true;
      if (state.wheelFrame) cancelAnimationFrame(state.wheelFrame);
      state.wheelFrame = 0; state.wheelTargetPosition = null; state.wheelDelta = 0;
    },
    scroll: (steps) => setSlicePosition(state.slicePosition + steps, { fromWheel: true }),
    zoom: (ratio, origin, dx, dy) => { changeZoom(ratio, origin); state.panX += dx; state.panY += dy; applyTransform(); },
    tap: (item) => selectStructure(item, { toggle: true }),
    hold: (item) => selectStructure(item, { highlightOnly: true }),
  });
  el.anatomyViewport.addEventListener("contextmenu", (event) => {
    if (event.pointerType === "touch" || touchGestures.points.size) event.preventDefault();
  });
  window.addEventListener("pagehide", () => { stopCine(); clearSliceCaches({ advanceDataRevision: false }); });
  window.addEventListener("pageshow", (event) => { if (event.persisted && state.variant) showCurrentSlice(); });
  el.anatomyLanguageSelect.addEventListener("change", () => {
    state.anatomyLanguage = el.anatomyLanguageSelect.value;
    savePreferences(); loadAnatomyLanguage();
  });
  el.moduleCatalogueButton.addEventListener("click", toggleModuleCatalogue);
  el.mprToggleButton.addEventListener("click", () => setMprVisible(!state.mprVisible));
  el.mprCloseButton.addEventListener("click", () => setMprVisible(false));
  el.mprFitWidthButton.addEventListener("click", () => setMprFitWidth(!state.mprFitWidth));
  el.mprResizeHandle.addEventListener("pointerdown", startMprResize);
  el.mprResizeHandle.addEventListener("dblclick", () => { applyMprWidth(320, { persist: true }); if (state.capture) fitView(); });
  el.mprResizeHandle.addEventListener("keydown", handleMprResizeKeyboard);
  el.optionsMenuButton.addEventListener("click", toggleOptionsMenu);
  el.optionsMenuCloseButton.addEventListener("click", () => closeOptionsMenu({ force: true }));
  el.optionsMenuPinButton.addEventListener("click", toggleMenuPin);
  el.showAllAnatomyButton.addEventListener("click", () => setAllAnatomyVisible());
  el.sourceFilterDefaultsButton.addEventListener("click", () => setAllAnatomyVisible({ sourceDefaults: true }));
  el.toolbarWeightingSelect.addEventListener("change", () => selectFromWeightingDropdown(el.toolbarWeightingSelect));
  [
    [el.menuLabelsToggle, "labelsVisible"], [el.menuLeadersToggle, "leadersVisible"],
    [el.menuTargetsToggle, "targetsVisible"], [el.menuOrientationToggle, "orientationVisible"],
    [el.menuFilmstripToggle, "filmstripVisible"],
    [el.menuDetailsToggle, "detailsVisible"], [el.menuAdjustmentsToggle, "adjustmentsVisible"],
  ].forEach(([input, field]) => input.addEventListener("change", () => setVisibility(field, input.checked)));
  el.menuSelectAllFilters.addEventListener("change", () => {
    state.previewFilterIds.clear(); state.highlightFilterIds.clear(); state.pinnedHighlightFilterId = null;
    displayFilters().forEach((filter) => {
      const id = String(filter.id);
      if (el.menuSelectAllFilters.checked) state.activeFilters.add(id); else state.activeFilters.delete(id);
    });
    afterFilterChange();
  });
  el.moduleSearch.addEventListener("input", renderModuleTree);
  el.capturedFilter.addEventListener("click", () => setCapturedOnly(true));
  el.allModulesFilter.addEventListener("click", () => setCapturedOnly(false));
  el.refreshLibraryButton.addEventListener("click", () => loadCatalogue());
  el.scrollModeButton.addEventListener("click", () => setInteractionMode("scroll"));
  el.panModeButton.addEventListener("click", () => setInteractionMode("pan"));
  el.zoomModeButton.addEventListener("click", () => setInteractionMode("zoom"));
  el.zoomOutButton.addEventListener("click", () => changeZoom(1 / 1.18));
  el.zoomInButton.addEventListener("click", () => changeZoom(1.18));
  el.fitButton.addEventListener("click", fitView);
  el.labelsButton.addEventListener("click", toggleLabels);
  el.targetsButton.addEventListener("click", toggleTargets);
  el.resetImageButton.addEventListener("click", () => { state.brightness = 100; state.contrast = 100; el.brightnessSlider.value = "100"; el.contrastSlider.value = "100"; applyTransform(); savePreferences(); });
  el.brightnessSlider.addEventListener("input", () => { state.brightness = Number(el.brightnessSlider.value); applyTransform(); savePreferences(); });
  el.contrastSlider.addEventListener("input", () => { state.contrast = Number(el.contrastSlider.value); applyTransform(); savePreferences(); });
  el.previousSliceButton.addEventListener("click", () => setSlicePosition(state.slicePosition - 1));
  el.nextSliceButton.addEventListener("click", () => setSlicePosition(state.slicePosition + 1));
  el.cineButton.addEventListener("click", toggleCine);
  el.sliceSlider.addEventListener("input", () => setSlicePosition(Number(el.sliceSlider.value) - 1));
  el.helpButton.addEventListener("click", () => el.shortcutDialog.showModal());
  el.fullscreenButton.addEventListener("click", () => document.fullscreenElement ? document.exitFullscreen() : el.anatomyViewport.requestFullscreen());
  el.detailDrawerButton.addEventListener("click", toggleDetailsPanel);
  el.sliceStructuresTab.addEventListener("click", () => setStructureMode("slice"));
  el.searchStructuresTab.addEventListener("click", () => setStructureMode("search"));
  let searchTimer = null;
  el.structureSearch.addEventListener("input", () => {
    window.clearTimeout(searchTimer);
    if (state.structureMode === "slice") renderSliceStructures();
    else searchTimer = window.setTimeout(searchStructures, 180);
  });
  el.globalSearch.addEventListener("input", () => {
    const value = el.globalSearch.value;
    if (state.module && value.trim()) { el.structureSearch.value = value; setStructureMode("search"); searchStructures(); }
    else if (!value.trim()) { el.structureSearch.value = ""; setStructureMode("slice"); }
  });
  el.anatomyViewport.addEventListener("wheel", handleWheel, { passive: false });
  el.anatomyViewport.addEventListener("pointerdown", startDrag);
  window.addEventListener("pointermove", moveDrag);
  window.addEventListener("pointerup", endDrag);
  window.addEventListener("pointercancel", cancelDrag);
  window.addEventListener("blur", cancelDrag);
  el.anatomyViewport.addEventListener("lostpointercapture", cancelDrag);
  el.anatomyViewport.addEventListener("click", suppressDragActivation, true);
  el.anatomyViewport.addEventListener("dblclick", suppressDragActivation, true);
  window.addEventListener("pointermove", moveMprResize);
  window.addEventListener("pointerup", endMprResize);
  el.anatomyViewport.addEventListener("dblclick", fitView);
  window.addEventListener("resize", () => { touchGestures?.cancel(); applyMprWidth(state.mprWidth); if (state.capture) fitView(); });
  document.addEventListener("pointerdown", (event) => {
    if (!el.moduleCataloguePopover.hidden && !event.target.closest(".module-catalogue-control")) closeModuleCatalogue();
  });
  document.addEventListener("keydown", handleKeyboard);
}

function setCapturedOnly(value) {
  state.capturedOnly = value;
  el.capturedFilter.classList.toggle("active", value); el.allModulesFilter.classList.toggle("active", !value);
  el.capturedFilter.setAttribute("aria-pressed", String(value)); el.allModulesFilter.setAttribute("aria-pressed", String(!value));
  renderModuleTree(); savePreferences();
}

function handleWheel(event) {
  if (!state.variant) return;
  event.preventDefault();
  if (state.dragging) return;
  if (state.interactionMode === "zoom") {
    changeZoom(event.deltaY < 0 ? 1.12 : 1 / 1.12, event);
    return;
  }
  state.wheelDelta += event.deltaY;
  if (Math.abs(state.wheelDelta) < 35) return;
  const direction = state.wheelDelta > 0 ? 1 : -1;
  state.wheelDelta = 0;
  const total = state.variant.slices.length;
  const base = state.wheelTargetPosition ?? state.slicePosition;
  state.wheelTargetPosition = Math.max(0, Math.min(base + direction, total - 1));
  if (!state.wheelFrame) state.wheelFrame = requestAnimationFrame(() => {
    const target = state.wheelTargetPosition;
    state.wheelTargetPosition = null;
    state.wheelFrame = 0;
    setSlicePosition(target, { fromWheel: true });
  });
}

function startDrag(event) {
  if (event.pointerType === "touch") return;
  if (event.button !== 0 || event.isPrimary === false || !state.capture || state.dragStart) return;
  if (!['scroll', 'pan', 'zoom'].includes(state.interactionMode)) return;
  if (event.target.closest?.("button, input, select, textarea, a")) return;
  state.suppressDragClick = false;
  state.dragStart = {
    pointerId: event.pointerId, mode: state.interactionMode,
    x: event.clientX, y: event.clientY, lastX: event.clientX, lastY: event.clientY,
    panX: state.panX, panY: state.panY, zoom: state.zoom,
    scrollY: event.clientY, scrollPosition: state.wheelTargetPosition ?? state.slicePosition,
  };
  // Do not capture yet: a stationary click on an SVG label must still open Detail.
}

function moveDrag(event) {
  const drag = state.dragStart;
  if (!drag || event.pointerId !== drag.pointerId) return;
  if (!(event.buttons & 1)) { endDrag(event); return; }
  drag.lastX = event.clientX; drag.lastY = event.clientY;
  if (!state.dragging) {
    if (Math.hypot(drag.lastX - drag.x, drag.lastY - drag.y) < DRAG_THRESHOLD) return;
    state.dragging = true;
    state.suppressDragClick = true;
    stopCine(); hideTooltip();
    if (state.wheelFrame) cancelAnimationFrame(state.wheelFrame);
    state.wheelFrame = 0; state.wheelTargetPosition = null; state.wheelDelta = 0;
    el.anatomyViewport.classList.add("dragging");
    try { el.anatomyViewport.setPointerCapture?.(drag.pointerId); } catch { /* Pointer may already have been cancelled. */ }
  }
  event.preventDefault();
  if (!state.dragFrame) state.dragFrame = requestAnimationFrame(applyDrag);
}

function applyDrag() {
  state.dragFrame = 0;
  const drag = state.dragStart;
  if (!state.dragging || !drag) return;
  if (drag.mode === "scroll") {
    const total = state.variant?.slices.length || 0;
    const steps = Math.trunc((drag.lastY - drag.scrollY) / DRAG_SLICE_PIXELS);
    if (!total || !steps) return;
    drag.scrollY += steps * DRAG_SLICE_PIXELS;
    drag.scrollPosition = Math.max(0, Math.min(drag.scrollPosition + steps, total - 1));
    // Share the existing latest-frame/cache loader; never queue every intermediate slice.
    if (drag.scrollPosition !== state.slicePosition) setSlicePosition(drag.scrollPosition, { fromWheel: true });
  } else if (drag.mode === "pan") {
    state.panX = drag.panX + drag.lastX - drag.x;
    state.panY = drag.panY + drag.lastY - drag.y;
    applyTransform();
  } else if (drag.mode === "zoom") {
    const exponent = Math.max(-20, Math.min(20, (drag.y - drag.lastY) * DRAG_ZOOM_RATE));
    changeZoom(drag.zoom * Math.exp(exponent) / state.zoom, { clientX: drag.x, clientY: drag.y });
  }
}

function endDrag(event, { cancel = false } = {}) {
  const drag = state.dragStart;
  if (!drag || (event?.pointerId != null && event.pointerId !== drag.pointerId)) return;
  if (state.dragFrame) cancelAnimationFrame(state.dragFrame);
  state.dragFrame = 0;
  if (!cancel) applyDrag();
  state.dragging = false; state.dragStart = null;
  el.anatomyViewport.classList.remove("dragging");
  if (el.anatomyViewport.hasPointerCapture?.(drag.pointerId)) el.anatomyViewport.releasePointerCapture(drag.pointerId);
}

function cancelDrag(event) { endDrag(event, { cancel: true }); }

function suppressDragActivation(event) {
  // A following pointerdown resets this flag; keyboard/programmatic clicks remain usable.
  if (!state.suppressDragClick || event.detail === 0) return;
  event.preventDefault(); event.stopImmediatePropagation();
}

function handleKeyboard(event) {
  if (window.viewerSession?.blocked) return;
  const typing = ["INPUT", "TEXTAREA"].includes(document.activeElement?.tagName);
  if (typing && event.key !== "Escape") return;
  if (event.key === "ArrowLeft" || event.key === "ArrowUp") { event.preventDefault(); setSlicePosition(state.slicePosition - 1); }
  if (event.key === "ArrowRight" || event.key === "ArrowDown") { event.preventDefault(); setSlicePosition(state.slicePosition + 1); }
  if (event.key.toLowerCase() === "l") toggleLabels();
  if (event.key.toLowerCase() === "z") setInteractionMode("zoom");
  if (event.key.toLowerCase() === "p") setInteractionMode("pan");
  if (event.key === "0") fitView();
  if (event.key === "+" || event.key === "=") changeZoom(1.18);
  if (event.key === "-") changeZoom(1 / 1.18);
  if (event.key === " ") { event.preventDefault(); toggleCine(); }
  if (event.key.toLowerCase() === "f") el.fullscreenButton.click();
  if (event.key.toLowerCase() === "m") toggleOptionsMenu();
  if (event.key === "/") { event.preventDefault(); el.globalSearch.focus(); }
  if (event.key === "Escape") { stopCine(); closeOptionsMenu({ force: true }); closeModuleCatalogue(); if (state.definitionPeek) closeDefinition(); else if (state.detailsVisible) setVisibility("detailsVisible", false); hideTooltip(); }
}

async function initialize() {
  cacheElements();
  const saved = loadPreferences();
  // Apply before asynchronous fetches, including when desktop preferences were saved.
  applyMobileDefaults();
  syncVisibilityControls();
  el.app.classList.add("layout-ready");
  if (window.viewerSession && !(await window.viewerSession.ready)) return;
  await initializeAnatomyLanguages();
  el.overlayOpacitySlider.value = String(state.overlayOpacity);
  el.overlayOpacityNumber.value = String(state.overlayOpacity);
  bindEvents();
  state.labelsVisible = saved.labelsVisible !== false;
  state.leadersVisible = saved.leadersVisible !== false;
  state.targetsVisible = saved.targetsVisible !== false;
  state.orientationVisible = saved.orientationVisible !== false;
  state.filmstripVisible = saved.filmstripVisible !== false;
  state.mprVisible = saved.mprVisible === true;
  state.mprWidth = Number(saved.mprWidth) || 320;
  state.mprFitWidth = saved.mprFitWidth === true;
  state.detailsVisible = saved.detailsVisible !== false;
  state.adjustmentsVisible = saved.adjustmentsVisible !== false;
  state.menuPinned = saved.menuPinned === true;
  applyMobileDefaults();
  state.brightness = Number(saved.brightness) || 100;
  state.contrast = Number(saved.contrast) || 100;
  el.brightnessSlider.value = String(state.brightness); el.contrastSlider.value = String(state.contrast);
  syncVisibilityControls();
  applyMprWidth(state.mprWidth);
  setMprFitWidth(state.mprFitWidth);
  setMprVisible(state.mprVisible);
  setCapturedOnly(saved.capturedOnly !== false);
  setInteractionMode("scroll");
  try {
    await loadCatalogue({ restore: true });
    if (!isMobileViewer()) openOptionsMenu({ focus: false });
    if (!state.module) { el.loadingState.hidden = true; el.emptyState.hidden = false; el.app.setAttribute("aria-busy", "false"); }
  } catch (error) { showError(error.message); }
}

initialize();
