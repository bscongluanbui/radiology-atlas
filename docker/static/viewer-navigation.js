"use strict";

// Use browser history only when the viewer was reached from this website.
// A direct link/new tab or a login/external referrer returns to Anatomy instead.
(() => {
  const back = document.getElementById("viewerBackLink");
  if (!back) return;
  const websitePages = new Set(["/", "/index.html", "/anatomy", "/account", "/admin"]);
  back.addEventListener("click", (event) => {
    if (event.defaultPrevented || event.button !== 0 || event.ctrlKey || event.metaKey || event.shiftKey || event.altKey) return;
    let previous;
    try { previous = new URL(document.referrer); } catch { return; }
    if (previous.origin !== location.origin || !websitePages.has(previous.pathname) || history.length <= 1) return;
    event.preventDefault();
    history.back();
  });
})();
