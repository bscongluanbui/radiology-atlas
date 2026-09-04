"use strict";
const fs = require("node:fs"), path = require("node:path"), vm = require("node:vm"), assert = require("node:assert/strict");
const root = path.resolve(process.argv[2] || path.join(__dirname, "../.."));
const gestures = fs.readFileSync(path.join(root, "offline_anatomy_viewer/mobile_gestures.js"), "utf8");
let count = 0;
function test(name, fn) { fn(); count++; console.log(`PASS ${name}`); }
function setup() {
  let next = 1; const timers = new Map(), frames = new Map(), listeners = {}, captured = new Set(), actions = [];
  const viewport = { addEventListener(n, fn) { listeners[n] = fn; }, setPointerCapture(id) { captured.add(id); },
    hasPointerCapture: id => captured.has(id), releasePointerCapture(id) { captured.delete(id); } };
  const host = { addEventListener(n, fn) { listeners[n] = fn; },
    setTimeout(fn, ms) { const id = next++; timers.set(id, { fn, ms }); return id; }, clearTimeout(id) { timers.delete(id); },
    requestAnimationFrame(fn) { const id = next++; frames.set(id, fn); return id; }, cancelAnimationFrame(id) { frames.delete(id); } };
  const ctx = vm.createContext({ window: host }); vm.runInContext(gestures, ctx);
  const controller = new host.ViewerTouchGestures(viewport, {
    ready: () => true, begin: () => actions.push(["begin"]), itemAt: node => node.item,
    scroll: value => actions.push(["scroll", value]), zoom: (...args) => actions.push(["zoom", ...args]),
    tap: item => actions.push(["tap", item]), hold: item => actions.push(["hold", item]),
  }, host);
  const event = (id, x, y, more = {}) => ({ pointerId: id, pointerType: "touch", clientX: x, clientY: y,
    target: { closest: () => null }, preventDefault() {}, ...more });
  return { controller, actions, timers, frames, captured, event, listeners,
    down: (id, x, y, more) => controller.down(event(id, x, y, more)),
    move: (id, x, y) => controller.move(event(id, x, y)), up: (id, x, y) => controller.up(event(id, x, y)),
    raf() { const all = [...frames.values()]; frames.clear(); all.forEach(fn => fn()); },
    hold() { const all = [...timers.values()]; timers.clear(); all.forEach(({ fn }) => fn()); },
    of: name => actions.filter(a => a[0] === name),
  };
}
test("one-finger scroll uses frame coalescing and supports both directions", () => {
  const h = setup(); h.down(1, 100, 100); h.move(1, 100, 130); h.move(1, 100, 160);
  assert.equal(h.frames.size, 1); h.raf(); assert.equal(h.of("scroll")[0][1], 5);
  h.move(1, 100, 112); h.raf(); assert.equal(h.of("scroll")[1][1], -4); h.up(1, 100, 112);
  assert.equal(h.captured.size, 0); assert.equal(h.controller.session, null);
});
test("stationary label tap occurs once and no hold fires", () => {
  const h = setup(), item = { key: "taxon:1:11" }; h.down(1, 10, 10, { target: { item } }); h.up(1, 11, 10); h.hold();
  assert.equal(h.of("tap").length, 1); assert.equal(h.of("tap")[0][1], item); assert.equal(h.of("hold").length, 0);
});
test("450ms long press highlights only and release never opens annotation", () => {
  const h = setup(), item = { key: "taxon:1:12" }; h.down(1, 10, 10, { target: { item } });
  assert.equal([...h.timers.values()][0].ms, 450); h.hold(); h.move(1, 12, 12); h.up(1, 12, 12);
  assert.equal(h.of("hold").length, 1); assert.equal(h.of("tap").length, 0); assert.equal(h.of("scroll").length, 0);
  h.down(2, 10, 10, { target: { item } }); h.up(2, 10, 10); assert.equal(h.of("tap").length, 1);
});
test("moving label cancels long press; no annotation after swipe", () => {
  const h = setup(); h.down(1, 10, 10, { target: { item: {} } }); h.move(1, 10, 60); h.hold(); h.up(1, 10, 60);
  assert.equal(h.of("hold").length, 0); assert.equal(h.of("tap").length, 0); assert.equal(h.of("scroll").length, 1);
});
test("pinch accepts non-primary pointer, zooms in and out with centroid", () => {
  const h = setup(); h.down(1, 0, 100); h.down(2, 100, 100, { isPrimary: false });
  h.move(2, 200, 100); h.raf(); assert.equal(h.of("zoom")[0][1], 2);
  assert.equal(h.of("zoom")[0][2].clientX, 50); assert.equal(h.of("zoom")[0][3], 50);
  h.move(2, 50, 100); h.raf(); assert.equal(h.of("zoom")[1][1], 0.25);
  h.up(2, 50, 100); h.move(1, 0, 200); h.raf(); h.up(1, 0, 200);
  assert.equal(h.of("scroll").length, 0); assert.equal(h.of("tap").length, 0);
});
test("second finger cancels pending scroll and label hold", () => {
  const h = setup(); h.down(1, 0, 0, { target: { item: {} } }); h.move(1, 0, 24);
  h.down(2, 100, 0); h.raf(); h.hold(); assert.equal(h.of("scroll").length, 0); assert.equal(h.of("hold").length, 0);
});
test("cancel, blur, pagehide and lost capture release all resources", () => {
  for (const name of ["pointercancel", "blur", "pagehide", "lostpointercapture"]) {
    const h = setup(); h.down(1, 0, 0, { target: { item: {} } }); h.move(1, 0, 1);
    h.listeners[name](h.event(1, 0, 1)); h.hold(); h.raf();
    assert.equal(h.controller.points.size, 0); assert.equal(h.captured.size, 0);
    assert.equal(h.timers.size, 0); assert.equal(h.frames.size, 0); assert.equal(h.of("tap").length, 0);
  }
});
test("mouse and native controls are not intercepted", () => {
  const h = setup(); h.down(1, 0, 0, { pointerType: "mouse" }); h.down(2, 0, 0, { target: { closest: () => ({}) } });
  assert.equal(h.actions.length, 0);
});
test("three-finger and zero-distance transitions remain finite", () => {
  const h = setup(); h.down(1, 0, 0); h.down(2, 0, 0); h.move(2, 100, 0); h.raf();
  h.down(3, 150, 0); h.move(3, 170, 0); h.raf(); h.up(1, 0, 0); h.move(3, 200, 0); h.raf();
  assert(h.of("zoom").every(a => Number.isFinite(a[1]))); assert.equal(h.of("scroll").length, 0);
});

const appSource = fs.readFileSync(path.join(root, "offline_anatomy_viewer/app.js"), "utf8").replace(/\ninitialize\(\);\s*$/, "\n");
function appContext(mobile) {
  const ctx = vm.createContext({ window: { matchMedia: () => ({ matches: mobile }), navigator: {}, ViewerRequestQueue: class {} },
    document: {}, requestAnimationFrame() {}, console });
  vm.runInContext(appSource, ctx);
  vm.runInContext(`hideTooltip=()=>{};renderOverlay=()=>{};renderSliceStructures=()=>{};renderSearchRows=()=>{};
    syncDetailPanel=()=>{};renderFilters=()=>{};renderDefinition=()=>{definitionCalls++};
    let definitionCalls=0;el.definitionPanel={innerHTML:''};
    closeOptionsMenu=()=>{menuClosed=true};closeModuleCatalogue=()=>{catalogueClosed=true};
    let menuClosed=false,catalogueClosed=false; el.optionsMenu={querySelectorAll:()=>[]};`, ctx);
  return code => vm.runInContext(code, ctx);
}
test("mobile startup ignores saved open menus but retains labels; desktop unchanged", () => {
  for (const mobile of [true, false]) {
    const run = appContext(mobile); run("state.mprVisible=true;state.menuPinned=true;applyMobileDefaults()");
    for (const field of ["detailsVisible", "mprVisible", "filmstripVisible", "adjustmentsVisible", "menuPinned"]) assert.equal(run(`state.${field}`), !mobile);
    assert.equal(run("state.labelsVisible"), true); assert.equal(run("menuClosed && catalogueClosed"), mobile);
  }
});
test("long press then tap same identity opens Detail; ordinary second tap toggles off", () => {
  const run = appContext(true); run("const item={key:'taxon:1:11'};selectStructure(item,{highlightOnly:true})");
  assert.equal(run("definitionCalls"), 0); assert.equal(run("detailsPanelVisible()"), false);
  assert.equal(run("annotationInteractionClass(['taxon:1:11'],[],'taxon:1:11')"), " is-selected");
  assert.equal(run("annotationInteractionClass(['taxon:1:12'],[],'taxon:1:11')"), " is-selection-muted");
  run("selectStructure(item,{toggle:true})"); assert.equal(run("definitionCalls"), 1); assert.equal(run("detailsPanelVisible()"), true);
  run("selectStructure(item,{toggle:true})"); assert.equal(run("state.selectedStructure"), null);
});
test("touch hover never previews hidden filter; checkbox clears stale preview", () => {
  const run = appContext(true);
  run("let previews=0;setPreviewFilter=()=>previews++;const handlers={};const node={dataset:{},addEventListener:(n,f)=>handlers[n]=f};bindFilterPreview(node,{id:'7'});handlers.pointerenter({pointerType:'touch'})");
  assert.equal(run("previews"), 0); run("handlers.pointerenter({pointerType:'mouse'})"); assert.equal(run("previews"), 1);
  run("state.activeFilters.add('7');state.previewFilterIds.add('7');toggleFilter('7',false)");
  assert.equal(run("filterEnabled('7')"), false);
});
test("packaging and CI load the touch controller", () => {
  const html = fs.readFileSync(path.join(root, "offline_anatomy_viewer/index.html"), "utf8");
  assert(html.indexOf('./mobile_gestures.js') < html.indexOf('./app.js'));
  assert.match(fs.readFileSync(path.join(root, "docker/portal.py"), "utf8"), /"mobile_gestures.js"/);
  assert.match(fs.readFileSync(path.join(root, ".github/workflows/publish-viewer.yml"), "utf8"), /viewer_navigation mobile_gestures/);
  assert.match(fs.readFileSync(path.join(root, "offline_anatomy_viewer/styles.css"), "utf8"), /min-height: 44px; touch-action: manipulation/);
});
console.log(`MOBILE_GESTURES=PASS; tests=${count}; scroll,pinch,hold,tap,cancel,mobile_defaults,filters,desktop,packaging`);
