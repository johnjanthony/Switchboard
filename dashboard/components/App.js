import { html } from "../vendor/htm-preact.js";
import { StatusBar } from "./StatusBar.js";
import { ConversationList } from "./ConversationList.js";
import { ConversationDetail } from "./ConversationDetail.js";
import { CommandRail } from "./CommandRail.js";

function AdminStrip({ notifications }) {
	const rows = Object.entries(notifications || {})
		.map(([key, n]) => ({ key, n: n || {} }))
		.sort((a, b) => String(b.n.timestamp || "").localeCompare(String(a.n.timestamp || "")));
	if (rows.length === 0) {
		return null;
	}
	return html`
		<div class="admin-strip">
			${rows.map(({ key, n }) => html`
				<div class="admin-note" key=${key}>
					<span class="admin-note-text">${n.text}</span>
					<span class="admin-note-time">${n.timestamp || ""}</span>
				</div>
			`)}
		</div>
	`;
}

export function App({ store }) {
	const state = store.getState();

	if (!state.authed) {
		return html`
			<div class="signin-gate">
				<h1>Switchboard Operator</h1>
				<p class="signin-sub">Sign in with your Google account to continue.</p>
				${state.authError
					? html`<p class="signin-error" role="alert">${state.authError}</p>`
					: null}
				<button class="signin-retry" onClick=${() => store.retrySignIn()}>Sign in</button>
			</div>
		`;
	}

	const shellClass = [
		"shell",
		state.ui.leftCollapsed ? "left-collapsed" : "",
		state.ui.rightCollapsed ? "right-collapsed" : ""
	].filter(Boolean).join(" ");

	return html`
		<div class="app-root">
			<${StatusBar} store=${store} />
			<${AdminStrip} notifications=${state.adminNotifications} />
			<div class=${shellClass}>
				<${ConversationList} store=${store} />
				<${ConversationDetail} store=${store} />
				<${CommandRail} store=${store} />
			</div>
		</div>
	`;
}
