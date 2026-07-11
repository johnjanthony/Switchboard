package io.github.johnjanthony.switchboard

import io.github.johnjanthony.switchboard.network.ChannelMessage

/**
 * Holds messages that arrive for a conversation whose row is not yet present in the
 * view-model, so a cold/partial-state message is not dropped (Firebase onChildAdded does
 * not replay for an already-attached listener). Drained when the row first appears.
 * Bounded per conversation so a permanently-unparseable node cannot grow without limit.
 * Not thread-safe: used only from the main thread.
 */
class PendingMessageBuffer(private val perConvCap: Int = 200) {
	private val byConv = mutableMapOf<String, MutableList<Pair<String, ChannelMessage>>>()

	fun buffer(convId: String, msgId: String, msg: ChannelMessage) {
		val list = byConv.getOrPut(convId) { mutableListOf() }
		val idx = list.indexOfFirst { it.first == msgId }
		if (idx >= 0) list[idx] = msgId to msg else list.add(msgId to msg)
		while (list.size > perConvCap) list.removeAt(0)
	}

	fun drain(convId: String): List<Pair<String, ChannelMessage>> =
		byConv.remove(convId) ?: emptyList()

	fun sizeOf(convId: String): Int = byConv[convId]?.size ?: 0
}
