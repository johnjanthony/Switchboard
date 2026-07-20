import { html } from "../vendor/htm-preact.js";

// Persistent inline error banner for a pane (permission-denied / server-unreachable).
// Renders nothing when message is falsy. onRetry is a store action bound by the caller.
export function PaneBanner({ message, onRetry, actionLabel }) {
	if (!message) return null;
	return html`
		<div class="pane-banner" role="alert">
			<span class="pane-banner-msg">${message}</span>
			<button class="pane-banner-retry" onClick=${onRetry}>${actionLabel || "Retry"}</button>
		</div>
	`;
}
