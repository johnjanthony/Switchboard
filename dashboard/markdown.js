// Full Markdown renderer for transcript messages, matching the phone (Android
// Markwon 4.6.2) feature set: CommonMark core + GFM tables + GFM task lists +
// GFM strikethrough + syntax-highlighted code. Built on the vendored
// markdown-it (which already bundles GFM tables, strikethrough, and a safe
// default link validator) plus highlight.js for fenced code blocks.
//
// Three deliberate deviations from the phone (do NOT undo):
//   (a) raw HTML is NOT rendered: it is escaped (html: false). Rendering raw
//       HTML in a browser would be an XSS hole.
//   (b) bare-URL autolinking is OFF (linkify: false): the phone has no linkify
//       plugin, so only explicit [text](href) links become anchors.
//   (c) links with dangerous schemes (javascript:, vbscript:, non-image data:)
//       are NOT rendered as anchors: markdown-it's default validateLink drops
//       them and the [text](href) stays literal text. The phone renders them as
//       live links; suppressing them here is the same browser-XSS-safety
//       rationale as (a). Do NOT loosen validateLink to "match" the phone.
import mdFactory from "./vendor/markdown-it.bundle.js";
import hljs from "./vendor/highlight.bundle.js";

// highlight callback: returns a full <pre class="hljs"><code class="language-..">
// wrapper. highlight.js highlight(...).value is already HTML-escaped, and for
// the unknown-language path we run the body through markdown-it's escapeHtml,
// so the output stays XSS-safe.
function highlight(code, lang) {
	// Escape lang before it enters the class attribute. markdown-it derives lang
	// from the fence info string via unescapeAll, which does NOT HTML-escape, so
	// an unrecognized language token is attacker-controlled and would break out of
	// the attribute (live XSS, even with html:false) if interpolated raw. Omit the
	// class entirely when there is no language.
	const langClass = lang ? ` class="language-${md.utils.escapeHtml(lang)}"` : "";
	if (lang && hljs.getLanguage(lang)) {
		const value = hljs.highlight(code, { language: lang, ignoreIllegals: true }).value;
		return `<pre class="hljs"><code${langClass}>${value}</code></pre>`;
	}
	return `<pre class="hljs"><code${langClass}>${md.utils.escapeHtml(code)}</code></pre>`;
}

const md = mdFactory({
	html: false, // escape raw HTML (XSS safety; deliberate deviation from phone)
	linkify: false, // no bare-URL autolinking (phone has no linkify plugin)
	breaks: false, // CommonMark soft-break parity with the phone
	highlight,
});

// GFM task lists: a list item whose text begins with "[ ] " or "[x] " (the x is
// case-insensitive) renders as a disabled checkbox followed by the remaining
// text, matching the phone ext-tasklist. This replicates the essential logic of
// markdown-it-task-lists as a small core rule operating on the token stream.
//
// The rule scans for the inline token that holds a list item's text, checks the
// first text child for the leading marker, strips the marker, and prepends a
// dedicated checkbox token. The checkbox token carries NO user text, so it can
// never smuggle unescaped content into the output.
const TASK_MARKER = /^\[([ xX])\]\s+/;

function isInlineInsideListItem(tokens, index) {
	// Pattern produced by markdown-it: list_item_open, paragraph_open, inline.
	if (tokens[index].type !== "inline") return false;
	if (index < 2) return false;
	return (
		tokens[index - 1].type === "paragraph_open" &&
		tokens[index - 2].type === "list_item_open"
	);
}

function taskListRule(state) {
	const tokens = state.tokens;
	for (let i = 0; i < tokens.length; i++) {
		if (!isInlineInsideListItem(tokens, i)) continue;
		const inline = tokens[i];
		const children = inline.children;
		if (!children || children.length === 0) continue;
		const first = children[0];
		if (first.type !== "text") continue;
		const match = TASK_MARKER.exec(first.content);
		if (!match) continue;

		const checked = match[1].toLowerCase() === "x";

		// Strip the "[ ] " / "[x] " marker from the visible text.
		first.content = first.content.slice(match[0].length);

		// Build a checkbox token. It renders a disabled <input type="checkbox">
		// via the custom renderer rule registered below. No user text rides on
		// this token.
		const checkbox = new state.Token("task_checkbox", "input", 0);
		checkbox.attrSet("type", "checkbox");
		checkbox.attrSet("disabled", "");
		if (checked) checkbox.attrSet("checked", "");

		// Prepend the checkbox to the inline content.
		children.unshift(checkbox);

		// Tag the enclosing list item and list so CSS can drop the bullet.
		const itemOpen = tokens[i - 2];
		itemOpen.attrJoin("class", "task-list-item");
		// Find the list_open that owns this item (scan backwards for the
		// nearest *_list_open at the same or lower nesting) and tag it once.
		for (let j = i - 3; j >= 0; j--) {
			const t = tokens[j];
			if (t.type === "bullet_list_open" || t.type === "ordered_list_open") {
				const existing = t.attrGet("class") || "";
				if (!existing.split(/\s+/).includes("contains-task-list")) {
					t.attrJoin("class", "contains-task-list");
				}
				break;
			}
			if (t.type === "bullet_list_close" || t.type === "ordered_list_close") {
				break;
			}
		}
	}
}

md.core.ruler.after("inline", "task_list", taskListRule);

// Render the checkbox token as a self-closing disabled input. renderToken
// produces only attribute names/values we set above, all of which are static,
// so this emits no user-controlled text.
md.renderer.rules.task_checkbox = function (tokens, idx, options, env, self) {
	return self.renderToken(tokens, idx, options);
};

export function renderMarkdown(text) {
	if (text == null) return "";
	return md.render(String(text));
}
