import { test } from "node:test";
import assert from "node:assert/strict";
import { escapeText, escapeAttr } from "./escape.js";

test("escapeText escapes the three HTML-structural characters", () => {
	assert.equal(escapeText("a & b < c > d"), "a &amp; b &lt; c &gt; d");
});

test("escapeText is null-safe", () => {
	assert.equal(escapeText(null), "");
	assert.equal(escapeText(undefined), "");
});

test("escapeText escapes an ampersand before angle brackets (no double-encode surprise)", () => {
	assert.equal(escapeText("<script>"), "&lt;script&gt;");
	assert.equal(escapeText("&lt;"), "&amp;lt;");
});

test("escapeAttr additionally escapes single and double quotes", () => {
	assert.equal(escapeAttr(`x"y'z`), "x&quot;y&#39;z");
	assert.equal(escapeAttr("a & <b>"), "a &amp; &lt;b&gt;");
});
