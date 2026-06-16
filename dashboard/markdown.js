// Tiny, dependency-free Markdown subset renderer for transcript messages.
// Order matters: escape ALL HTML first so no raw tag survives, then pull out
// fenced and inline code (protected from further inline rules via placeholders),
// then apply bold/italic/link, then paragraph/line-break assembly.
function escapeHtml(s) {
	return s
		.replace(/&/g, "&amp;")
		.replace(/</g, "&lt;")
		.replace(/>/g, "&gt;")
		.replace(/"/g, "&quot;");
}

// Allowlist link schemes: permit http(s)/mailto and schemeless (relative or
// anchor) links; reject javascript:, data:, and any other scheme so a crafted
// link cannot smuggle script execution through an <a href>.
function isSafeHref(href) {
	const scheme = /^([a-z][a-z0-9+.-]*):/i.exec(href);
	if (!scheme) return true;
	const s = scheme[1].toLowerCase();
	return s === "http" || s === "https" || s === "mailto";
}

export function renderMarkdown(text) {
	if (text == null) return "";
	let src = escapeHtml(String(text));

	// Protect code so inline rules below do not touch its contents.
	const stash = [];
	const protect = (html) => {
		const token = `\x00${stash.length}\x00`;
		stash.push(html);
		return token;
	};

	// Fenced code blocks: ```\n...\n```
	src = src.replace(/```\n?([\s\S]*?)```/g, (_m, body) => {
		const trimmed = body.replace(/\n$/, "");
		return protect(`<pre><code>${trimmed}</code></pre>`);
	});
	// Inline code: `...`
	src = src.replace(/`([^`\n]+)`/g, (_m, body) => protect(`<code>${body}</code>`));

	// Links: [text](href) - href is already HTML-escaped by escapeHtml above.
	// Only emit a live link for an allowlisted scheme; an unsafe scheme (e.g.
	// javascript:) renders as the plain label text instead.
	src = src.replace(/\[([^\]]+)\]\(([^)\s]+)\)/g, (_m, label, href) =>
		isSafeHref(href)
			? `<a href="${href}" target="_blank" rel="noopener noreferrer">${label}</a>`
			: label);
	// Bold then italic (bold first so ** is not eaten by the * rule).
	src = src.replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>");
	src = src.replace(/\*([^*]+)\*/g, "<em>$1</em>");

	// Paragraphs from blank-line-separated blocks; single newlines become <br />.
	const html = src
		.split(/\n{2,}/)
		.map((block) => {
			const b = block.trim();
			if (b === "") return "";
			// A block that is purely a protected code token renders bare (no <p>).
			if (/^\x00\d+\x00$/.test(b)) return b;
			return `<p>${b.replace(/\n/g, "<br />")}</p>`;
		})
		.filter(Boolean)
		.join("\n");

	// Restore protected code spans/blocks.
	return html.replace(/\x00(\d+)\x00/g, (_m, i) => stash[Number(i)]);
}
