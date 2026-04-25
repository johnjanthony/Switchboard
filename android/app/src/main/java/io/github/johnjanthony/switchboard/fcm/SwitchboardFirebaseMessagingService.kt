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
		const val EXTRA_AGENT_ID = "agent_id"
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
		val title = remoteMessage.notification?.title ?: "Switchboard"
		val body = remoteMessage.notification?.body ?: return
		val channelId = remoteMessage.data["channel_id"]
		val messageType = remoteMessage.data["sb_message_type"] ?: "notify"

		// Warm up database connection and sync the specific channel immediately
		if (channelId != null) {
			com.google.firebase.database.FirebaseDatabase.getInstance()
				.getReference("sessions/$channelId")
				.keepSynced(true)
		}

		showNotification(title, body, channelId, messageType)
	}

	override fun onNewToken(token: String) {
		// Topic-based messaging — token not needed by server
	}

	private fun showNotification(title: String, body: String, channelId: String?, messageType: String) {
		val intent = Intent(this, MainActivity::class.java).apply {
			addFlags(Intent.FLAG_ACTIVITY_CLEAR_TOP)
			if (channelId != null) putExtra(EXTRA_AGENT_ID, channelId)
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

		val notification = NotificationCompat.Builder(this, notificationChannelId)
			.setSmallIcon(android.R.drawable.ic_dialog_info)
			.setContentTitle(title)
			.setContentText(body)
			.setStyle(NotificationCompat.BigTextStyle().bigText(body))
			.setAutoCancel(true)
			.setPriority(priority)
			.setContentIntent(pendingIntent)
			.build()

		val manager = getSystemService(Context.NOTIFICATION_SERVICE) as NotificationManager
		ensureChannels(this)
		manager.notify(notificationId.getAndIncrement(), notification)
	}
}
