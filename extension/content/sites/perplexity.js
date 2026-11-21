// Perplexity adapter: selectors only. Each list is tried in order.
// Checked against the live site on 21 Nov 2026 (recon snippet in the README).
(globalThis.UploadGuardSites = globalThis.UploadGuardSites || []).push({
  name: "perplexity",
  hosts: ["www.perplexity.ai", "perplexity.ai"],
  // Ask box: a contenteditable role=textbox ("Ask anything" / "Ask a follow-up").
  // #ask-input and textarea are older versions, kept as fallbacks.
  composers: ['div[contenteditable="true"][role="textbox"]', "#ask-input", "textarea[placeholder]", "textarea"],
  editComposers: [],
  sendButtons: ['button[aria-label="Submit"]', 'button[data-testid="submit-button"]', 'button[aria-label*="Submit" i]'],
  // Answer bodies (div.prose). markdown-content-N is an older id for the same thing.
  responses: [".prose", '[id^="markdown-content-"]'],
});
