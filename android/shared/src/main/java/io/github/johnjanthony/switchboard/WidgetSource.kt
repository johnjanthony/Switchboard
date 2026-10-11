package io.github.johnjanthony.switchboard

import io.github.johnjanthony.switchboard.network.QuotaNode
import io.github.johnjanthony.switchboard.network.QuotaNodeWindow
import io.github.johnjanthony.switchboard.network.RegistrySession
import io.github.johnjanthony.switchboard.network.WidgetQuota
import io.github.johnjanthony.switchboard.network.WidgetQuotaWindow
import io.github.johnjanthony.switchboard.network.WidgetRing

// The widget data the screens draw, built from what the server publishes now that the Claude
// Code mod reports it: context rings from the sessions/ records, plan usage from the quota/
// node. The output types are the ones Watchtower's widget/ push used, so the screens (and the
// watch, through conversationRows) are unchanged.

private val RING_LIVE_STATES = setOf("active", "awaiting_human", "awaiting_agent")
private val RING_TERMINAL_STATES = setOf("ended", "lost")

/**
 * One ring per live session that has reported its context. A session that has not reported
 * yet gets no ring, so no 0% badge is drawn for it.
 */
fun ringsFromSessions(sessions: Map<String, RegistrySession>): Map<String, WidgetRing> =
	sessions
		.filter { (_, rec) -> rec.state !in RING_TERMINAL_STATES && rec.contextPct != null }
		.mapValues { (_, rec) ->
			WidgetRing(
				pct = rec.contextPct ?: 0.0,
				model = rec.model ?: "",
				status = if (rec.state in RING_LIVE_STATES) "live" else "idle",
				contextTokens = rec.contextTokens ?: 0L,
				window = rec.contextWindow ?: 0L,
				isError = false,
			)
		}

/** Plan usage for the screens: percent_used (0-100) becomes the 0-1 pct they draw. */
fun quotaFromNode(node: QuotaNode?): WidgetQuota? {
	if (node == null || (node.fiveHour == null && node.sevenDay == null)) return null
	return WidgetQuota(
		session = node.fiveHour?.let(::quotaWindow),
		weekly = node.sevenDay?.let(::quotaWindow),
		polledAt = node.receivedAt ?: "",
		antigravity = null,
	)
}

private fun quotaWindow(w: QuotaNodeWindow): WidgetQuotaWindow =
	WidgetQuotaWindow(pct = (w.percentUsed ?: 0.0) / 100.0, resetsAt = w.resetsAt ?: "")
