// Non-secret Firebase web config. These values ship to every browser; the real
// access control is the RTDB security rules (scoped to John's identity in Phase 0).
// Sourced from android/app/google-services.json (project jja-switchboard).
// appId is intentionally omitted: it is only needed for Analytics (unused here);
// auth and Realtime Database work without it.
export const FIREBASE_CONFIG = {
	apiKey: "AIzaSyBzY1siw92nUcPfOg3vRH4dP4va21zlbag",
	authDomain: "jja-switchboard.firebaseapp.com",
	databaseURL: "https://jja-switchboard-default-rtdb.firebaseio.com",
	projectId: "jja-switchboard",
	storageBucket: "jja-switchboard.firebasestorage.app",
	messagingSenderId: "1005645832286"
};
