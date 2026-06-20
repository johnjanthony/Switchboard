// Firebase Web SDK wrapper. The only module that imports the SDK.
// SDK functions are aliased on import (fbXxx) so this module's own exports
// (onValue, onChildAdded, ...) never self-recurse. Bare specifiers resolve
// through the importmap in index.html (Firebase Web SDK 10.12.5, gstatic ESM).

import { initializeApp } from 'firebase-app';
import {
	getAuth,
	GoogleAuthProvider,
	signInWithPopup,
	onAuthStateChanged as fbOnAuthStateChanged,
} from 'firebase-auth';
import {
	getDatabase,
	ref as fbRef,
	push as fbPush,
	set as fbSet,
	update as fbUpdate,
	onValue as fbOnValue,
	onChildAdded as fbOnChildAdded,
	onChildChanged as fbOnChildChanged,
	onChildRemoved as fbOnChildRemoved,
} from 'firebase-database';

let app = null;
let auth = null;
let database = null;

export function initFirebase(config) {
	app = initializeApp(config);
	auth = getAuth(app);
	database = getDatabase(app);
	return app;
}

export function signIn() {
	return signInWithPopup(auth, new GoogleAuthProvider());
}

export function onAuth(cb) {
	return fbOnAuthStateChanged(auth, cb);
}

export function onValue(path, cb) {
	return fbOnValue(fbRef(database, path), (snapshot) => cb(snapshot.val(), snapshot.key));
}

export function onChildAdded(path, cb) {
	return fbOnChildAdded(fbRef(database, path), (snapshot) => cb(snapshot.val(), snapshot.key));
}

export function onChildChanged(path, cb) {
	return fbOnChildChanged(fbRef(database, path), (snapshot) => cb(snapshot.val(), snapshot.key));
}

export function onChildRemoved(path, cb) {
	return fbOnChildRemoved(fbRef(database, path), (snapshot) => cb(snapshot.val(), snapshot.key));
}

export function pushValue(path, value) {
	return fbPush(fbRef(database, path), value);
}

export function setValue(path, value) {
	return fbSet(fbRef(database, path), value);
}

export function updateValue(path, value) {
	return fbUpdate(fbRef(database, path), value);
}

export function nowIso() {
	return new Date().toISOString();
}
