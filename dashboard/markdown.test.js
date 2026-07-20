import { test } from "node:test";
import assert from "node:assert/strict";
import { renderMarkdown } from "./markdown.js";

test("renders h1 and h2 headings", () => {
	const out = renderMarkdown("# Title\n## Sub");
	assert.ok(out.includes("<h1>Title</h1>"));
	assert.ok(out.includes("<h2>Sub</h2>"));
});

test("renders an unordered list as ul/li", () => {
	const out = renderMarkdown("- one\n- two");
	assert.ok(out.includes("<ul>"));
	assert.ok(out.includes("<li>one</li>"));
	assert.ok(out.includes("<li>two</li>"));
});

test("renders an ordered list as ol/li", () => {
	const out = renderMarkdown("1. one\n2. two");
	assert.ok(out.includes("<ol>"));
	assert.ok(out.includes("<li>one</li>"));
	assert.ok(out.includes("<li>two</li>"));
});

test("renders a blockquote", () => {
	const out = renderMarkdown("> quoted");
	assert.ok(out.includes("<blockquote>"));
	assert.ok(out.includes("quoted"));
});

test("renders a GFM table with th and td", () => {
	const out = renderMarkdown("| a | b |\n|---|---|\n| 1 | 2 |");
	assert.ok(out.includes("<table>"));
	assert.ok(out.includes("<th>a</th>"));
	assert.ok(out.includes("<td>1</td>"));
});

test("renders strikethrough", () => {
	const out = renderMarkdown("~~gone~~");
	// Markwon/markdown-it emit <s>; accept <del> as well for parity tolerance.
	assert.ok(out.includes("<s>gone</s>") || out.includes("<del>gone</del>"));
});

test("renders a task list with unchecked and checked disabled checkboxes", () => {
	const out = renderMarkdown("- [ ] todo\n- [x] done");
	// Two checkbox inputs, both disabled, exactly one checked.
	const inputs = out.match(/<input[^>]*>/g) || [];
	assert.equal(inputs.length, 2);
	assert.ok(inputs.every((i) => /type="checkbox"/.test(i)));
	assert.ok(inputs.every((i) => /\bdisabled\b/.test(i)));
	const checked = inputs.filter((i) => /\bchecked\b/.test(i));
	assert.equal(checked.length, 1);
	// Visible text is preserved; literal bracket marker is gone.
	assert.ok(out.includes("todo"));
	assert.ok(out.includes("done"));
	assert.ok(!out.includes("[ ]"));
	assert.ok(!out.includes("[x]"));
});

test("renders a js fenced code block with language-js class and no leaked lang word", () => {
	const out = renderMarkdown("```js\nconst a = 1;\n```");
	assert.ok(out.includes("<code class=\"language-js\""));
	// The bare language tag must not leak into the rendered code body.
	assert.ok(!/<code[^>]*>js\b/.test(out));
	assert.ok(!/<code[^>]*>\s*js\n/.test(out));
});

test("renders a fenced code block with no language as pre/code", () => {
	const out = renderMarkdown("```\nplain code\n```");
	assert.ok(out.includes("<pre"));
	assert.ok(out.includes("<code"));
	assert.ok(out.includes("plain code"));
});

test("renders inline code", () => {
	assert.ok(renderMarkdown("run `npm test` now").includes("<code>npm test</code>"));
});

test("renders bold as strong", () => {
	assert.ok(renderMarkdown("a **bold** b").includes("<strong>bold</strong>"));
});

test("renders italic as em", () => {
	assert.ok(renderMarkdown("a *em* b").includes("<em>em</em>"));
});

test("renders a normal https link as an anchor with the right href, target, and rel", () => {
	const out = renderMarkdown("see [docs](https://example.com)");
	assert.ok(out.includes('<a href="https://example.com"'));
	assert.ok(out.includes('target="_blank"'));
	assert.ok(out.includes('rel="noopener noreferrer"'));
	assert.ok(out.includes(">docs</a>"));
});

test("does not produce an anchor for a javascript: link", () => {
	const out = renderMarkdown("see [click](javascript:alert(1))");
	assert.ok(!out.includes("<a "));
	assert.ok(!/href="javascript:/i.test(out));
});

test("escapes raw HTML such as a script tag", () => {
	const out = renderMarkdown("a <script>x</script> b");
	assert.ok(!out.includes("<script>"));
	assert.ok(out.includes("&lt;script&gt;"));
});

test("escapes a malicious code-fence language tag so it cannot break out of the class attribute", () => {
	const out = renderMarkdown('```x"></code></pre><img/src=x/onerror=alert(1)>\nbody\n```');
	// The language token is attacker-controlled; it must not inject a live element.
	assert.ok(!/<img/i.test(out), "must not emit a live <img>");
	// The breakout characters survive only in escaped form inside the class attr.
	assert.ok(out.includes("&lt;img"));
	assert.ok(out.includes("&quot;"));
	// Exactly one real <pre>...</pre> wrapper; the injected </pre> is escaped.
	assert.equal((out.match(/<\/pre>/g) || []).length, 1);
});

test("returns empty string for null or undefined", () => {
	assert.equal(renderMarkdown(null), "");
	assert.equal(renderMarkdown(undefined), "");
});
