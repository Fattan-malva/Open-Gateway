/* Tema terang / gelap. Default mengikuti sistem, pilihan pengguna disimpan. */
(() => {
  "use strict";

  const KEY = "open-gateway-theme";
  const LEGACY_KEY = "open-masking-theme";
  const root = document.documentElement;
  const media = window.matchMedia("(prefers-color-scheme: dark)");

  function readSaved() {
    try {
      const value = localStorage.getItem(KEY);
      if (value === "light" || value === "dark") return value;
      const legacy = localStorage.getItem(LEGACY_KEY);
      return legacy === "light" || legacy === "dark" ? legacy : null;
    } catch {
      return null;
    }
  }

  function resolve() {
    return readSaved() || (media.matches ? "dark" : "light");
  }

  function paintToggle(theme) {
    const button = document.getElementById("theme-toggle");
    if (!button) return;
    const next = theme === "dark" ? "light" : "dark";
    const label = next === "dark" ? "Aktifkan tema gelap" : "Aktifkan tema terang";
    button.setAttribute("aria-label", label);
    button.title = label;
    button.innerHTML = `<i data-lucide="${theme === "dark" ? "sun" : "moon"}"></i>`;
    if (window.lucide && typeof window.lucide.createIcons === "function") {
      window.lucide.createIcons();
    }
  }

  function apply(theme) {
    root.dataset.theme = theme;
    paintToggle(theme);
  }

  apply(resolve());

  document.addEventListener("DOMContentLoaded", () => {
    paintToggle(root.dataset.theme);
    const button = document.getElementById("theme-toggle");
    if (button) {
      button.addEventListener("click", () => {
        const next = root.dataset.theme === "dark" ? "light" : "dark";
        try {
          localStorage.setItem(KEY, next);
        } catch {
          /* penyimpanan tidak tersedia; tema tetap berubah untuk sesi ini */
        }
        apply(next);
      });
    }
  });

  media.addEventListener("change", () => {
    if (!readSaved()) apply(resolve());
  });
})();
