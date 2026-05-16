package io.github.johnjanthony.switchboard.shared

import android.content.Context
import android.util.Log
import androidx.credentials.ClearCredentialStateRequest
import androidx.credentials.CredentialManager
import androidx.credentials.CustomCredential
import androidx.credentials.GetCredentialRequest
import com.google.android.libraries.identity.googleid.GetGoogleIdOption
import com.google.android.libraries.identity.googleid.GoogleIdTokenCredential
import com.google.firebase.auth.FirebaseAuth
import com.google.firebase.auth.GoogleAuthProvider
import kotlinx.coroutines.tasks.await

object GoogleAuthHelper {
    private const val TAG = "GoogleAuthHelper"
    
    // TODO: Replace this with your Web Client ID from Google Cloud Console
    const val WEB_CLIENT_ID = "1005645832286-n3mo6ednuq5a9r8gcfec58vpqp9870ir.apps.googleusercontent.com"

    /**
     * Triggers the Google Sign-In flow using Credential Manager.
     * @param context The context to launch the UI from.
     */
    suspend fun signInWithGoogle(context: Context): Boolean {
        if (WEB_CLIENT_ID == "YOUR_WEB_CLIENT_ID_HERE") {
            Log.e(TAG, "WEB_CLIENT_ID is not configured. Please update it in GoogleAuthHelper.kt")
            return false
        }
        val credentialManager = CredentialManager.create(context)

        val googleIdOption: GetGoogleIdOption = GetGoogleIdOption.Builder()
            .setFilterByAuthorizedAccounts(false)
            .setServerClientId(WEB_CLIENT_ID)
            .setAutoSelectEnabled(true)
            .build()

        val request = GetCredentialRequest.Builder()
            .addCredentialOption(googleIdOption)
            .build()

        return try {
            val result = credentialManager.getCredential(context, request)
            val credential = result.credential 

            val googleIdTokenCredential: GoogleIdTokenCredential? = if (credential is GoogleIdTokenCredential) {
                credential
            } else if (credential is CustomCredential && credential.type == GoogleIdTokenCredential.TYPE_GOOGLE_ID_TOKEN_CREDENTIAL) {
                GoogleIdTokenCredential.createFrom(credential.data)
            } else {
                Log.e(TAG, "Unexpected credential type from Credential Manager: ${credential.type}. Data: ${credential.data}")
                null
            }

            if (googleIdTokenCredential != null) {
                val googleIdToken = googleIdTokenCredential.idToken
                val authCredential = GoogleAuthProvider.getCredential(googleIdToken, null)
                FirebaseAuth.getInstance().signInWithCredential(authCredential).await()
                true
            } else {
                Log.e(TAG, "Could not extract Google ID Token Credential from result.")
                false
            }
        } catch (e: Exception) {
            Log.e(TAG, "Google Sign-In failed", e)
            false
        }
    }

    /**
     * Signs out the user from Firebase and clears credentials from Credential Manager.
     */
    suspend fun signOut(context: Context) {
        FirebaseAuth.getInstance().signOut()
        val credentialManager = CredentialManager.create(context)
        credentialManager.clearCredentialState(ClearCredentialStateRequest())
    }
}
