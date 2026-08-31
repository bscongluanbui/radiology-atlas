"use strict";
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const assert = require("node:assert/strict");
const root = path.resolve(process.argv[2] || path.join(__dirname, "../.."));
const code = fs.readFileSync(path.join(root, "docker/static/viewer-navigation.js"), "utf8");
function click(referrer, length = 3, extra = {}, present = true) {
  let handler, calls = 0, prevented = false;
  const context = { URL, document: { referrer, getElementById: () => present ? {addEventListener: (name, callback) => { assert.equal(name,"click"); handler=callback; }} : null },
    location: {origin:"https://atlas.test"}, history:{length, back:() => calls++} };
  vm.runInNewContext(code, context);
  if (handler) handler({button:0,defaultPrevented:false,preventDefault:()=>prevented=true,...extra});
  return {calls,prevented};
}
for (const url of ["/", "/#guide", "/index.html", "/anatomy?region=BRAIN", "/account", "/admin"]) {
  assert.deepEqual(click("https://atlas.test"+url),{calls:1,prevented:true});
}
for (const referrer of ["", "not a url", "https://outside.test/anatomy", "http://atlas.test/anatomy", "https://atlas.test/login", "https://atlas.test/viewer?key=BRAIN/mri-brain", "https://atlas.test/unknown"]) {
  assert.deepEqual(click(referrer),{calls:0,prevented:false});
}
assert.deepEqual(click("https://atlas.test/anatomy",1),{calls:0,prevented:false});
for (const extra of [{ctrlKey:true},{metaKey:true},{shiftKey:true},{altKey:true},{button:1},{defaultPrevented:true}]) {
  assert.deepEqual(click("https://atlas.test/anatomy",3,extra),{calls:0,prevented:false});
}
assert.deepEqual(click("",1,{},false),{calls:0,prevented:false});
const html=fs.readFileSync(path.join(root,"docker/templates/viewer_navigation.html"),"utf8");
assert.match(html,/id="viewerHomeLink"[^>]*href="\/"/);
assert.match(html,/id="viewerBackLink"[^>]*href="\/anatomy"/);
assert.match(html,/aria-label="Home/);assert.match(html,/aria-label="Back/);
assert.doesNotMatch(html,/onclick=|javascript:/);
const css=fs.readFileSync(path.join(root,"docker/static/viewer-session.css"),"utf8");
assert.match(css,/@media\(max-width:720px\)/);assert.match(css,/:focus-visible/);
console.log("NAVIGATION_DOM=PASS; Home,Back,same_origin_history,direct_link,new_tab,login_external_fallback,modified_click,keyboard_anchor,offline_noop,responsive_rules");
