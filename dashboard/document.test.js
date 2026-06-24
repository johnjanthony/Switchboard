import { test } from "node:test";
import assert from "node:assert/strict";
import { documentPillHtml } from "./document.js";

test("pill links to the preview page with encoded conv/msg/name", () => {
	const out = documentPillHtml({ url: "https://store/x", filename: "report.md" }, "conv-1", "m-9");
	assert.ok(out.includes('href="doc-view.html?conv=conv-1&amp;msg=m-9&amp;name=report.md"'));
	assert.ok(out.includes("<a "));
	assert.ok(out.includes("target=\"_blank\""));
});

test("pill label is the filename leaf", () => {
	const out = documentPillHtml({ url: "https://store/x", filename: "docs/sub/report.md" }, "c", "m");
	assert.ok(out.includes(">📎 report.md<"));
	assert.ok(!out.includes("docs/sub"));
});

test("pill escapes HTML in the filename label", () => {
	const out = documentPillHtml({ url: "https://store/x", filename: "<img onerror=alert(1)>.md" }, "c", "m");
	assert.ok(!out.includes("<img"));
	assert.ok(out.includes("&lt;img"));
});

test("pill renders nothing without a document or without ids", () => {
	assert.equal(documentPillHtml({ text: "notify" }, "c", "m"), "");
	assert.equal(documentPillHtml({ url: "https://store/x", filename: "" }, "c", "m"), "");
	assert.equal(documentPillHtml({ url: "https://store/x", filename: "a.md" }, null, "m"), "");
	assert.equal(documentPillHtml({ url: "https://store/x", filename: "a.md" }, "c", null), "");
	assert.equal(documentPillHtml(null, "c", "m"), "");
});

import { classifyDocument } from "./document.js";

test("classifyDocument maps extensions to render modes", () => {
	assert.equal(classifyDocument("a.md"), "markdown");
	assert.equal(classifyDocument("a.MARKDOWN"), "markdown");
	assert.equal(classifyDocument("a.png"), "image");
	assert.equal(classifyDocument("a.svg"), "image");
	assert.equal(classifyDocument("a.pdf"), "pdf");
	assert.equal(classifyDocument("a.json"), "text");
	assert.equal(classifyDocument("a.py"), "text");
	assert.equal(classifyDocument("a.log"), "text");
	assert.equal(classifyDocument("mystery.bin"), "other");
	assert.equal(classifyDocument("README"), "other");
	assert.equal(classifyDocument(""), "other");
});
