// Recolours AI responses. One <style> element built from the adapter's
// response selectors; it applies while text is still streaming in, because it
// matches the containers rather than individual nodes.
(() => {
  "use strict";
  const site = (globalThis.UploadGuardSites || []).find((s) => s.hosts.includes(location.hostname));
  if (!site || !site.responses.length) return;

  const DEFAULTS = { recolor: true, colorLight: "#0f766e", colorDark: "#5eead4" };
  const COLOR = /^#[0-9a-f]{3,8}$/i;
  let settings = { ...DEFAULTS };

  const style = document.createElement("style");
  style.id = "upload-guard-response-color";

  function css({ colorLight, colorDark }) {
    const light = COLOR.test(colorLight) ? colorLight : DEFAULTS.colorLight;
    const dark = COLOR.test(colorDark) ? colorDark : DEFAULTS.colorDark;
    const targets = site.responses.map((s) => `${s}, ${s} *`).join(",\n");
    return `
:root { --upload-guard-response: ${light}; }
@media (prefers-color-scheme: dark) {
  :root:not(.light):not([data-theme="light"]):not([data-color-scheme="light"]) { --upload-guard-response: ${dark}; }
}
:root.dark, :root[data-theme="dark"], :root[data-color-scheme="dark"] { --upload-guard-response: ${dark}; }
${targets} {
  color: var(--upload-guard-response) !important;
  -webkit-text-fill-color: var(--upload-guard-response) !important;
}`;
  }

  function apply() {
    if (!settings.recolor) {
      style.remove();
      return;
    }
    style.textContent = css(settings);
    ensureAttached();
  }

  function ensureAttached() {
    if (!settings.recolor || style.isConnected) return;
    (document.head || document.documentElement).appendChild(style);
  }

  // SPAs sometimes replace <head> or strip unknown nodes: put the style back.
  let watchedHead = null;
  const observer = new MutationObserver(() => {
    ensureAttached();
    if (document.head && document.head !== watchedHead) {
      watchedHead = document.head;
      observer.observe(watchedHead, { childList: true });
    }
  });
  observer.observe(document.documentElement, { childList: true });

  apply();
  try {
    chrome.storage.sync.get(DEFAULTS, (v) => {
      settings = { ...DEFAULTS, ...v };
      apply();
    });
    chrome.storage.onChanged.addListener((changes, area) => {
      if (area !== "sync") return;
      for (const [k, { newValue }] of Object.entries(changes)) if (k in DEFAULTS) settings[k] = newValue;
      apply();
    });
  } catch (_) {}
})();
