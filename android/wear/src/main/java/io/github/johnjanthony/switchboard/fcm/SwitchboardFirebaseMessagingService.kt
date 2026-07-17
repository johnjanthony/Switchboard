package io.github.johnjanthony.switchboard.fcm

import androidx.core.app.NotificationCompat
import io.github.johnjanthony.switchboard.MainActivity

/** Wear FCM service; adds the wearable decoration to the shared base's notification. */
class SwitchboardFirebaseMessagingService : BaseSwitchboardMessagingService() {
	override val mainActivityClass: Class<*> = MainActivity::class.java

	override fun decorate(builder: NotificationCompat.Builder, messageId: String?) {
		val wearableExtender = NotificationCompat.WearableExtender()
			.setHintContentIntentLaunchesActivity(true) // Prioritize launching the contentIntent on tap
		if (messageId != null) {
			wearableExtender.dismissalId = messageId
		}
		builder.setLocalOnly(true) // Prevent bridging to/from phone
			.extend(wearableExtender)
	}
}
