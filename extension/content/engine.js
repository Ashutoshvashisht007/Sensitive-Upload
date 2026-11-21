// Interception and replay. Runs in the isolated world at document_start, so
// its capture-phase listeners on window run before any listener the page adds
// (React attaches its own to the app root, further down the path).
//
// Prompts: checked locally on Enter / send click / form submit. Clean prompts
//   pass straight through. A prompt with the marker is held while the desktop
//   app asks the user; Continue replays the send, Cancel leaves the text in place.
// Files (picker, drop, paste): always held briefly while the desktop app
//   inspects the bytes. Clean files are replayed silently; flagged files get a
//   native dialog first. If the app can't be reached, the action is blocked.
(() => {
  "use strict";
  if (globalThis.__uploadGuardEngine) return;
  globalThis.__uploadGuardEngine = true;

  const Marker = globalThis.UploadGuardMarker;
  const host = location.hostname;
  const site = (globalThis.UploadGuardSites || []).find((s) => s.hosts.includes(host));
  if (!site || !Marker) return;

  const MAX_FILE_BYTES = 25 * 1024 * 1024; // larger files are sent unread; the app asks the user
  const MAX_BATCH_BYTES = 40 * 1024 * 1024; // keeps one message well under Chrome's limit
  const ALLOW_RESEND_MS = 5000;

  // ---------------------------------------------------------------- logging
  let debug = false;
  try {
    chrome.storage.local.get({ debug: false }, (v) => (debug = !!v?.debug));
    chrome.storage.onChanged.addListener((changes, area) => {
      if (area === "local" && changes.debug) debug = !!changes.debug.newValue;
    });
  } catch (_) {}
  const log = (...args) => debug && console.log("[UploadGuard]", ...args);

  // ---------------------------------------------------------------- replay guard
  // Events we dispatch ourselves must not be intercepted again. click() and
  // dispatchEvent() run listeners synchronously, so a counter is enough.
  let bypass = 0;
  function replay(fn) {
    bypass++;
    try {
      fn();
    } finally {
      bypass--;
    }
  }
  function block(e) {
    e.preventDefault();
    e.stopImmediatePropagation();
  }

  // ---------------------------------------------------------------- talking to the app
  function ask(msg) {
    return new Promise((resolve) => {
      try {
        chrome.runtime.sendMessage({ ...msg, site: site.name }, (reply) => {
          if (chrome.runtime.lastError || !reply) resolve({ error: "extension_unavailable" });
          else resolve(reply);
        });
      } catch (_) {
        // "Extension context invalidated": the extension was reloaded under this tab.
        resolve({ error: "extension_unavailable" });
      }
    });
  }

  function explainError(reply) {
    if (reply.error === "host_unavailable") return "Sensitive Upload Guard app isn't running, blocked";
    if (reply.error === "extension_unavailable") return "Sensitive Upload Guard was updated, reload this page. Blocked";
    return "Sensitive Upload Guard couldn't check this, blocked";
  }

  // ---------------------------------------------------------------- DOM helpers
  const sel = (list) => list.join(",");
  const ALL_COMPOSERS = sel([...site.composers, ...site.editComposers]);
  const SEND_BUTTONS = sel(site.sendButtons);
  const GENERIC_SEND = /^(send|submit)\b/i;

  function composerText(el) {
    if (!el) return "";
    return String(el.value ?? el.innerText ?? el.textContent ?? "");
  }

  function isEditable(el) {
    return !!el && (el.tagName === "TEXTAREA" || el.isContentEditable);
  }

  function isVisible(el) {
    return el.getClientRects().length > 0;
  }

  // First visible editor under `node`, trying the adapter's selectors in order.
  const COMPOSER_ORDER = [...site.composers, ...site.editComposers, 'textarea,[contenteditable="true"]'];
  function findComposerIn(node) {
    for (const s of COMPOSER_ORDER) {
      for (const el of node.querySelectorAll(s)) {
        if (isEditable(el) && isVisible(el)) return el;
      }
    }
    return null;
  }

  // The composer an Enter keypress belongs to (main ask box or a message being edited).
  function composerFor(target) {
    if (!(target instanceof Element)) return null;
    return target.closest(ALL_COMPOSERS);
  }

  // Nearest composer around a button: its form first, then a few ancestors up.
  function composerNear(el) {
    let node = el.closest("form") || el.parentElement;
    for (let i = 0; node && i < 10; i++, node = node.parentElement) {
      const found = findComposerIn(node);
      if (found) return found;
    }
    return null;
  }

  function sendButtonFor(composer) {
    let node = composer;
    for (let i = 0; node && i < 10; i++, node = node.parentElement) {
      for (const s of site.sendButtons) {
        const b = node.querySelector(s);
        if (b) return b;
      }
      const generic = [...node.querySelectorAll("button")].find((b) => GENERIC_SEND.test(buttonLabel(b)));
      if (generic) return generic;
    }
    return null; // caller falls back to an Enter keypress
  }

  function buttonLabel(b) {
    return (b.getAttribute("aria-label") || b.textContent || "").trim();
  }

  // Is this click on a send button? Returns {button, composer} or null.
  function sendClick(target) {
    if (!(target instanceof Element)) return null;
    const button = target.closest('button,[role="button"],input[type="submit"]');
    if (!button) return null;
    if (button.matches(SEND_BUTTONS) || GENERIC_SEND.test(buttonLabel(button))) {
      const composer = composerNear(button);
      return composer ? { button, composer } : null;
    }
    return null;
  }

  // ---------------------------------------------------------------- prompts
  let pendingPrompt = null; // {composer, text}
  let allowed = null; // {text, until}: lets the approved text through if the app resubmits late

  // Returns true if the event was held.
  function checkPrompt(e, composer, trigger) {
    if (bypass || !composer) return false;
    if (pendingPrompt) {
      block(e); // a dialog is already up for this prompt
      return true;
    }
    const text = composerText(composer);
    if (!text.trim() || !Marker.has(text)) return false;
    if (allowed && allowed.text === text && Date.now() < allowed.until) return false;

    block(e);
    pendingPrompt = { composer, text };
    log("prompt held", { trigger: trigger.kind, chars: text.length });
    ask({ type: "prompt", text }).then((reply) => {
      pendingPrompt = null;
      if (reply.error) {
        log("prompt blocked:", reply.error);
        toast(explainError(reply));
        return;
      }
      if (reply.decision !== "continue") {
        toast("Blocked by Sensitive Upload Guard");
        composer.focus?.();
        return;
      }
      if (composerText(composer) !== text) {
        log("prompt changed while the dialog was open; not sending");
        return;
      }
      allowed = { text, until: Date.now() + ALLOW_RESEND_MS };
      resubmit(composer, trigger);
    });
    return true;
  }

  function resubmit(composer, trigger) {
    replay(() => {
      if (trigger.kind === "click" && trigger.button.isConnected && !trigger.button.disabled) {
        log("replay: click", trigger.button);
        trigger.button.click();
        return;
      }
      if (trigger.kind === "submit" && trigger.form.isConnected) {
        log("replay: form submit");
        trigger.form.requestSubmit();
        return;
      }
      const button = sendButtonFor(composer);
      if (button && !button.disabled) {
        log("replay: send button", button);
        button.click();
        return;
      }
      log("replay: Enter key");
      composer.focus?.();
      composer.dispatchEvent(
        new KeyboardEvent("keydown", { key: "Enter", code: "Enter", bubbles: true, cancelable: true, composed: true }),
      );
    });
  }

  function onKeyDown(e) {
    if (bypass || e.key !== "Enter" || e.isComposing || e.keyCode === 229 || e.shiftKey) return;
    const composer = composerFor(e.target);
    if (composer) checkPrompt(e, composer, { kind: "key" });
  }

  function onClick(e) {
    if (bypass) return;
    const hit = sendClick(e.target);
    if (hit) checkPrompt(e, hit.composer, { kind: "click", button: hit.button });
  }

  function onSubmit(e) {
    if (bypass || !(e.target instanceof HTMLFormElement)) return;
    const composer = e.target.querySelector(ALL_COMPOSERS);
    if (composer) checkPrompt(e, composer, { kind: "submit", form: e.target });
  }

  // ---------------------------------------------------------------- files
  async function toBase64(file) {
    const bytes = new Uint8Array(await file.arrayBuffer());
    if (typeof bytes.toBase64 === "function") return bytes.toBase64();
    let binary = "";
    const CHUNK = 0x8000; // String.fromCharCode.apply has an argument limit
    for (let i = 0; i < bytes.length; i += CHUNK) {
      binary += String.fromCharCode.apply(null, bytes.subarray(i, i + CHUNK));
    }
    return btoa(binary);
  }

  // Ask the app about a batch of files. Resolves to one boolean per file: may it go?
  async function inspectFiles(files) {
    const payload = [];
    let total = 0;
    for (const f of files) {
      const entry = { name: f.name || "unnamed", mime: f.type || "", size: f.size, b64: null };
      if (f.size > MAX_FILE_BYTES || total + f.size > MAX_BATCH_BYTES) {
        entry.skipped = "too_large";
      } else {
        try {
          entry.b64 = await toBase64(f);
          total += f.size;
        } catch (_) {
          entry.skipped = "the browser couldn't read it";
        }
      }
      payload.push(entry);
    }
    log("files held", payload.map((p) => ({ name: p.name, size: p.size, skipped: p.skipped })));
    const reply = await ask({ type: "files", files: payload });
    if (reply.error || !Array.isArray(reply.results)) {
      log("files blocked:", reply.error);
      toast(explainError(reply));
      return files.map(() => false);
    }
    const allow = files.map(() => false);
    reply.results.forEach((r, i) => {
      const idx = Number.isInteger(r.index) ? r.index : i;
      if (idx >= 0 && idx < allow.length) allow[idx] = r.decision === "continue";
    });
    const blocked = allow.filter((a) => !a).length;
    if (blocked === files.length) toast("Blocked by Sensitive Upload Guard");
    else if (blocked) toast(`Blocked by Sensitive Upload Guard: ${blocked} of ${files.length} files removed`);
    log("files decided", allow);
    return allow;
  }

  function dataTransferWith(files, extras = []) {
    const dt = new DataTransfer();
    for (const f of files) dt.items.add(f);
    for (const [type, value] of extras) {
      try {
        dt.setData(type, value);
      } catch (_) {}
    }
    return dt;
  }

  // Non-file clipboard/drag data (text, html, uri-list) to carry over on replay.
  function nonFileData(dt) {
    const out = [];
    for (const type of dt.types || []) {
      if (type === "Files") continue;
      try {
        out.push([type, dt.getData(type)]);
      } catch (_) {}
    }
    return out;
  }

  // File picker: <input type=file> fires input then change; hold both, replay both.
  const heldInputs = new Map(); // input -> Set of event types seen

  function onFileInput(e) {
    if (bypass) return;
    const input = e.target;
    if (!(input instanceof HTMLInputElement) || input.type !== "file") return;
    const seen = heldInputs.get(input);
    if (seen) {
      seen.add(e.type);
      e.stopImmediatePropagation();
      return;
    }
    const files = [...(input.files || [])];
    if (!files.length) return;
    e.stopImmediatePropagation();
    const events = new Set([e.type]);
    heldInputs.set(input, events);
    inspectFiles(files).then((allow) => {
      heldInputs.delete(input);
      const ok = files.filter((_, i) => allow[i]);
      if (!ok.length) {
        input.value = ""; // so picking the same file again fires change
        return;
      }
      if (ok.length < files.length) input.files = dataTransferWith(ok).files;
      replay(() => {
        for (const type of ["input", "change"]) {
          if (events.has(type)) input.dispatchEvent(new Event(type, { bubbles: true, composed: type === "input" }));
        }
      });
    });
  }

  function onDrop(e) {
    if (bypass) return;
    const dt = e.dataTransfer;
    if (!dt || ![...(dt.types || [])].includes("Files")) return;
    const files = [...dt.files]; // copy now: the DataTransfer is locked after the event
    if (!files.length) return;
    block(e);
    const { target, clientX, clientY, screenX, screenY } = e;
    const extras = nonFileData(dt);
    inspectFiles(files).then((allow) => {
      const ok = files.filter((_, i) => allow[i]);
      if (!ok.length) {
        // let the app hide its drop overlay
        replay(() => target.dispatchEvent(new DragEvent("dragleave", { bubbles: true, composed: true, clientX, clientY })));
        return;
      }
      const init = { bubbles: true, cancelable: true, composed: true, clientX, clientY, screenX, screenY };
      replay(() => target.dispatchEvent(new DragEvent("drop", { ...init, dataTransfer: dataTransferWith(ok, extras) })));
    });
  }

  function onPaste(e) {
    if (bypass) return;
    const cd = e.clipboardData;
    const files = cd ? [...cd.files] : [];
    if (!files.length) return; // text-only paste: checked when the prompt is sent
    block(e);
    const target = e.target;
    const extras = nonFileData(cd);
    inspectFiles(files).then((allow) => {
      const ok = files.filter((_, i) => allow[i]);
      if (!ok.length) return;
      const clipboardData = dataTransferWith(ok, extras);
      replay(() =>
        target.dispatchEvent(new ClipboardEvent("paste", { bubbles: true, cancelable: true, composed: true, clipboardData })),
      );
    });
  }

  // Extra layer: direct listeners on every file input, in case a page stops
  // propagation on the way down. Window capture already runs first, so these
  // only matter for inputs whose events never reach window.
  function watchInput(input) {
    if (input.__uploadGuardWatched) return;
    input.__uploadGuardWatched = true;
    input.addEventListener("input", onDirectFileEvent, true);
    input.addEventListener("change", onDirectFileEvent, true);
  }
  function onDirectFileEvent(e) {
    if (e.__uploadGuardSeen) return;
    onFileInput(e);
  }
  function markSeen(e) {
    e.__uploadGuardSeen = true;
  }
  new MutationObserver((records) => {
    for (const r of records) {
      for (const n of r.addedNodes) {
        if (n.nodeType !== 1) continue;
        if (n.matches?.('input[type="file"]')) watchInput(n);
        n.querySelectorAll?.('input[type="file"]').forEach(watchInput);
      }
    }
  }).observe(document, { childList: true, subtree: true });

  // ---------------------------------------------------------------- toasts
  let toastHost = null;
  let toastBox = null;
  let toastTimer = null;
  function toast(message) {
    try {
      if (!toastHost || !toastHost.isConnected) {
        toastHost = document.createElement("upload-guard-toast");
        const root = toastHost.attachShadow({ mode: "closed" });
        root.innerHTML = `<style>
          .t { position: fixed; z-index: 2147483647; left: 50%; bottom: 96px; transform: translateX(-50%);
               max-width: min(560px, calc(100vw - 32px)); padding: 10px 16px; border-radius: 10px;
               background: #7f1d1d; color: #fff; font: 500 14px/1.4 -apple-system, system-ui, sans-serif;
               box-shadow: 0 8px 24px rgba(0,0,0,.25); opacity: 0; transition: opacity .2s; pointer-events: none; }
          .t.show { opacity: 1; }
        </style><div class="t" role="status" aria-live="polite"></div>`;
        toastBox = root.querySelector(".t");
        document.documentElement.appendChild(toastHost);
      }
      toastBox.textContent = message;
      toastBox.classList.add("show");
      clearTimeout(toastTimer);
      toastTimer = setTimeout(() => toastBox.classList.remove("show"), 4000);
    } catch (_) {}
  }

  // ---------------------------------------------------------------- wire up
  const opts = { capture: true };
  window.addEventListener("keydown", onKeyDown, opts);
  window.addEventListener("click", onClick, opts);
  window.addEventListener("submit", onSubmit, opts);
  window.addEventListener("input", (e) => (markSeen(e), onFileInput(e)), opts);
  window.addEventListener("change", (e) => (markSeen(e), onFileInput(e)), opts);
  window.addEventListener("drop", onDrop, opts);
  window.addEventListener("paste", onPaste, opts);

  // Wake the service worker so the toolbar badge reflects the app's state.
  try {
    chrome.runtime.sendMessage({ type: "ping" }, () => void chrome.runtime.lastError);
  } catch (_) {}
  log(`active on ${host} (${site.name})`);

  // Exposed for the automated tests only (isolated world; the page can't see it).
  globalThis.__uploadGuardTest = { site: site.name, composerText, sendButtonFor };
})();
