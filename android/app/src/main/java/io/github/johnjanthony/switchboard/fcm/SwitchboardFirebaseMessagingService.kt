package io.github.johnjanthony.switchboard.fcm

import io.github.johnjanthony.switchboard.MainActivity

/** Phone FCM service; all handling lives in the shared base. */
class SwitchboardFirebaseMessagingService : BaseSwitchboardMessagingService() {
	override val mainActivityClass: Class<*> = MainActivity::class.java
}
