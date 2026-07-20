package io.github.johnjanthony.switchboard

import android.app.Application
import androidx.lifecycle.DefaultLifecycleObserver
import androidx.lifecycle.LifecycleOwner
import androidx.lifecycle.ProcessLifecycleOwner
import com.google.firebase.database.FirebaseDatabase
import com.google.firebase.messaging.FirebaseMessaging
import io.github.johnjanthony.switchboard.fcm.BaseSwitchboardMessagingService

class SwitchboardApplication : Application() {
	override fun onCreate() {
		super.onCreate()
		// Enable local persistence for an "offline-first" experience and faster UI updates
		FirebaseDatabase.getInstance().setPersistenceEnabled(true)
		// Register notification channels at app start so users can adjust them in OS
		// Settings before any push arrives.
		BaseSwitchboardMessagingService.ensureChannels(this)
		// Subscribe to FCM topics. The server publishes via topic, so without these
		// calls no FCM pushes are delivered. Restored after commit c8f932e
		// ("unified channel routing") removed them; pre-existing topic subscriptions
		// on existing installs masked the regression until a clean re-install
		// wiped them.
		FirebaseMessaging.getInstance().subscribeToTopic("questions")
		FirebaseMessaging.getInstance().subscribeToTopic("notifications")

		// Track whole-app foreground state so the shared view model can gate
		// server-visible side effects (unread zeroing) on the user actually
		// looking at the app. ON_START/ON_STOP fire for the whole process, so
		// configuration changes do not flap the flag.
		ProcessLifecycleOwner.get().lifecycle.addObserver(object : DefaultLifecycleObserver {
			override fun onStart(owner: LifecycleOwner) { AppForeground.isForeground = true }
			override fun onStop(owner: LifecycleOwner) { AppForeground.isForeground = false }
		})
	}
}
