package io.github.johnjanthony.switchboard

import io.github.johnjanthony.switchboard.network.ChannelMessage

/**
 * Reorder a message list so that any message whose `attached_to_msg_id` names another
 * message in the list is repositioned immediately after that target. Multiple messages
 * attaching to the same target preserve their original (arrival) order. Messages whose
 * target isn't in the list keep their original position.
 *
 * The input is expected to be in arrival/msgId order. The output is a new list; the
 * input is not mutated.
 */
fun applySpliceOrder(
	messages: List<Pair<String, ChannelMessage>>,
): List<Pair<String, ChannelMessage>> {
	if (messages.isEmpty()) return messages

	// Group "attached" messages by their target msgId, preserving arrival order.
	val attachmentsByTarget: Map<String, List<Pair<String, ChannelMessage>>> =
		messages
			.filter { (_, m) -> m.attached_to_msg_id != null && messages.any { it.first == m.attached_to_msg_id } }
			.groupBy { (_, m) -> m.attached_to_msg_id!! }

	// Collect the msgIds that are spliced (so we don't emit them in their original slot).
	val splicedIds: Set<String> =
		attachmentsByTarget.values.flatten().map { it.first }.toSet()

	val out = ArrayList<Pair<String, ChannelMessage>>(messages.size)
	for (entry in messages) {
		val (id, _) = entry
		if (id in splicedIds) continue  // emitted later, under its target
		out.add(entry)
		// Splice attachments under this message, recursively (so chains work).
		spliceAttachmentsUnder(id, attachmentsByTarget, out)
	}
	return out
}

private fun spliceAttachmentsUnder(
	targetId: String,
	attachmentsByTarget: Map<String, List<Pair<String, ChannelMessage>>>,
	out: MutableList<Pair<String, ChannelMessage>>,
) {
	val attached = attachmentsByTarget[targetId] ?: return
	for (a in attached) {
		out.add(a)
		// If a reply itself has attachments (chain), splice them recursively.
		spliceAttachmentsUnder(a.first, attachmentsByTarget, out)
	}
}
