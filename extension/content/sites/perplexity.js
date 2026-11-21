// Perplexity adapter: selectors only. Each list is tried in order.
(globalThis.UploadGuardSites = globalThis.UploadGuardSites || []).push({
  name: "perplexity",
  hosts: ["www.perplexity.ai", "perplexity.ai"],
  // Ask box (Lexical contenteditable), older textarea version as fallback.
  composers: ["#ask-input", 'div[contenteditable="true"][role="textbox"]', "textarea[placeholder]", "textarea"],
  editComposers: [],
  sendButtons: ['button[aria-label="Submit"]', 'button[data-testid="submit-button"]', 'button[aria-label*="Submit" i]'],
  // Answer bodies. The user's question is rendered outside these.
  responses: ['[id^="markdown-content-"]', ".prose:not(textarea)"],
});
