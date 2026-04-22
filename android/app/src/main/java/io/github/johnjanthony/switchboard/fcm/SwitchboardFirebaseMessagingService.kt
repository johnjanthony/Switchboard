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
		const val CHANNEL_UPDATES = "switchboard_updates"
		const val EXTRA_AGENT_ID = "agent_id"
		private val notificationId = AtomicInteger(1)
	}

	override fun onMessageReceived(remoteMessage: RemoteMessage) {
		val title = remoteMessage.notification?.title ?: "Switchboard"
		val body = remoteMessage.notification?.body ?: return
		val agentId = remoteMessage.data["agent_id"]
		val isQuestion = remoteMessage.data.containsKey("request_id")
		showNotification(title, body, agentId, isQuestion)
	}

	override fun onNewToken(token: String) {
		// Topic-based messaging — token not needed by server
	}

	private fun showNotification(title: String, body: String, agentId: String?, isQuestion: Boolean) {
		val intent = Intent(this, MainActivity::class.java).apply {
			addFlags(Intent.FLAG_ACTIVITY_CLEAR_TOP)
			if (agentId != null) putExtra(EXTRA_AGENT_ID, agentId)
		}
		val pendingIntent = PendingIntent.getActivity(
			this, notificationId.get(), intent,
			PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT
		)

		val channelId = if (isQuestion) CHANNEL_QUESTIONS else CHANNEL_UPDATES
		val notification = NotificationCompat.Builder(this, channelId)
			.setSmallIcon(android.R.drawable.ic_dialog_info)
			.setContentTitle(title)
			.setContentText(body)
			.setStyle(NotificationCompat.BigTextStyle().bigText(body))
			.setAutoCancel(true)
			.setPriority(if (isQuestion) NotificationCompat.PRIORITY_HIGH else NotificationCompat.PRIORITY_DEFAULT)
			.setContentIntent(pendingIntent)
			.build()

		val manager = getSystemService(Context.NOTIFICATION_SERVICE) as NotificationManager
		ensureChannels(manager)
		manager.notify(notificationId.getAndIncrement(), notification)
	}

	private fun ensureChannels(manager: NotificationManager) {
		if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
			manager.createNotificationChannel(
				NotificationChannel(CHANNEL_QUESTIONS, "Questions", NotificationManager.IMPORTANCE_HIGH).apply {
					description = "Agent questions requiring a response"
				}
			)
			manager.createNotificationChannel(
				NotificationChannel(CHANNEL_UPDATES, "Updates", NotificationManager.IMPORTANCE_DEFAULT).apply {
					description = "Agent status updates and documents"
				}
			)
		}
	}
}
