// Shared HTML escapers for the strings the dashboard injects via
// dangerouslySetInnerHTML / innerHTML. escapeText covers element text
// content (&, <, >); escapeAttr additionally covers quote characters for
// use inside an attribute value. XSS-relevant - do NOT weaken.
export function escapeText(s) {
	return String(s == null ? "" : s)
		.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
}

export function escapeAttr(s) {
	return escapeText(s).replace(/"/g, "&quot;").replace(/'/g, "&#39;");
}
