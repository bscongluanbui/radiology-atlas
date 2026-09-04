"use strict";

(() => {
  const sidebar = document.getElementById("adminSidebar");
  const sidebarToggle = document.querySelector("[data-sidebar-toggle]");

  function setSidebar(open) {
    if (!sidebar || !sidebarToggle) return;
    sidebar.classList.toggle("is-open", open);
    sidebarToggle.setAttribute("aria-expanded", String(open));
  }

  sidebarToggle?.addEventListener("click", () => {
    setSidebar(sidebarToggle.getAttribute("aria-expanded") !== "true");
  });
  sidebar?.addEventListener("click", event => {
    if (event.target.closest("a") && window.matchMedia("(max-width: 900px)").matches) setSidebar(false);
  });

  document.addEventListener("click", event => {
    const button = event.target.closest("[data-drawer-target]");
    if (!button) return;
    const row = document.getElementById(button.dataset.drawerTarget);
    if (!row) return;
    const open = row.classList.toggle("is-open");
    document.querySelectorAll(`[data-drawer-target="${CSS.escape(row.id)}"]`).forEach(control => {
      control.setAttribute("aria-expanded", String(open));
    });
  });

  document.addEventListener("keydown", event => {
    if (event.key !== "Escape") return;
    setSidebar(false);
    document.querySelectorAll(".drawer-row.is-open").forEach(row => {
      row.classList.remove("is-open");
      document.querySelectorAll(`[data-drawer-target="${CSS.escape(row.id)}"]`).forEach(control => {
        control.setAttribute("aria-expanded", "false");
      });
    });
  });
})();
