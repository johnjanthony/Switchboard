// Renders the download affordance for a document message, mirroring the Android
// FilePill: a clickable chip linking to the Firebase Storage url, labeled with
// the filename. Returns "" for any message that carries no fetchable file so
// non-document bubbles render unchanged.
//
// The output is injected via dangerouslySetInnerHTML (the transcript body is
// already raw HTML), so every interpolated value is escaped here.

export function documentPillHtml(msg, convId, msgId) {
	const url = msg && msg.url;
	const filename = msg && msg.filename;
	if (!url || !filename || !convId || !msgId) return "";
	const href = "doc-view.html?conv=" + encodeURIComponent(convId)
		+ "&msg=" + encodeURIComponent(msgId)
		+ "&name=" + encodeURIComponent(filename);
	const label = escapeText(fileLeaf(filename));
	return `<a class="file-pill" href="${escapeAttr(href)}" target="_blank" rel="noopener noreferrer">📎 ${label}</a>`;
}

// Last path segment, matching Android's leafName (trailing slash trimmed).
function fileLeaf(name) {
	const s = String(name == null ? "" : name).replace(/\/+$/, "");
	const cut = s.lastIndexOf("/");
	return cut === -1 ? s : s.slice(cut + 1);
}

function escapeText(s) {
	return String(s == null ? "" : s)
		.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
}

function escapeAttr(s) {
	return escapeText(s).replace(/"/g, "&quot;").replace(/'/g, "&#39;");
}

const IMAGE_EXTS = new Set(["png", "jpg", "jpeg", "gif", "webp", "svg", "bmp", "ico"]);
const TEXT_EXTS = new Set([
	"txt", "log", "json", "js", "mjs", "cjs", "ts", "tsx", "jsx", "py", "java",
	"c", "cc", "cpp", "h", "hpp", "cs", "sh", "bash", "zsh", "yml", "yaml", "xml",
	"html", "htm", "css", "scss", "kt", "kts", "sql", "toml", "ini", "cfg", "conf",
	"csv", "tsv", "rb", "go", "rs", "php", "pl", "r", "scala", "swift", "gradle",
	"properties", "diff", "patch", "env", "dockerfile", "makefile",
]);

// Pick the render mode for a document by its filename extension. "text" covers
// code and plaintext (the preview page highlights it); markdown/image/pdf are
// their own modes; everything else falls back to a download.
export function classifyDocument(filename) {
	const name = String(filename || "").toLowerCase();
	const ext = name.includes(".") ? name.split(".").pop() : "";
	if (ext === "md" || ext === "markdown") return "markdown";
	if (IMAGE_EXTS.has(ext)) return "image";
	if (ext === "pdf") return "pdf";
	if (TEXT_EXTS.has(ext)) return "text";
	return "other";
}
