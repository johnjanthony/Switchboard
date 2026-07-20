package io.github.johnjanthony.switchboard

/**
 * Schemes a markdown link may open via ACTION_VIEW. Agents relay tool and web
 * output, so message markdown can carry hostile tap targets (tel:, sms:,
 * market:, intent:); only plainly-safe schemes pass and everything else
 * no-ops. Deliberately STRICTER than Operator's markdown-it default (a
 * javascript/vbscript/file/data denylist that would still let tel: through) -
 * see dashboard/markdown.js's header comment.
 */
private val ALLOWED_LINK_SCHEMES = setOf("http", "https", "mailto")

/** True when [link] carries an explicitly allowed scheme. Scheme-less and
 *  fragment links are not openable URIs and return false. */
fun isAllowedLinkScheme(link: String): Boolean {
	val scheme = link.substringBefore(':', "").trim().lowercase()
	return scheme in ALLOWED_LINK_SCHEMES
}
