"use strict";

(() => {
  const root = document.documentElement;
  const storageKey = "radiology-atlas-theme";
  const darkPreference = window.matchMedia("(prefers-color-scheme: dark)");
  let storedTheme = null;

  try {
    const value = localStorage.getItem(storageKey);
    if (value === "light" || value === "dark") storedTheme = value;
  } catch (_) {
    // Storage may be disabled; the operating-system preference remains usable.
  }

  function resolvedTheme() {
    return storedTheme || (darkPreference.matches ? "dark" : "light");
  }

  function paintTheme(theme) {
    root.dataset.theme = theme;
    root.style.colorScheme = theme;
    const color = theme === "dark" ? "#17131f" : "#63508d";
    document.querySelector('meta[name="theme-color"]')?.setAttribute("content", color);
    document.querySelectorAll("[data-theme-toggle]").forEach(button => {
      const next = theme === "dark" ? "sáng" : "tối";
      button.setAttribute("aria-label", `Bật giao diện ${next}`);
      button.setAttribute("title", `Bật giao diện ${next}`);
      button.setAttribute("aria-pressed", String(theme === "dark"));
      const text = button.querySelector("[data-theme-label]");
      if (text) text.textContent = theme === "dark" ? "Giao diện sáng" : "Giao diện tối";
    });
  }

  paintTheme(resolvedTheme());

  function setNavigation(button, navigation, open) {
    navigation.classList.toggle("is-open", open);
    button.setAttribute("aria-expanded", String(open));
    const icon = button.querySelector("[data-nav-icon]");
    if (icon) icon.textContent = open ? "×" : "☰";
  }

  function activateControls() {
    paintTheme(resolvedTheme());
    document.querySelectorAll("[data-theme-toggle]").forEach(button => {
      button.addEventListener("click", () => {
        storedTheme = resolvedTheme() === "dark" ? "light" : "dark";
        try { localStorage.setItem(storageKey, storedTheme); } catch (_) {}
        paintTheme(storedTheme);
      });
    });

    const navigationPairs = [...document.querySelectorAll("[data-nav-toggle]")].map(button => ({
      button,
      navigation: document.getElementById(button.getAttribute("aria-controls"))
    })).filter(pair => pair.navigation);

    navigationPairs.forEach(({ button, navigation }) => {
      button.addEventListener("click", event => {
        event.stopPropagation();
        setNavigation(button, navigation, button.getAttribute("aria-expanded") !== "true");
      });
      navigation.addEventListener("click", event => {
        if (event.target.closest("a,button[type='submit']")) setNavigation(button, navigation, false);
      });
    });
    document.addEventListener("click", event => navigationPairs.forEach(({ button, navigation }) => {
      if (!navigation.contains(event.target) && !button.contains(event.target)) setNavigation(button, navigation, false);
    }));
    document.addEventListener("keydown", event => {
      if (event.key !== "Escape") return;
      navigationPairs.forEach(({ button, navigation }) => {
        if (button.getAttribute("aria-expanded") === "true") {
          setNavigation(button, navigation, false);
          button.focus();
        }
      });
    });
    window.matchMedia("(min-width: 721px)").addEventListener?.("change", event => {
      if (event.matches) navigationPairs.forEach(({ button, navigation }) => setNavigation(button, navigation, false));
    });
  }

  darkPreference.addEventListener?.("change", () => {
    if (!storedTheme) paintTheme(resolvedTheme());
  });
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", activateControls, { once: true });
  else activateControls();
})();
