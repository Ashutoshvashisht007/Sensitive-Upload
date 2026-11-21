// Runs in the page's own (MAIN) world at document_start.
//
// Some apps open the file picker on an <input type=file> that was never added
// to the document. Its change event then never reaches window, so the
// isolated-world engine can't see it. Before such an input opens its picker,
// park it in a hidden holder inside the document. Nothing else changes: the
// page keeps its own reference and handlers.
(() => {
  "use strict";
  if (window.__uploadGuardPageHook) return;
  Object.defineProperty(window, "__uploadGuardPageHook", { value: true });

  const proto = HTMLInputElement.prototype;
  const nativeClick = proto.click;
  const nativeShowPicker = proto.showPicker;

  function adopt(input) {
    try {
      if (!input || input.type !== "file" || input.isConnected) return;
      let holder = document.getElementById("upload-guard-holder");
      if (!holder) {
        holder = document.createElement("div");
        holder.id = "upload-guard-holder";
        holder.hidden = true;
        holder.style.display = "none";
        (document.body || document.documentElement).appendChild(holder);
      }
      holder.appendChild(input);
    } catch (_) {
      /* never break the page */
    }
  }

  proto.click = function click() {
    adopt(this);
    return nativeClick.apply(this, arguments);
  };
  if (typeof nativeShowPicker === "function") {
    proto.showPicker = function showPicker() {
      adopt(this);
      return nativeShowPicker.apply(this, arguments);
    };
  }
})();
