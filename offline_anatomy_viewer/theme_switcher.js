(function() {
  const STORAGE_KEY = "radiology-viewer-theme";
  const DEFAULT_THEME = "cyan";

  function getUrlTheme() {
    try {
      const params = new URLSearchParams(window.location.search);
      const t = params.get("theme");
      if (t && ["cyan", "violet", "amber", "nordic", "default"].includes(t)) {
        return t;
      }
    } catch (_) {}
    return null;
  }

  function applyTheme(theme, updateUrl = true) {
    if (!theme || theme === "default") {
      document.documentElement.removeAttribute("data-viewer-theme");
      document.body.removeAttribute("data-viewer-theme");
      const app = document.getElementById("app");
      if (app) app.removeAttribute("data-viewer-theme");
    } else {
      document.documentElement.setAttribute("data-viewer-theme", theme);
      document.body.setAttribute("data-viewer-theme", theme);
      const app = document.getElementById("app");
      if (app) app.setAttribute("data-viewer-theme", theme);
    }

    try {
      localStorage.setItem(STORAGE_KEY, theme || "default");
    } catch (_) {}

    if (updateUrl && window.history && window.history.replaceState) {
      const url = new URL(window.location);
      if (theme && theme !== "default") {
        url.searchParams.set("theme", theme);
      } else {
        url.searchParams.delete("theme");
      }
      window.history.replaceState({}, "", url);
    }

    const themeSelect = document.getElementById("themeSelect");
    if (themeSelect) {
      themeSelect.value = theme || "default";
    }

    updateModuleButtonMeta();
  }

  function updateModuleButtonMeta() {
    const thumbImg = document.getElementById("studyThumbImg");
    const svgGlyph = document.getElementById("studySvgGlyph");
    const seriesBadge = document.getElementById("studySeriesBadge");
    const currentTheme = document.documentElement.getAttribute("data-viewer-theme") || "default";

    const activeBtn = document.querySelector("#moduleTree .module-button.active");
    const activeImg = activeBtn?.querySelector("img.module-thumbnail");
    const metaText = activeBtn?.querySelector(".module-copy small")?.textContent || "";

    let seriesCount = "";
    const match = metaText.match(/(\d+)\s+series/i);
    if (match) {
      seriesCount = match[1];
    } else {
      const selectOptions = Array.from(document.querySelectorAll("#toolbarWeightingSelect option"));
      const validOptions = selectOptions.filter(opt => opt.value && !opt.textContent.includes("No series"));
      if (validOptions.length > 0) {
        seriesCount = String(validOptions.length);
      }
    }

    if (seriesBadge) {
      if (seriesCount) {
        if (currentTheme === "amber") {
          seriesBadge.textContent = `[${seriesCount}-SER]`;
        } else if (currentTheme === "nordic") {
          seriesBadge.textContent = `\u00b7 ${seriesCount} series`;
        } else {
          seriesBadge.textContent = `${seriesCount} series`;
        }
        seriesBadge.style.display = "inline-flex";
      } else {
        seriesBadge.textContent = "";
        seriesBadge.style.display = "none";
      }
    }

    if (thumbImg && svgGlyph) {
      const preferThumb = (currentTheme === "cyan" || currentTheme === "amber" || currentTheme === "default");
      if (preferThumb && activeImg && activeImg.src) {
        thumbImg.src = activeImg.src;
        thumbImg.style.display = "block";
        svgGlyph.style.display = "none";
      } else {
        thumbImg.style.display = "none";
        svgGlyph.style.display = "block";
      }
    }
  }

  function shortenLanguageOptions() {
    const langSelect = document.getElementById("anatomyLanguageSelect");
    if (!langSelect) return;
    Array.from(langSelect.options).forEach(opt => {
      if (opt.value === "en" && opt.textContent !== "En") opt.textContent = "En";
      if (opt.value === "vi" && opt.textContent !== "Vi") opt.textContent = "Vi";
      if (opt.value === "en-vi" && opt.textContent !== "Dual") opt.textContent = "Dual";
    });
  }

  function initSwitcher() {
    let currentTheme = getUrlTheme();
    if (!currentTheme) {
      try {
        currentTheme = localStorage.getItem(STORAGE_KEY) || DEFAULT_THEME;
      } catch (_) {
        currentTheme = DEFAULT_THEME;
      }
    }

    applyTheme(currentTheme, false);

    // Theme dropdown listener
    const themeSelect = document.getElementById("themeSelect");
    if (themeSelect) {
      themeSelect.value = currentTheme;
      themeSelect.addEventListener("change", () => {
        applyTheme(themeSelect.value, true);
      });
    }

    // Reload button
    const reloadBtn = document.getElementById("reloadThemeButton");
    if (reloadBtn) {
      reloadBtn.addEventListener("click", () => {
        window.location.reload();
      });
    }

    // Toolbar Hide/Show Toggle
    const toggleToolbarBtn = document.getElementById("toggleViewerToolbarBtn");
    const toolbarToggleLabel = document.getElementById("toolbarToggleLabel");
    let toolbarVisible = true;

    if (toggleToolbarBtn) {
      toggleToolbarBtn.addEventListener("click", () => {
        toolbarVisible = !toolbarVisible;
        const app = document.getElementById("app");
        if (app) {
          app.classList.toggle("hide-viewer-toolbar", !toolbarVisible);
        }
        toggleToolbarBtn.setAttribute("aria-pressed", String(toolbarVisible));
        toggleToolbarBtn.classList.toggle("active", toolbarVisible);
        if (toolbarToggleLabel) {
          toolbarToggleLabel.textContent = toolbarVisible ? "Tools: Show" : "Tools: Hide";
        }
        toggleToolbarBtn.title = toolbarVisible ? "Báº¥m Ä‘á»ƒ áº©n thanh cÃ´ng cá»¥" : "Báº¥m Ä‘á»ƒ hiá»‡n thanh cÃ´ng cá»¥";
        // Recenter and fit anatomy image to the newly sized viewport
        window.setTimeout(() => {
          const fitBtn = document.getElementById("fitButton");
          if (fitBtn) {
            fitBtn.click();
          } else {
            window.dispatchEvent(new Event("resize"));
          }
        }, 60);
      });
    }

    // Shorten Language Select options to En / Vi / Dual
    const langSelect = document.getElementById("anatomyLanguageSelect");
    if (langSelect) {
      shortenLanguageOptions();
      const observer = new MutationObserver(shortenLanguageOptions);
      observer.observe(langSelect, { childList: true });
    }

    // Observers to dynamically update Module Button metadata (thumbnail, series count)
    const studyTitle = document.getElementById("studyTitle");
    if (studyTitle) {
      const titleObserver = new MutationObserver(updateModuleButtonMeta);
      titleObserver.observe(studyTitle, { characterData: true, childList: true, subtree: true });
    }

    const moduleTree = document.getElementById("moduleTree");
    if (moduleTree) {
      const treeObserver = new MutationObserver(updateModuleButtonMeta);
      treeObserver.observe(moduleTree, { childList: true, subtree: true });
    }

    const weightingSelect = document.getElementById("toolbarWeightingSelect");
    if (weightingSelect) {
      const selectObserver = new MutationObserver(updateModuleButtonMeta);
      selectObserver.observe(weightingSelect, { childList: true });
    }

    updateModuleButtonMeta();
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", initSwitcher);
  } else {
    initSwitcher();
  }
})();

