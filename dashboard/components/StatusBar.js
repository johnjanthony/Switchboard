import { html, useState } from "../vendor/htm-preact.js";
import { isActive, globalPendingCount, oldestPendingAgeSeconds, formatAge, modelOptionsFor, effortOptionsFor } from "../derive.js";
import { statusDotClass } from "../statusControl.js";

function healthLampClass(health) {
	if (!health.reachable) return "lamp lamp-red";
	return health.healthy ? "lamp lamp-green" : "lamp lamp-amber";
}

// Open a fresh line. The one global action that doesn't act on an existing line,
// so it lives in the operator header rather than on a conversation.
function SpawnDialog({ store, onClose }) {
	const [agent, setAgent] = useState("claude");
	const [surface, setSurface] = useState("windows");
	const [project, setProject] = useState("");
	const [prompt, setPrompt] = useState("");
	const [target, setTarget] = useState("");
	const [model, setModel] = useState("");
	const [effort, setEffort] = useState("");
	const spawnOpts = store.getState().spawnOptions;
	const modelOptions = modelOptionsFor(spawnOpts, agent);
	const effortOptions = effortOptionsFor(spawnOpts, agent, model || null);
	const canSubmit = project.trim().length > 0;
	const submit = () => {
		if (!canSubmit) return;
		store.spawnFresh({
			agent,
			surface,
			project,
			prompt: prompt || undefined,
			targetConversationId: target || undefined,
			model: model || undefined,
			effort: effort || undefined,
		});
		onClose();
	};
	return html`
		<div class="dialog" role="dialog">
			<h3>Open a line</h3>
			<label>Agent
				<select value=${agent} onChange=${(e) => { setAgent(e.target.value); setModel(""); setEffort(""); }}>
					<option value="claude">Claude</option>
					<option value="antigravity">Antigravity</option>
				</select>
			</label>
			<label>Surface
				<select value=${surface} onChange=${(e) => setSurface(e.target.value)}>
					<option value="windows">windows</option>
					<option value="wsl">wsl</option>
				</select>
			</label>
			<label>Model
				<select value=${model} onChange=${(e) => {
					const v = e.target.value;
					setModel(v);
					if (effort && !effortOptionsFor(spawnOpts, agent, v || null).includes(effort)) setEffort("");
				}}>
					<option value="">Default (CLI)</option>
					${modelOptions.map((id) => html`<option value=${id}>${id}</option>`)}
				</select>
			</label>
			${agent !== "antigravity" ? html`
				<label>Effort
					<select value=${effort} disabled=${effortOptions.length === 0} onChange=${(e) => setEffort(e.target.value)}>
						<option value="">Default (CLI)</option>
						${effortOptions.map((t) => html`<option value=${t}>${t}</option>`)}
					</select>
				</label>
			` : null}
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

	return html`
		<div class="quota-row">
			<div class="quota-bars">
				<div class="quota-segment-track">
					<div class="quota-segment gradient-fill" style=${{ clipPath: `inset(0 ${(1 - usageFrac) * 100}% 0 0 round 99px)` }}>
					</div>
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
	const title = "Claude: " + desc;
	const onClick = () => {
		if (!isGreen) {
			window.open("https://status.claude.com", "_blank");
			return;
		}
		const action = (s.button === "check" || s.watch_state === "idle") ? "check" : "stop";
		store.requestClaudeStatus(action);
	};
	const colorClass = isGreen ? "status-green" : claudeStatusPillClass(s.level);
	return html`
		<button
			class=${"claude-pill status-pill " + (isWatching ? "watching " : "idle ") + colorClass}
			onClick=${onClick}
			title=${title}
		>
			<svg viewBox="0 0 24 24" width="14" height="14" fill="currentColor">
				<polygon points="12,0 14.07,6.6 17.3,3.84 17.4,9.93 24,12 17.4,14.07 17.3,20.16 14.07,17.4 12,24 9.93,17.4 6.7,20.16 6.6,14.07 0,12 6.6,9.93 6.7,3.84 9.93,6.6" />
			</svg>
		</button>
	`;
}

function AntigravityStatusControl({ status, store }) {
	const s = status || { watch_state: "idle", button: "check", level: "operational", description: "", incidents: [] };
	const isWatching = s.watch_state !== "idle" && s.button !== "check";
	const isGreen = !s.level || s.level === "operational" || s.level === "none";

	let desc = s.description || "Antigravity status";
	if (s.incidents && s.incidents.length > 0) {
		const incidentsText = s.incidents.join("; ");
		if (!desc || desc.toLowerCase().includes("all systems operational")) {
			desc = incidentsText;
		} else if (!desc.includes(incidentsText)) {
			desc = desc + " - " + incidentsText;
		}
	}
	const title = "Antigravity: " + desc;
	const onClick = () => {
		if (!isGreen) {
			window.open("https://status.cloud.google.com", "_blank");
			return;
		}
		const action = (s.button === "check" || s.watch_state === "idle") ? "check" : "stop";
		store.requestAntigravityStatus(action);
	};
	const colorClass = isGreen ? "status-green" : claudeStatusPillClass(s.level);
	return html`
		<button
			class=${"antigravity-pill status-pill " + (isWatching ? "watching " : "idle ") + colorClass}
			onClick=${onClick}
			title=${title}
		>
			<svg viewBox="0 0 24 24" width="14" height="14" fill="currentColor">
				<path d="M 12 0 C 16.2 7.8 16.2 7.8 24 12 C 16.2 16.2 16.2 16.2 12 24 C 7.8 16.2 7.8 16.2 0 12 C 7.8 7.8 7.8 7.8 12 0 Z"/>
			</svg>
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
		<button class=${"switchboard-pill status-pill " + healthStatusPillClass(health)} title=${title}>
			<svg viewBox="0 0 24 24" width="14" height="14" fill="currentColor">
				<rect x="6.25" y="2" width="1.5" height="20" rx="0.75" />
				<rect x="16.25" y="2" width="1.5" height="20" rx="0.75" />
				<circle cx="7" cy="7" r="3.5" />
				<circle cx="17" cy="17" r="3.5" />
			</svg>
		</button>
	`;
}

export function HeaderControls({ store, collapsed }) {
	const state = store.getState();
	const [spawnOpen, setSpawnOpen] = useState(false);
	const awayOn = state.globalAway;
	const onAwayPill = () => {
		if (awayOn) {
			store.setAwayOffDialogOpen(true);
		} else {
			store.awayOn();
		}
	};

	return html`
		<div class=${"rail-header-controls" + (collapsed ? " collapsed" : "")}>
			${collapsed ? html`
				<button class="rail-toggle-header status-pill expand-top" title="Expand rail" onClick=${() => store.toggleLeftCollapsed()}>»</button>
			` : null}
			<button class="open-line-btn status-pill" onClick=${() => setSpawnOpen(true)} title="Open line">+</button>
			<button
				class=${"away-pill status-pill " + (awayOn ? "away-on" : "away-off")}
				onClick=${onAwayPill}
				title="Toggle global away mode"
			>
				<svg class="away-moon-icon" viewBox="0 0 24 24" width="13" height="13" fill="currentColor">
					<path d="M12 3c-4.97 0-9 4.03-9 9s4.03 9 9 9 9-4.03 9-9c0-.46-.04-.92-.1-1.36-1.14 1.4-2.88 2.26-4.8 2.26-3.31 0-6-2.69-6-6 0-1.92.86-3.66 2.26-4.8C12.92 3.04 12.46 3 12 3z"/>
				</svg>
			</button>
			<${AntigravityStatusControl} status=${state.widget.antigravityStatus} store=${store} />
			<${ClaudeStatusControl} status=${state.widget.status} store=${store} />
			<${SwitchboardStatusControl} health=${state.health} />
			${!collapsed ? html`
				<button class="rail-toggle-header status-pill" title="Collapse rail" onClick=${() => store.toggleLeftCollapsed()}>«</button>
			` : null}

			${spawnOpen ? html`<${SpawnDialog} store=${store} onClose=${() => setSpawnOpen(false)} />` : null}
			${state.ui.awayOffDialogOpen
				? html`<${AwayOffDialog} store=${store} onClose=${() => store.setAwayOffDialogOpen(false)} />`
				: null}
		</div>
	`;
}

export function StatusBar({ store }) {
	const state = store.getState();
	const convs = state.conversations;
	const pendingCount = globalPendingCount(convs);
	const oldest = oldestPendingAgeSeconds(state.pendingsFlat, Date.now());

	return html`
		<div class="status-bar">
			${pendingCount > 0 ? html`
				<span class="status-counts">
					<span class="count lit"><b>${pendingCount}</b> ${pendingCount === 1 ? 'question' : 'questions'}</span>
					<span class="count">age <b>${oldest == null ? "-" : formatAge(oldest)}</b></span>
				</span>
			` : null}
			<${QuotaReadout} quota=${state.widget.quota} />
		</div>
	`;
}
