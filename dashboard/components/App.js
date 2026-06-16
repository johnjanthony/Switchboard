import { html } from "../vendor/htm-preact.js";
import { StatusBar } from "./StatusBar.js";
import { ConversationList } from "./ConversationList.js";
import { ConversationDetail } from "./ConversationDetail.js";
import { CommandRail } from "./CommandRail.js";

// Dragging the left resizer narrower than this (well past the 180px min width)
// collapses the rail entirely instead of sticking at the min.
const LEFT_COLLAPSE_AT = 120;

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

	// Drag the left/center boundary to resize the conversation list. The move is
	// driven by window listeners so it keeps working across re-renders, and the
	// width is clamped + persisted inside store.setLeftWidth.
	const startLeftResize = (e) => {
		e.preventDefault();
		const startX = e.clientX;
		const startW = store.getState().ui.leftWidth;
		const onUp = () => {
			window.removeEventListener("mousemove", onMove);
			window.removeEventListener("mouseup", onUp);
			document.body.classList.remove("resizing-col");
		};
		const onMove = (ev) => {
			const targetW = startW + (ev.clientX - startX);
			// Pulling well past the min width collapses the rail entirely instead of
			// sticking at the min, mirroring the collapse toggle. End the drag so
			// repeated move events cannot toggle it back open.
			if (targetW < LEFT_COLLAPSE_AT) {
				store.setLeftCollapsed(true);
				onUp();
				return;
			}
			store.setLeftWidth(targetW);
		};
		document.body.classList.add("resizing-col");
		window.addEventListener("mousemove", onMove);
		window.addEventListener("mouseup", onUp);
	};

	return html`
		<div class="app-root">
			<${StatusBar} store=${store} />
			<${AdminStrip} notifications=${state.adminNotifications} />
			<div class=${shellClass} style=${"--left-rail-width:" + state.ui.leftWidth + "px"}>
				<${ConversationList} store=${store} />
				<${ConversationDetail} store=${store} />
				<${CommandRail} store=${store} />
				${state.ui.leftCollapsed
					? null
					: html`<div class="left-resizer" title="Drag to resize" onMouseDown=${startLeftResize}></div>`}
			</div>
		</div>
	`;
}
