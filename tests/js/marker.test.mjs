// Runs the shared vectors against the extension's marker.js: `node --test tests/js`
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { createRequire } from "node:module";

const require = createRequire(import.meta.url);
const Marker = require("../../extension/content/marker.js");
const vectors = JSON.parse(readFileSync(new URL("../vectors/marker_cases.json", import.meta.url)));

for (const c of vectors.cases) {
  test(`marker: ${c.name}`, () => {
    const m = Marker.find(c.text, c.ocr ?? false);
    assert.equal(m !== null, c.found);
    if (c.found) assert.equal(m.matched, c.matched);
  });
}

for (const c of vectors.excerpts) {
  test(`excerpt: ${c.name}`, () => {
    const m = Marker.find(c.text);
    assert.ok(m);
    assert.equal(Marker.makeExcerpt(m.text, m.start, m.end), c.excerpt);
  });
}
