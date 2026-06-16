import { test } from "node:test";
import assert from "node:assert/strict";
import { renderMarkdown } from "./markdown.js";

test("escapes HTML so raw tags cannot inject markup", () => {
	const out = renderMarkdown("a <script>x</script> b");
	assert.ok(!out.includes("<script>"));
	assert.ok(out.includes("&lt;script&gt;"));
});

test("renders bold with double asterisks", () => {
	assert.ok(renderMarkdown("a **bold** b").includes("<strong>bold</strong>"));
});

test("renders italic with single asterisks", () => {
	assert.ok(renderMarkdown("a *em* b").includes("<em>em</em>"));
});

test("renders inline code", () => {
	assert.ok(renderMarkdown("run `npm test` now").includes("<code>npm test</code>"));
});

test("renders a fenced code block as pre/code", () => {
	const out = renderMarkdown("```\nline1\nline2\n```");
	assert.ok(out.includes("<pre><code>"));
	assert.ok(out.includes("line1\nline2"));
});

test("renders a link with safe href and escaped text", () => {
	const out = renderMarkdown("see [docs](https://example.com)");
	assert.ok(out.includes('<a href="https://example.com"'));
	assert.ok(out.includes(">docs</a>"));
});

test("drops a javascript: link, keeping only the escaped label text", () => {
	const out = renderMarkdown("see [click](javascript:foo)");
	assert.ok(!out.includes("javascript:"));
	assert.ok(!out.includes("<a "));
	assert.ok(out.includes("click"));
});

test("converts blank-line-separated blocks into paragraphs", () => {
	const out = renderMarkdown("para one\n\npara two");
	assert.ok(out.includes("<p>para one</p>"));
	assert.ok(out.includes("<p>para two</p>"));
});

test("converts a single newline inside a paragraph to a line break", () => {
	assert.ok(renderMarkdown("line a\nline b").includes("<br />"));
});
