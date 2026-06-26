import { html, useState } from "../vendor/htm-preact.js";
import * as fb from "../firebase.js";
import { isActive, globalPendingCount, oldestPendingAgeSeconds } from "../derive.js";
import { awayOnCmd, awayOffCmd, spawnFreshCmd } from "../commands.js";
import { requestStatus, statusDotClass } from "../statusControl.js";

function push(cmd) {
	fb.pushValue(cmd.path, cmd.value);
}

function healthLampClass(health) {
	if (!health.reachable) return "lamp lamp-red";
	return health.healthy ? "lamp lamp-green" : "lamp lamp-amber";
}

function fmtAge(seconds) {
	if (seconds == null) return "-";
	const s = Math.floor(seconds);
	if (s < 60) return `${s}s`;
	if (s < 3600) return `${Math.floor(s / 60)}m`;
	return `${Math.floor(s / 3600)}h`;
}

// Open a fresh line. The one global action that doesn't act on an existing line,
// so it lives in the operator header rather than on a conversation.
function SpawnDialog({ onClose }) {
	const [surface, setSurface] = useState("windows");
	const [project, setProject] = useState("");
	const [prompt, setPrompt] = useState("");
	const [target, setTarget] = useState("");
	const submit = () => {
		push(spawnFreshCmd({
			surface,
			project,
			prompt: prompt || undefined,
			targetConversationId: target || undefined
		}, fb.nowIso));
		onClose();
	};
	return html`
		<div class="dialog" role="dialog">
			<h3>Open a line</h3>
			<label>Surface
				<select value=${surface} onChange=${(e) => setSurface(e.target.value)}>
					<option value="windows">windows</option>
					<option value="wsl">wsl</option>
				</select>
			</label>
			<label>Project path
				<input value=${project} onInput=${(e) => setProject(e.target.value)} /></label>
			<label>Opening prompt (optional)
				<textarea value=${prompt} onInput=${(e) => setPrompt(e.target.value)}></textarea></label>
			<label>Target conversation id (optional)
				<input value=${target} onInput=${(e) => setTarget(e.target.value)} /></label>
			<div class="dialog-actions">
				<button onClick=${submit}>Open line</button>
				<button class="ghost" onClick=${onClose}>Cancel</button>
			</div>
		</div>
	`;
}

// Turning away OFF is never a bare boolean when questions are waiting: the
// operator chooses how to resolve them. Triggered from the away pill below.
function AwayOffDialog({ store, onClose }) {
	const state = store.getState();
	const pendingCount = globalPendingCount(state.conversations);
	const [defaultText, setDefaultText] = useState("");

	if (pendingCount === 0) {
		const confirm = () => {
			push(awayOffCmd({}, fb.nowIso));
			onClose();
		};
		return html`
			<div class="dialog" role="dialog">
				<h3>Turn away off</h3>
				<p>No questions are waiting. Turn away off now?</p>
				<div class="dialog-actions">
					<button onClick=${confirm}>Turn off</button>
					<button class="ghost" onClick=${onClose}>Cancel</button>
				</div>
			</div>
		`;
	}

	const sendDefault = () => {
		push(awayOffCmd({ decision: "send_default", defaultText }, fb.nowIso));
		onClose();
	};
	const skip = () => {
		push(awayOffCmd({ decision: "skip" }, fb.nowIso));
		onClose();
	};
	return html`
		<div class="dialog" role="dialog">
			<h3>Turn away off</h3>
			<p>${pendingCount} question(s) are still waiting. Choose how to resolve them:</p>
			<label>Default reply
				<textarea value=${defaultText} onInput=${(e) => setDefaultText(e.target.value)}></textarea></label>
			<div class="dialog-actions">
				<button onClick=${sendDefault} disabled=${!defaultText.trim()}>Send default to all</button>
				<button class="ghost" onClick=${skip}>Skip (leave unanswered)</button>
				<button class="ghost" onClick=${onClose}>Cancel</button>
			</div>
		</div>
	`;
}

function QuotaReadout({ quota }) {
	if (!quota) return null;
	const pct = (w) => (w && w.pct != null ? Math.round(Number(w.pct) * 100) + "%" : "-");
	return html`
		<span class="quota-readout" title="Plan usage (5h / 7d)">
			<span class="count">5h <b>${pct(quota.session)}</b></span>
			<span class="count">7d <b>${pct(quota.weekly)}</b></span>
		</span>
	`;
}

function ClaudeStatusControl({ status }) {
	const s = status || { watch_state: "idle", button: "check", level: "operational", description: "", incidents: [] };
	const label = s.button === "stop" ? "Stop" : s.button === "clear" ? "Clear" : "Check";
	const action = s.button === "check" ? "check" : "stop";
	const title = (s.description || "Claude status") + ((s.incidents && s.incidents.length) ? " - " + s.incidents.join("; ") : "");
	return html`
		<span class="claude-status" title=${title}>
			${s.watch_state !== "idle" ? html`<span class=${statusDotClass(s.level)}></span>` : null}
			<span class="claude-status-label">Claude</span>
			<button class="claude-status-btn" onClick=${() => requestStatus(action)}>${label}</button>
		</span>
	`;
}

export function StatusBar({ store }) {
	const state = store.getState();
	const convs = state.conversations;
	const [spawnOpen, setSpawnOpen] = useState(false);

	const activeCount = Object.values(convs)
		.filter((c) => c && c.meta && isActive(c.meta)).length;
	const pendingCount = globalPendingCount(convs);
	const oldest = oldestPendingAgeSeconds(
		state.pendingsFlat, state.messageTimestampResolver, Date.now());

	const awayOn = state.globalAway;
	const onAwayPill = () => {
		if (awayOn) {
			// Turning away OFF opens the dialog (resolves the pendings decision).
			store.setAwayOffDialogOpen(true);
		} else {
			const { path, value } = awayOnCmd(fb.nowIso);
			fb.pushValue(path, value);
		}
	};

	return html`
		<div>
			<div class="status-bar">
				<button
					class=${"away-pill " + (awayOn ? "away-on" : "away-off")}
					onClick=${onAwayPill}
					title="Toggle global away mode"
				>Away ${awayOn ? "ON" : "OFF"}</button>
				<span class="status-counts">
					<span class="count"><b>${activeCount}</b> active</span>
					<span class="count lit"><b>${pendingCount}</b> lit</span>
					<span class="count">oldest <b>${fmtAge(oldest)}</b></span>
				</span>
				<span class="status-health">
					<span class=${healthLampClass(state.health)} title="Server health"></span>
					${state.health.totalAnswered != null
						? html`<span class="count">${state.health.totalAnswered} answered</span>`
						: null}
					<span class=${"wsl-indicator " + (state.wslAvailable ? "wsl-on" : "wsl-off")}
						title="WSL availability">WSL ${state.wslAvailable ? "on" : "off"}</span>
					<${QuotaReadout} quota=${state.widget.quota} />
					<${ClaudeStatusControl} status=${state.widget.status} />
				</span>
				<button class="open-line-btn" onClick=${() => setSpawnOpen(true)}>Open line</button>
			</div>
			<div class="header-dialogs">
				${spawnOpen ? html`<${SpawnDialog} onClose=${() => setSpawnOpen(false)} />` : null}
				${state.ui.awayOffDialogOpen
					? html`<${AwayOffDialog} store=${store} onClose=${() => store.setAwayOffDialogOpen(false)} />`
					: null}
			</div>
		</div>
	`;
}
