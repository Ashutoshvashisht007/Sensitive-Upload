// ChatGPT adapter: selectors only. Each list is tried in order.
// Checked against the live site on 21 Nov 2026 (recon snippet in the README).
(globalThis.UploadGuardSites = globalThis.UploadGuardSites || []).push({
  name: "chatgpt",
  hosts: ["chatgpt.com", "chat.openai.com"],
  // Composer: a ProseMirror contenteditable (aria-label "Ask ChatGPT") inside
  // [data-composer-input]. #prompt-textarea is the older id, kept as a fallback.
  composers: [
    '[data-composer-input] [contenteditable="true"]',
    '[data-composer-input][contenteditable="true"]',
    'div.ProseMirror[contenteditable="true"]',
    '[contenteditable="true"][role="textbox"]',
    "#prompt-textarea",
    'form div[contenteditable="true"]',
    "form textarea",
  ],
  // Editing an earlier message opens an editor inside the user's bubble.
  editComposers: [
    '[data-user-message-bubble] textarea',
    '[data-user-message-bubble] [contenteditable="true"]',
    '[data-message-author-role="user"] textarea',
  ],
  sendButtons: [
    '[data-testid="send-button"]',
    "#composer-submit-button",
    '[data-composer-trailing-controls] button[aria-label*="Send" i]',
    'button[aria-label*="Send" i]',
  ],
  // Assistant replies only; the user's own messages are [data-user-message-bubble].
  responses: [
    '[data-markdown-text-style="assistant-message"]',
    "[data-response-body]",
    '[data-message-author-role="assistant"]',
  ],
});
