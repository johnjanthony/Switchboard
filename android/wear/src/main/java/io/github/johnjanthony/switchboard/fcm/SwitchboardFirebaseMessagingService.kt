package io.github.johnjanthony.switchboard.fcm

import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.content.Context
import android.content.Intent
import android.os.Build
import androidx.core.app.NotificationCompat
import com.google.firebase.messaging.FirebaseMessagingService
import com.google.firebase.messaging.RemoteMessage
import io.github.johnjanthony.switchboard.MainActivity
import java.util.concurrent.atomic.AtomicInteger

class SwitchboardFirebaseMessagingService : FirebaseMessagingService() {

	companion object {
		const val CHANNEL_QUESTIONS = "switchboard_questions"
		const val CHANNEL_DOCUMENTS = "switchboard_documents"
		const val CHANNEL_UPDATES = "switchboard_updates"
		// Values intentionally match the server's FCM data keys (server/firebase.py).
		// When the app is in foreground, our showNotification() attaches these as
		// intent extras. When the app is in background/killed, Android handles the
		// notification itself and attaches the FCM data fields as intent extras
		// using the original key names — so we must read the same names in both paths.
		// EXTRA_AGENT_ID carries conv_id (server standardized on this in Fix 9).
		const val EXTRA_AGENT_ID = "conv_id"
		const val EXTRA_MESSAGE_ID = "message_id"
		private val notificationId = AtomicInteger(1)

		fun ensureChannels(context: Context) {
			if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
				val manager = context.getSystemService(Context.NOTIFICATION_SERVICE) as NotificationManager
				manager.createNotificationChannel(
					NotificationChannel(CHANNEL_QUESTIONS, "Questions", NotificationManager.IMPORTANCE_HIGH).apply {
						description = "Agent questions requiring a response"
					}
				)
				manager.createNotificationChannel(
					NotificationChannel(CHANNEL_DOCUMENTS, "Documents", NotificationManager.IMPORTANCE_DEFAULT).apply {
						description = "Documents delivered by agents"
					}
				)
				manager.createNotificationChannel(
					NotificationChannel(CHANNEL_UPDATES, "Updates", NotificationManager.IMPORTANCE_DEFAULT).apply {
						description = "Agent status updates and collab relay messages"
					}
				)
			}
		}
	}

	override fun onMessageReceived(remoteMessage: RemoteMessage) {
		// Server sends data-only messages so this runs in foreground, background,
		// and killed states. Title/body live in the data dict, not remoteMessage.notification.
		val title = remoteMessage.data["title"] ?: "Switchboard"
		val body = remoteMessage.data["body"] ?: return
		val convId = remoteMessage.data["conv_id"]
		val messageId = remoteMessage.data["message_id"]
		val messageType = remoteMessage.data["sb_message_type"] ?: "notify"

		// Warm up database connection and sync the specific conversation immediately
		if (convId != null) {
			com.google.firebase.database.FirebaseDatabase.getInstance()
				.getReference("conversations/$convId")
				.keepSynced(true)
			com.google.firebase.database.FirebaseDatabase.getInstance()
				.getReference("messages/$convId")
				.keepSynced(true)
		}

		showNotification(title, body, convId, messageId, messageType)
	}

	override fun onNewToken(token: String) {
		// Topic-based messaging — token not needed by server
	}

	private fun showNotification(title: String, body: String, channelId: String?, messageId: String?, messageType: String) {
		val intent = Intent(this, MainActivity::class.java).apply {
			addFlags(Intent.FLAG_ACTIVITY_CLEAR_TOP or Intent.FLAG_ACTIVITY_SINGLE_TOP)
			if (channelId != null) putExtra(EXTRA_AGENT_ID, channelId)
			if (messageId != null) putExtra(EXTRA_MESSAGE_ID, messageId)
		}
		val pendingIntent = PendingIntent.getActivity(
			this, notificationId.get(), intent,
			PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT
		)

		val notificationChannelId = when (messageType) {
			"question" -> CHANNEL_QUESTIONS
			"document" -> CHANNEL_DOCUMENTS
			else -> CHANNEL_UPDATES
		}
		val priority = if (messageType == "question") NotificationCompat.PRIORITY_HIGH else NotificationCompat.PRIORITY_DEFAULT

		val wearableExtender = NotificationCompat.WearableExtender()
			.setHintContentIntentLaunchesActivity(true) // Prioritize launching the contentIntent on tap
		
		if (messageId != null) {
			wearableExtender.dismissalId = messageId
		}

		val notification = NotificationCompat.Builder(this, notificationChannelId)
			.setSmallIcon(android.R.drawable.ic_dialog_info)
			.setContentTitle(title)
			.setContentText(body)
			.setStyle(NotificationCompat.BigTextStyle().bigText(body))
			.setAutoCancel(true)
			.setPriority(priority)
			.setContentIntent(pendingIntent)
			.setLocalOnly(true) // Prevent bridging to/from phone
			.extend(wearableExtender)
			.build()

		val manager = getSystemService(Context.NOTIFICATION_SERVICE) as NotificationManager
		ensureChannels(this)
		manager.notify(notificationId.getAndIncrement(), notification)
	}
}
