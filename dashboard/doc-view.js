import { renderMarkdown } from "./markdown.js";
import hljs from "./vendor/highlight.bundle.js";
import { classifyDocument } from "./document.js";

// Map a few common extensions to highlight.js language ids; unknown extensions
// fall back to an escaped plain block (matching markdown.js's safety stance).
const EXT_LANG = {
	js: "javascript", mjs: "javascript", cjs: "javascript", jsx: "javascript",
	ts: "typescript", tsx: "typescript", py: "python", java: "java", c: "c",
	cc: "cpp", cpp: "cpp", h: "cpp", hpp: "cpp", cs: "csharp", sh: "bash",
	bash: "bash", zsh: "bash", yml: "yaml", yaml: "yaml", xml: "xml",
	html: "xml", htm: "xml", css: "css", scss: "scss", kt: "kotlin",
	sql: "sql", toml: "ini", ini: "ini", json: "json", rb: "ruby", go: "go",
	rs: "rust", php: "php", swift: "swift", diff: "diff", patch: "diff",
};

function escapeText(s) {
	return String(s == null ? "" : s)
		.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
}
function escapeAttr(s) {
	return escapeText(s).replace(/"/g, "&quot;").replace(/'/g, "&#39;");
}

function headerHtml(name, src) {
	return `<div class="doc-head">`
		+ `<span class="doc-name">${escapeText(name)}</span>`
		+ `<a class="doc-download" href="${escapeAttr(src + "&download=1")}">Download</a>`
		+ `</div>`;
}

function highlightCode(text, name) {
	const ext = name.toLowerCase().includes(".") ? name.toLowerCase().split(".").pop() : "";
	const lang = EXT_LANG[ext];
	if (lang && hljs.getLanguage(lang)) {
		const value = hljs.highlight(text, { language: lang, ignoreIllegals: true }).value;
		return `<div class="msg-body"><pre class="hljs"><code class="language-${escapeAttr(lang)}">${value}</code></pre></div>`;
	}
	return `<div class="msg-body"><pre class="hljs"><code>${escapeText(text)}</code></pre></div>`;
}

function renderError(root, message) {
	root.innerHTML = `<div class="doc-error">${escapeText(message)}</div>`;
}

async function main() {
	const root = document.getElementById("doc-view");
	const params = new URLSearchParams(location.search);
	const conv = params.get("conv");
	const msg = params.get("msg");
	const name = params.get("name") || "document";
	document.title = "Operator - " + name;

	if (!conv || !msg) {
		renderError(root, "Missing document reference.");
		return;
	}
	const src = "/document?conv=" + encodeURIComponent(conv) + "&msg=" + encodeURIComponent(msg);
	const header = headerHtml(name, src);
	const kind = classifyDocument(name);

	if (kind === "image") {
		root.innerHTML = header + `<img class="doc-image" src="${escapeAttr(src)}" alt="${escapeAttr(name)}" />`;
		return;
	}
	if (kind === "pdf") {
		root.innerHTML = header + `<iframe class="doc-pdf" src="${escapeAttr(src)}" title="${escapeAttr(name)}"></iframe>`;
		return;
	}
	if (kind === "other") {
		root.innerHTML = header + `<p class="doc-note">No inline preview for this file type. Use Download above.</p>`;
		return;
	}

	let text;
	try {
		const resp = await fetch(src);
		if (!resp.ok) throw new Error("HTTP " + resp.status);
		text = await resp.text();
	} catch (e) {
		renderError(root, "Could not load this document.");
		return;
	}
	const bodyHtml = kind === "markdown"
		? `<div class="msg-body doc-md">${renderMarkdown(text)}</div>`
		: highlightCode(text, name);
	root.innerHTML = header + bodyHtml;
}

main();
