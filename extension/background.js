// Service worker: the bridge between content scripts and the desktop app.
//
// One native messaging port, opened lazily. Each request gets an id; replies
// are matched back to the waiting content script. If the port drops, every
// waiting request is answered with {error: "host_unavailable"} so the page
// can fail closed. The toolbar badge shows whether the app is reachable.
"use strict";

const HOST = "com.walled.upload_guard";
const PING_TIMEOUT_MS = 5000;
const DECISION_TIMEOUT_MS = 30 * 60 * 1000; // the user may leave a dialog open a while
const KEEPALIVE_MS = 4 * 60 * 1000;

let port = null;
let nextId = 1;
const pending = new Map(); // id -> resolve
const state = { connected: false, detail: "not checked yet", version: null, maxInspectMb: 25 };

function setStatus(connected, detail) {
  state.connected = connected;
  state.detail = detail;
  chrome.action.setBadgeText({ text: connected ? "✓" : "!" });
  chrome.action.setBadgeBackgroundColor({ color: connected ? "#15803d" : "#b91c1c" });
  chrome.action.setTitle({
    title: connected
      ? `Sensitive Upload Guard: protecting ChatGPT and Perplexity (app ${state.version})`
      : `Sensitive Upload Guard: desktop app not reachable (${detail}). Run "upload-guard install".`,
  });
}

function connect() {
  if (port) return port;
  try {
    port = chrome.runtime.connectNative(HOST);
  } catch (e) {
    port = null;
    setStatus(false, String(e?.message || e));
    return null;
  }
  port.onMessage.addListener((msg) => {
    if (!msg || typeof msg !== "object") return;
    if (msg.ok) {
      state.version = msg.version;
      state.maxInspectMb = msg.max_inspect_mb || state.maxInspectMb;
      setStatus(true, "connected");
    }
    const resolve = pending.get(msg.id);
    if (resolve) {
      pending.delete(msg.id);
      resolve(msg);
    }
  });
  port.onDisconnect.addListener(() => {
    const detail = chrome.runtime.lastError?.message || "disconnected";
    port = null;
    setStatus(false, detail);
    for (const resolve of pending.values()) resolve({ error: "host_unavailable", detail });
    pending.clear();
  });
  return port;
}

function request(msg, timeoutMs) {
  return new Promise((resolve) => {
    const p = connect();
    if (!p) return resolve({ error: "host_unavailable", detail: state.detail });
    const id = nextId++;
    const timer = setTimeout(() => {
      if (pending.delete(id)) resolve({ error: "timeout" });
    }, timeoutMs);
    pending.set(id, (reply) => {
      clearTimeout(timer);
      resolve(reply);
    });
    try {
      p.postMessage({ ...msg, id });
    } catch (e) {
      pending.delete(id);
      clearTimeout(timer);
      resolve({ error: "host_unavailable", detail: String(e?.message || e) });
    }
  });
}

function ping() {
  return request({ type: "ping" }, PING_TIMEOUT_MS);
}

chrome.runtime.onMessage.addListener((msg, sender, sendResponse) => {
  if (!msg || typeof msg !== "object" || sender.id !== chrome.runtime.id) return false;
  switch (msg.type) {
    case "prompt":
      request({ type: "prompt", site: msg.site, text: msg.text }, DECISION_TIMEOUT_MS).then(sendResponse);
      return true;
    case "files":
      request({ type: "files", site: msg.site, files: msg.files }, DECISION_TIMEOUT_MS).then(sendResponse);
      return true;
    case "ping":
      ping().then((reply) => sendResponse({ ...reply, ...state }));
      return true;
    case "status":
      sendResponse({ ...state });
      return false;
    default:
      return false;
  }
});

chrome.runtime.onStartup.addListener(ping);
chrome.runtime.onInstalled.addListener(ping);
setInterval(() => {
  if (port) ping();
}, KEEPALIVE_MS);
ping();
