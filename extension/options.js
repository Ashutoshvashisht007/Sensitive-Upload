"use strict";

const DEFAULTS = { recolor: true, colorLight: "#0f766e", colorDark: "#5eead4" };
const $ = (id) => document.getElementById(id);

function showStatus(s) {
  $("dot").className = "dot " + (s && s.connected ? "ok" : "bad");
  $("status").textContent = s && s.connected
    ? `Connected (app version ${s.version})`
    : `Not reachable${s && s.detail ? `: ${s.detail}` : ""}`;
}

function check() {
  $("status").textContent = "Checking…";
  chrome.runtime.sendMessage({ type: "ping" }, (reply) => showStatus(chrome.runtime.lastError ? null : reply));
}

chrome.storage.sync.get(DEFAULTS, (v) => {
  $("recolor").checked = v.recolor;
  $("colorLight").value = v.colorLight;
  $("colorDark").value = v.colorDark;
});
chrome.storage.local.get({ debug: false }, (v) => ($("debug").checked = v.debug));

$("recolor").addEventListener("change", (e) => chrome.storage.sync.set({ recolor: e.target.checked }));
$("colorLight").addEventListener("input", (e) => chrome.storage.sync.set({ colorLight: e.target.value }));
$("colorDark").addEventListener("input", (e) => chrome.storage.sync.set({ colorDark: e.target.value }));
$("debug").addEventListener("change", (e) => chrome.storage.local.set({ debug: e.target.checked }));
$("reset").addEventListener("click", () => {
  chrome.storage.sync.set({ colorLight: DEFAULTS.colorLight, colorDark: DEFAULTS.colorDark });
  $("colorLight").value = DEFAULTS.colorLight;
  $("colorDark").value = DEFAULTS.colorDark;
});
$("recheck").addEventListener("click", check);
check();
