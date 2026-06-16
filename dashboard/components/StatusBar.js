import { html } from "../vendor/htm-preact.js";
import * as fb from "../firebase.js";
import { isActive, globalPendingCount, oldestPendingAgeSeconds } from "../derive.js";
import { awayOnCmd } from "../commands.js";

function healthDotClass(health) {
	if (!health.reachable) return "dot dot-red";
	return health.healthy ? "dot dot-green" : "dot dot-amber";
}

function fmtAge(seconds) {
	if (seconds == null) return "-";
	const s = Math.floor(seconds);
	if (s < 60) return `${s}s`;
	if (s < 3600) return `${Math.floor(s / 60)}m`;
	return `${Math.floor(s / 3600)}h`;
}

export function StatusBar({ store }) {
	const state = store.getState();
	const convs = state.conversations;

	const activeCount = Object.values(convs)
		.filter((c) => c && c.meta && isActive(c.meta)).length;
	const pendingCount = globalPendingCount(convs);
	const oldest = oldestPendingAgeSeconds(
		state.pendingsFlat, state.messageTimestampResolver, Date.now());

	const awayOn = state.globalAway;
	const onAwayPill = () => {
		if (awayOn) {
			// Turning away OFF is never a bare boolean: open the dialog, which
			// resolves the zero-pending vs pendings decision (CommandRail renders it).
			store.setAwayOffDialogOpen(true);
		} else {
			const { path, value } = awayOnCmd(fb.nowIso);
			fb.pushValue(path, value);
		}
	};

	return html`
		<div class="status-bar">
			<button
				class=${"away-pill " + (awayOn ? "away-on" : "away-off")}
				onClick=${onAwayPill}
				title="Toggle global away mode"
			>Away ${awayOn ? "ON" : "OFF"}</button>
			<span class="status-counts">
				<span class="count">${activeCount} active</span>
				<span class="count">${pendingCount} pending</span>
				<span class="count">oldest ${fmtAge(oldest)}</span>
			</span>
			<span class="status-health">
				<span class=${healthDotClass(state.health)} title="Server health"></span>
				${state.health.totalAnswered != null
					? html`<span class="count">${state.health.totalAnswered} answered</span>`
					: null}
			</span>
			<span class=${"wsl-indicator " + (state.wslAvailable ? "wsl-on" : "wsl-off")}
				title="WSL availability">WSL ${state.wslAvailable ? "on" : "off"}</span>
		</div>
	`;
}
