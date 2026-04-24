package io.github.johnjanthony.switchboard

import android.app.Application
import com.google.firebase.database.FirebaseDatabase

class SwitchboardApplication : Application() {
    override fun onCreate() {
        super.onCreate()
        // Enable local persistence for an "offline-first" experience and faster UI updates
        FirebaseDatabase.getInstance().setPersistenceEnabled(true)
    }
}
