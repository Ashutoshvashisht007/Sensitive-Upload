// ChatGPT adapter: selectors only. Each list is tried in order.
(globalThis.UploadGuardSites = globalThis.UploadGuardSites || []).push({
  name: "chatgpt",
  hosts: ["chatgpt.com", "chat.openai.com"],
  // Main composer (ProseMirror contenteditable) and its fallbacks.
  composers: ["#prompt-textarea", 'form div[contenteditable="true"]', "form textarea"],
  // Editing an earlier message opens a textarea inside the conversation.
  editComposers: ['[data-message-author-role="user"] textarea', 'article textarea', "main textarea"],
  sendButtons: ['[data-testid="send-button"]', "#composer-submit-button", 'button[aria-label*="Send" i]'],
  // Assistant replies only; user messages have role "user".
  responses: ['[data-message-author-role="assistant"]'],
});
