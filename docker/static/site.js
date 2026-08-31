"use strict";

// Source modality values are exact: MRI includes MRA/MRV only when the source
// catalogue itself classifies that module as MRI. No title-based inference.
function normalizeCatalogueText(value) {
  return String(value || "").normalize("NFKD").replace(/[\u0300-\u036f]/g, "").replace(/đ/gi, "d").toLowerCase().trim();
}
function catalogueMatches(row, filters) {
  return (!filters.regions.size || filters.regions.has(row.region))
    && (!filters.modalities.size || filters.modalities.has(row.modality))
    && (!filters.readyOnly || row.ready === "true")
    && (!filters.query || normalizeCatalogueText(`${row.title} ${row.region} ${row.modality}`).includes(filters.query));
}
if (typeof module !== "undefined" && module.exports) module.exports = { normalizeCatalogueText, catalogueMatches };

if (typeof document !== "undefined") (() => {
  const dialog = document.getElementById("loginDialog");
  const authenticated = document.body.dataset.authenticated === "true";
  let previousFocus = null;
  function openLogin(next = "/anatomy") {
    if (!dialog) return;
    previousFocus = document.activeElement;
    dialog.querySelector('input[name="next"]').value = next;
    const error = document.getElementById("loginError");
    error.hidden = true; error.textContent = "";
    dialog.showModal();
    document.getElementById("siteUsername").focus();
  }
  document.querySelectorAll("[data-login-trigger], [data-anatomy-link]").forEach(link => {
    link.addEventListener("click", event => {
      if (authenticated || event.ctrlKey || event.metaKey || event.shiftKey || event.altKey || event.button !== 0) return;
      event.preventDefault();
      openLogin(link.dataset.next || link.getAttribute("href") || "/anatomy");
    });
  });
  if (dialog) {
    dialog.querySelector("[data-close-login]").addEventListener("click", () => dialog.close());
    dialog.addEventListener("close", () => previousFocus?.focus());
    dialog.addEventListener("click", event => {
      if (event.target !== dialog) return;
      const rect = dialog.getBoundingClientRect();
      if (event.clientX < rect.left || event.clientX > rect.right || event.clientY < rect.top || event.clientY > rect.bottom) dialog.close();
    });
    const form = document.getElementById("siteLoginForm");
    form.addEventListener("submit", async event => {
      event.preventDefault();
      const button = form.querySelector('button[type="submit"]');
      const error = document.getElementById("loginError");
      button.disabled = true; button.setAttribute("aria-busy", "true"); error.hidden = true;
      try {
        const response = await fetch("/login", { method: "POST", body: new FormData(form), headers: { Accept: "application/json" }, credentials: "same-origin", cache: "no-store" });
        const body = await response.json();
        if (!response.ok) throw new Error(body.error || "Đăng nhập chưa thành công. Vui lòng thử lại.");
        const target = new URL(body.redirect || "/anatomy", location.origin);
        if (target.origin !== location.origin) throw new Error("Đường dẫn đăng nhập không hợp lệ.");
        location.assign(target.pathname + target.search);
      } catch (failure) {
        error.textContent = failure.message || "Kết nối gián đoạn. Vui lòng thử lại.";
        error.hidden = false;
      } finally { button.disabled = false; button.removeAttribute("aria-busy"); }
    });
    if (dialog.dataset.openOnLoad === "true") openLogin(dialog.querySelector('input[name="next"]').value);
  }

  const catalogue = document.querySelector("[data-catalogue]");
  if (!catalogue) return;
  const search = document.getElementById("catalogueSearch");
  const readyOnly = document.getElementById("readyOnly");
  const regionBoxes = [...catalogue.querySelectorAll('input[name="region"]')];
  const modalityBoxes = [...catalogue.querySelectorAll('input[name="modality"]')];
  const cards = [...catalogue.querySelectorAll("[data-module-card]")];
  const groups = [...catalogue.querySelectorAll("[data-group]")];
  const menus = [...catalogue.querySelectorAll(".filter-menu")];
  const params = new URLSearchParams(location.search);
  search.value = params.get("q") || "";
  readyOnly.checked = params.get("available") === "1";
  regionBoxes.forEach(box => { box.checked = params.getAll("region").includes(box.value); });
  modalityBoxes.forEach(box => { box.checked = params.getAll("modality").includes(box.value); });

  function applyFilters() {
    const regions = new Set(regionBoxes.filter(box => box.checked).map(box => box.value));
    const modalities = new Set(modalityBoxes.filter(box => box.checked).map(box => box.value));
    const filters = { regions, modalities, query: normalizeCatalogueText(search.value), readyOnly: readyOnly.checked };
    let visible = 0, ready = 0, visibleGroups = 0;
    cards.forEach(card => {
      card.hidden = !catalogueMatches(card.dataset, filters);
      if (!card.hidden) { visible++; if (card.dataset.ready === "true") ready++; }
    });
    groups.forEach(group => {
      const count = [...group.querySelectorAll("[data-module-card]")].filter(card => !card.hidden).length;
      group.hidden = count === 0;
      group.querySelector("[data-group-count]").textContent = `${count} module`;
      if (count) visibleGroups++;
    });
    document.getElementById("catalogueResult").textContent = `${visible} / ${cards.length} module · ${ready} sẵn sàng · ${visibleGroups} vùng`;
    document.getElementById("catalogueEmpty").hidden = visible > 0;
    [["region", regions], ["modality", modalities]].forEach(([name, values]) => {
      const badge = catalogue.querySelector(`[data-${name}-count]`);
      badge.hidden = !values.size; badge.textContent = values.size;
    });
    const active = Boolean(regions.size || modalities.size || filters.query || filters.readyOnly);
    catalogue.querySelector(".catalogue-status [data-clear-all]").hidden = !active;
    catalogue.querySelectorAll("[data-region-toggle]").forEach(button => button.setAttribute("aria-pressed", String(regions.has(button.dataset.regionToggle))));
    const query = new URLSearchParams();
    if (search.value.trim()) query.set("q", search.value.trim());
    if (readyOnly.checked) query.set("available", "1");
    regions.forEach(value => query.append("region", value));
    modalities.forEach(value => query.append("modality", value));
    history.replaceState(null, "", location.pathname + (query.size ? "?" + query.toString() : ""));
  }
  search.addEventListener("input", applyFilters);
  readyOnly.addEventListener("change", applyFilters);
  [...regionBoxes, ...modalityBoxes].forEach(box => box.addEventListener("change", applyFilters));
  catalogue.querySelectorAll("[data-clear]").forEach(button => button.addEventListener("click", () => {
    (button.dataset.clear === "region" ? regionBoxes : modalityBoxes).forEach(box => { box.checked = false; }); applyFilters();
  }));
  catalogue.querySelectorAll("[data-clear-all]").forEach(button => button.addEventListener("click", () => {
    [...regionBoxes, ...modalityBoxes].forEach(box => { box.checked = false; });
    readyOnly.checked = false; search.value = ""; applyFilters();
  }));
  catalogue.querySelectorAll("[data-region-toggle]").forEach(button => button.addEventListener("click", () => {
    const box = regionBoxes.find(box => box.value === button.dataset.regionToggle);
    if (box) { box.checked = !box.checked; applyFilters(); }
  }));
  document.addEventListener("click", event => menus.forEach(menu => { if (!menu.contains(event.target)) menu.open = false; }));
  document.addEventListener("keydown", event => { if (event.key === "Escape") menus.forEach(menu => { if (menu.open) { menu.open = false; menu.querySelector("summary").focus(); } }); });
  cards.forEach(card => {
    const image = card.querySelector("img");
    if (image) image.addEventListener("error", () => { image.hidden = true; card.querySelector(".module-fallback").hidden = false; }, { once: true });
  });
  applyFilters();
})();
