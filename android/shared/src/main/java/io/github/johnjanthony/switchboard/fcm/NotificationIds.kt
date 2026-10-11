package io.github.johnjanthony.switchboard.fcm

/**
 * Notification id derived from the FCM message_id (the RTDB push key - present
 * on every push and globally unique). A process-lifetime counter resets after
 * the routine kill of the FCM service process, so a new question would reuse
 * id 1 and REPLACE a still-unread question in the tray - in away mode that
 * loses a pending question's only visible signal. String.hashCode is stable
 * across processes; 32-bit collisions are negligible at phone scale.
 */
fun notificationIdFor(messageId: String?, fallback: String): Int =
	(messageId ?: fallback).hashCode()

/**
 * The notification to cancel for an FCM data payload. When a question ends, by
 * any path, the server sends sb_message_type "dismiss" with the question card's
 * message_id, the id the question's notification was posted under. Null for any
 * other payload, and for a dismiss without a message_id.
 */
fun dismissalNotificationId(data: Map<String, String>): Int? {
	if (data["sb_message_type"] != "dismiss") return null
	val messageId = data["message_id"] ?: return null
	return notificationIdFor(messageId, messageId)
}
