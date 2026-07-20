import { html, render } from "./vendor/htm-preact.js";
import * as fb from "./firebase.js";
import * as paths from "./schema.js";
import { createStore } from "./store.js";
import { FIREBASE_CONFIG } from "./dashboard-config.js";
import { App } from "./components/App.js";
import * as statusControl from "./statusControl.js";

function consumeDeepLink(store) {
	const m = /(?:^|#)conv=([^&]+)/.exec(window.location.hash || "");
	if (m) {
		store.selectConversation(decodeURIComponent(m[1]));
	}
}

async function pollHealth(store) {
	try {
		const resp = await fetch("/healthz", { cache: "no-store" });
		if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
		const body = await resp.json();
		store.setHealth({
			reachable: true,
			healthy: body.healthy === true,
			totalAnswered: (body.pending && body.pending.total_answered) ?? null
		});
	} catch (_e) {
		store.setHealth({ reachable: false, healthy: false, totalAnswered: null });
	}
}

function main() {
	fb.initFirebase(FIREBASE_CONFIG);
	const store = createStore({
		fb, paths, storage: window.localStorage,
		nowMs: () => Date.now(), requestStatus: statusControl.requestStatus,
	});

	const mount = document.getElementById("app");
	const draw = () => render(html`<${App} store=${store} />`, mount);
	store.subscribe(draw);
	draw();

	fb.onAuth((user) => {
		if (user) {
			store.setAuthed(true, user);
			store.startGlobalListeners();
			consumeDeepLink(store);
		} else {
			store.setAuthed(false, null);
		}
	});

	window.addEventListener("hashchange", () => consumeDeepLink(store));

	pollHealth(store);
	setInterval(() => pollHealth(store), 5000);
}

main();
