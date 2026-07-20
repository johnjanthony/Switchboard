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
