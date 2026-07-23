import { html, useState } from "../vendor/htm-preact.js";
import { isActive, globalPendingCount, oldestPendingAgeSeconds, formatAge } from "../derive.js";
import { statusDotClass } from "../statusControl.js";

function healthLampClass(health) {
	if (!health.reachable) return "lamp lamp-red";
	return health.healthy ? "lamp lamp-green" : "lamp lamp-amber";
}

// Open a fresh line. The one global action that doesn't act on an existing line,
// so it lives in the operator header rather than on a conversation.
function SpawnDialog({ store, onClose }) {
	const [surface, setSurface] = useState("windows");
	const [project, setProject] = useState("");
	const [prompt, setPrompt] = useState("");
	const [target, setTarget] = useState("");
	const canSubmit = project.trim().length > 0;
	const submit = () => {
		if (!canSubmit) return;
		store.spawnFresh({
			surface,
			project,
			prompt: prompt || undefined,
			targetConversationId: target || undefined,
		});
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
				<button onClick=${submit} disabled=${!canSubmit}>Open line</button>
				<button class="ghost" onClick=${onClose}>Cancel</button>
			</div>
			${!canSubmit ? html`<p class="resume-hint">A project path is required.</p>` : null}
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
			store.awayOff({});
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
		store.awayOff({ decision: "send_default", defaultText });
		onClose();
	};
	const skip = () => {
		store.awayOff({ decision: "skip" });
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

function formatResetAt(resetsAt) {
	if (!resetsAt) return null;
	const d = new Date(resetsAt);
	if (isNaN(d.getTime())) return null;
	const now = new Date();
	const isToday = d.toDateString() === now.toDateString();
	const timeStr = d.toLocaleTimeString([], { hour: 'numeric', minute: '2-digit', hour12: true });
	if (isToday) return timeStr;
	const dayStr = d.toLocaleDateString([], { weekday: 'short' });
	return `${dayStr} ${timeStr}`;
}

function buildWindowTooltip(label, window, durationMs) {
	if (!window || window.pct == null) return null;
	const usageFrac = Math.min(1, Math.max(0, Number(window.pct)));
	const usagePct = Math.round(usageFrac * 100);
	
	const resetsAt = window.resetsAt || window.resets_at;
	let elapsedFrac = null;
	if (resetsAt) {
		const resetMs = Date.parse(resetsAt);
		if (!isNaN(resetMs)) {
			const startMs = resetMs - durationMs;
			const nowMs = Date.now();
			elapsedFrac = Math.min(1, Math.max(0, (nowMs - startMs) / durationMs));
		}
	}

	const elapsedPct = elapsedFrac != null ? Math.round(elapsedFrac * 100) : null;
	const resetStr = formatResetAt(resetsAt);
	const labelFull = label === "5h" ? "Session (5h)" : "Weekly (7d)";
	const titleParts = [`${labelFull}`, `Usage: ${usagePct}%`];
	if (elapsedPct != null) titleParts.push(`Time elapsed: ${elapsedPct}%`);
	if (resetStr) titleParts.push(`Reset at: ${resetStr}`);
	return titleParts.join(" · ");
}

function QuotaWindowGraph({ label, window, durationMs }) {
	if (!window || window.pct == null) return null;
	const usageFrac = Math.min(1, Math.max(0, Number(window.pct)));
	const usagePct = Math.round(usageFrac * 100);
	
	const resetsAt = window.resetsAt || window.resets_at;
	let elapsedFrac = null;
	let isOverPace = false;
	if (resetsAt) {
		const resetMs = Date.parse(resetsAt);
		if (!isNaN(resetMs)) {
			const startMs = resetMs - durationMs;
			const nowMs = Date.now();
			elapsedFrac = Math.min(1, Math.max(0, (nowMs - startMs) / durationMs));
			isOverPace = usageFrac > (elapsedFrac + 0.02);
		}
	}

	const segments = Array.from({ length: 10 }, (_, i) => {
		const segStart = i * 10;
		const fillFrac = Math.min(1, Math.max(0, (usagePct - segStart) / 10));
		const colorClass = usageFrac > 0.8 ? "over" : usageFrac > 0.6 ? "warn" : "ok";
		return html`
			<div class="quota-segment">
				${fillFrac > 0 ? html`<div class=${"quota-segment-fill " + colorClass} style=${{ width: (fillFrac * 100) + "%" }}></div>` : null}
			</div>
		`;
	});

	return html`
		<div class="quota-row">
			<div class="quota-bars">
				<div class="quota-segment-track">
					${segments}
				</div>
				${elapsedFrac != null ? html`
					<div class="quota-pace-track">
						<div class=${"quota-pace-fill " + (isOverPace ? "over-pace" : "")} style=${{ width: (elapsedFrac * 100) + "%" }}></div>
					</div>
				` : null}
			</div>
		</div>
	`;
}

function groupSortKey(displayName) {
	const d = (displayName || "").toLowerCase();
	if (d.includes("claude")) return 0;
	if (d.includes("gemini")) return 1;
	return 2;
}

function formatAgyGroupName(displayName) {
	const d = (displayName || "").toLowerCase();
	if (d.includes("claude")) return "Antigravity w/ Claude";
	if (d.includes("gemini")) return "Antigravity w/ Gemini";
	return displayName || "Antigravity";
}

function isAgyGroupVisible(group) {
	if (!group) return false;
	const sPct = group.session ? Number(group.session.pct) : 0;
	const wPct = group.weekly ? Number(group.weekly.pct) : 0;
	return sPct > 0 || wPct > 0;
}

function buildPairTooltip(groupName, sessionWindow, weeklyWindow) {
	const SESSION_5H = 5 * 3600 * 1000;
	const WEEKLY_7D = 7 * 86400 * 1000;
	const t5h = buildWindowTooltip("5h", sessionWindow, SESSION_5H);
	const t7d = buildWindowTooltip("7d", weeklyWindow, WEEKLY_7D);
	const lines = [groupName, t5h, t7d].filter(Boolean);
	return lines.length > 0 ? lines.join("\n") : null;
}

function QuotaPairGraph({ groupName, sessionWindow, weeklyWindow }) {
	const SESSION_5H = 5 * 3600 * 1000;
	const WEEKLY_7D = 7 * 86400 * 1000;
	const tooltip = buildPairTooltip(groupName, sessionWindow, weeklyWindow);

	return html`
		<div class="quota-graph" title=${tooltip}>
			<${QuotaWindowGraph} label="5h" window=${sessionWindow} durationMs=${SESSION_5H} />
			<${QuotaWindowGraph} label="7d" window=${weeklyWindow} durationMs=${WEEKLY_7D} />
		</div>
	`;
}

function QuotaReadout({ quota }) {
	if (!quota) return null;

	const rawAgy = quota.antigravity || [];
	const visibleAgy = rawAgy
		.filter((g) => isAgyGroupVisible(g))
		.sort((a, b) => groupSortKey(a.display_name) - groupSortKey(b.display_name));

	const agyPairs = visibleAgy.map((g) => {
		const name = formatAgyGroupName(g.display_name);
		return html`<${QuotaPairGraph} key=${g.display_name} groupName=${name} sessionWindow=${g.session} weeklyWindow=${g.weekly} />`;
	});

	const claudeVisible = quota.session || quota.weekly;
	const claudePair = claudeVisible
		? html`<${QuotaPairGraph} key="claude" groupName="Claude Code" sessionWindow=${quota.session} weeklyWindow=${quota.weekly} />`
		: null;

	if (agyPairs.length === 0 && !claudePair) return null;

	return html`
		<div class="quota-readout">
			${agyPairs}
			${claudePair}
		</div>
	`;
}

function claudeStatusPillClass(level) {
	if (!level) return "status-cold";
	const l = String(level).toLowerCase();
	if (l === "operational" || l === "none") return "status-green";
	if (l.includes("major") || l.includes("critical") || l.includes("outage")) return "status-red";
	if (l.includes("minor") || l.includes("degraded") || l.includes("partial")) return "status-amber";
	return "status-cold";
}

function healthStatusPillClass(health) {
	if (!health || !health.reachable) return "status-red";
	return health.healthy ? "status-green" : "status-amber";
}

function ClaudeStatusControl({ status, store }) {
	const s = status || { watch_state: "idle", button: "check", level: "operational", description: "", incidents: [] };
	const isWatching = s.watch_state !== "idle" && s.button !== "check";
	const isGreen = !s.level || s.level === "operational" || s.level === "none";

	let desc = s.description || "Claude status";
	if (s.incidents && s.incidents.length > 0) {
		const incidentsText = s.incidents.join("; ");
		if (!desc || desc.toLowerCase().includes("all systems operational")) {
			desc = incidentsText;
		} else if (!desc.includes(incidentsText)) {
			desc = desc + " - " + incidentsText;
		}
	}
	const title = desc;
	const onClick = () => {
		if (!isGreen) {
			window.open("https://status.claude.com", "_blank");
			return;
		}
		const action = (s.button === "check" || s.watch_state === "idle") ? "check" : "stop";
		store.requestClaudeStatus(action);
	};
	const colorClass = isGreen ? (isWatching ? "status-green" : "status-cold") : claudeStatusPillClass(s.level);
	return html`
		<button
			class=${"claude-pill " + (isWatching ? "watching " : "idle ") + colorClass}
			onClick=${onClick}
			title=${title}
		>
			<span class=${statusDotClass(s.level)}></span>
			<span>CLAUDE</span>
		</button>
	`;
}

function SwitchboardStatusControl({ health }) {
	const reachable = health && health.reachable;
	const healthy = health && health.healthy;
	const title = !reachable
		? "Switchboard server unreachable"
		: healthy
			? "Switchboard server healthy"
			: "Switchboard server degraded";
	return html`
		<span class=${"switchboard-pill " + healthStatusPillClass(health)} title=${title}>
			<span class=${healthLampClass(health)}></span>
			<span>SWITCHBOARD</span>
		</span>
	`;
}

export function StatusBar({ store }) {
	const state = store.getState();
	const convs = state.conversations;
	const [spawnOpen, setSpawnOpen] = useState(false);
	const pendingCount = globalPendingCount(convs);
	const oldest = oldestPendingAgeSeconds(state.pendingsFlat, Date.now());

	const awayOn = state.globalAway;
	const onAwayPill = () => {
		if (awayOn) {
			// Turning away OFF opens the dialog (resolves the pendings decision).
			store.setAwayOffDialogOpen(true);
		} else {
			store.awayOn();
		}
	};

	return html`
		<div>
			<div class="status-bar">
				<button class="open-line-btn" onClick=${() => setSpawnOpen(true)} title="Open line">+</button>
				<span class="status-counts">
					${pendingCount > 0 ? html`
						<span class="count lit"><b>${pendingCount}</b> ${pendingCount === 1 ? 'question' : 'questions'}</span>
						<span class="count">age <b>${oldest == null ? "-" : formatAge(oldest)}</b></span>
					` : null}
				</span>
				<${QuotaReadout} quota=${state.widget.quota} />
				<span class="status-pills">
					<${ClaudeStatusControl} status=${state.widget.status} store=${store} />
					<${SwitchboardStatusControl} health=${state.health} />
					<button
						class=${"away-pill " + (awayOn ? "away-on" : "away-off")}
						onClick=${onAwayPill}
						title="Toggle global away mode"
					>
						<svg class="away-moon-icon" viewBox="0 0 24 24" width="13" height="13" fill="currentColor">
							<path d="M12 3c-4.97 0-9 4.03-9 9s4.03 9 9 9 9-4.03 9-9c0-.46-.04-.92-.1-1.36-1.14 1.4-2.88 2.26-4.8 2.26-3.31 0-6-2.69-6-6 0-1.92.86-3.66 2.26-4.8C12.92 3.04 12.46 3 12 3z"/>
						</svg>
						AWAY
					</button>
				</span>
			</div>
			<div class="header-dialogs">
				${spawnOpen ? html`<${SpawnDialog} store=${store} onClose=${() => setSpawnOpen(false)} />` : null}
				${state.ui.awayOffDialogOpen
					? html`<${AwayOffDialog} store=${store} onClose=${() => store.setAwayOffDialogOpen(false)} />`
					: null}
			</div>
		</div>
	`;
}
