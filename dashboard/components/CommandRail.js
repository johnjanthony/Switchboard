import { html, useState } from "../vendor/htm-preact.js";
import * as fb from "../firebase.js";
import { globalPendingCount, memberState } from "../derive.js";
import { awayOffCmd, spawnFreshCmd, resumeCmd, combineCmd, forceEndCmd } from "../commands.js";

function push(cmd) {
	fb.pushValue(cmd.path, cmd.value);
}

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
			<h3>Spawn fresh</h3>
			<label>Surface
				<select value=${surface} onChange=${(e) => setSurface(e.target.value)}>
					<option value="windows">windows</option>
					<option value="wsl">wsl</option>
				</select>
			</label>
			<label>Project path
				<input value=${project} onInput=${(e) => setProject(e.target.value)} /></label>
			<label>Prompt (optional)
				<textarea value=${prompt} onInput=${(e) => setPrompt(e.target.value)}></textarea></label>
			<label>Target conversation id (optional)
				<input value=${target} onInput=${(e) => setTarget(e.target.value)} /></label>
			<div class="dialog-actions">
				<button onClick=${submit}>Spawn</button>
				<button onClick=${onClose}>Cancel</button>
			</div>
		</div>
	`;
}

function ResumeDialog({ store, onClose }) {
	const state = store.getState();
	const id = state.selectedConversationId;
	const conv = id ? state.conversations[id] : null;
	const members = (conv && conv.members) || {};
	const memberList = Object.values(members);
	// Resume acts on the selected conversation (no global picker). A conversation
	// is resumable when it has members and all of them are dormant (memberState
	// === 'dormant': not alive and not permanently lost). members_active for the
	// selected conversation is already subscribed by selectConversation.
	const resumable = memberList.length > 0 && memberList.every((m) => memberState(m) === "dormant");
	const [prompt, setPrompt] = useState("");
	const submit = () => {
		push(resumeCmd({ sourceConversationId: id, prompt: prompt || undefined }, fb.nowIso));
		onClose();
	};
	return html`
		<div class="dialog" role="dialog">
			<h3>Resume</h3>
			<p>Resume the selected conversation <code>${id || "none"}</code>?</p>
			${!resumable
				? html`<p class="resume-hint">Not resumable: the selected conversation must have members and all of them must be dormant.</p>`
				: null}
			<label>Prompt (optional)
				<textarea value=${prompt} onInput=${(e) => setPrompt(e.target.value)}></textarea></label>
			<div class="dialog-actions">
				<button onClick=${submit} disabled=${!id || !resumable}>Resume</button>
				<button onClick=${onClose}>Cancel</button>
			</div>
		</div>
	`;
}

function CombineDialog({ store, onClose }) {
	const state = store.getState();
	const source = state.selectedConversationId;
	const targets = Object.entries(state.conversations)
		.filter(([id, c]) => id !== source && c && c.meta && c.meta.state === "active")
		.map(([id, c]) => ({ id, title: (c.meta && c.meta.title) || id }));
	const [target, setTarget] = useState(targets.length ? targets[0].id : "");
	const [confirming, setConfirming] = useState(false);
	const submit = () => {
		push(combineCmd({ sourceConversationId: source, targetConversationId: target }, fb.nowIso));
		onClose();
	};
	return html`
		<div class="dialog" role="dialog">
			<h3>Combine</h3>
			<p>Combine selected (<code>${source || "none"}</code>) into:</p>
			<label>Target conversation
				<select value=${target} onChange=${(e) => setTarget(e.target.value)}>
					${targets.map((t) => html`<option key=${t.id} value=${t.id}>${t.title}</option>`)}
				</select>
			</label>
			<div class="dialog-actions">
				${confirming
					? html`<span class="confirm">Combine ${source} into ${target}?</span>
						<button onClick=${submit} disabled=${!source || !target}>Confirm combine</button>`
					: html`<button onClick=${() => setConfirming(true)} disabled=${!source || !target}>Combine...</button>`}
				<button onClick=${onClose}>Cancel</button>
			</div>
		</div>
	`;
}

function ForceEndDialog({ store, onClose }) {
	const state = store.getState();
	const id = state.selectedConversationId;
	const submit = () => {
		push(forceEndCmd({ conversationId: id }, fb.nowIso));
		onClose();
	};
	return html`
		<div class="dialog" role="dialog">
			<h3>Force-end</h3>
			<p>Force-end the selected conversation <code>${id || "none"}</code>? This cannot be undone.</p>
			<div class="dialog-actions">
				<button onClick=${submit} disabled=${!id}>Confirm force-end</button>
				<button onClick=${onClose}>Cancel</button>
			</div>
		</div>
	`;
}

function AwayOffDialog({ store, onClose }) {
	const state = store.getState();
	const pendingCount = globalPendingCount(state.conversations);
	const [defaultText, setDefaultText] = useState("");

	if (pendingCount === 0) {
		// Zero-pending branch: turn away off immediately with a bare exit_global.
		const confirm = () => {
			push(awayOffCmd({}, fb.nowIso));
			onClose();
		};
		return html`
			<div class="dialog" role="dialog">
				<h3>Turn away mode OFF</h3>
				<p>No pending questions. Turn away mode off now?</p>
				<div class="dialog-actions">
					<button onClick=${confirm}>Turn off</button>
					<button onClick=${onClose}>Cancel</button>
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
			<h3>Turn away mode OFF</h3>
			<p>${pendingCount} pending question(s) are waiting. Choose how to resolve them:</p>
			<label>Default reply text
				<textarea value=${defaultText} onInput=${(e) => setDefaultText(e.target.value)}></textarea></label>
			<div class="dialog-actions">
				<button onClick=${sendDefault} disabled=${!defaultText.trim()}>Send default to all</button>
				<button onClick=${skip}>Skip (leave unanswered)</button>
				<button onClick=${onClose}>Cancel</button>
			</div>
		</div>
	`;
}

export function CommandRail({ store }) {
	const state = store.getState();
	const collapsed = state.ui.rightCollapsed;
	const [openDialog, setOpenDialog] = useState(null); // 'spawn'|'resume'|'combine'|'forceend'|null
	const close = () => setOpenDialog(null);

	if (collapsed) {
		return html`
			<aside class="rail rail-right rail-collapsed">
				<button class="rail-toggle" title="Expand commands"
					onClick=${() => store.toggleRightCollapsed()}>&laquo;</button>
				<div class="rail-icons">
					<button class="cmd-icon" title="Spawn fresh" onClick=${() => setOpenDialog("spawn")}>+</button>
					<button class="cmd-icon" title="Resume" onClick=${() => setOpenDialog("resume")}>&#8635;</button>
					<button class="cmd-icon" title="Combine" onClick=${() => setOpenDialog("combine")}>&#8862;</button>
					<button class="cmd-icon" title="Force-end" onClick=${() => setOpenDialog("forceend")}>&times;</button>
				</div>
				${openDialog === "spawn" ? html`<${SpawnDialog} onClose=${close} />` : null}
				${openDialog === "resume" ? html`<${ResumeDialog} store=${store} onClose=${close} />` : null}
				${openDialog === "combine" ? html`<${CombineDialog} store=${store} onClose=${close} />` : null}
				${openDialog === "forceend" ? html`<${ForceEndDialog} store=${store} onClose=${close} />` : null}
				${state.ui.awayOffDialogOpen
					? html`<${AwayOffDialog} store=${store} onClose=${() => store.setAwayOffDialogOpen(false)} />`
					: null}
			</aside>
		`;
	}

	return html`
		<aside class="rail rail-right">
			<div class="rail-head">
				<span class="rail-title">Commands</span>
				<button class="rail-toggle" title="Collapse commands"
					onClick=${() => store.toggleRightCollapsed()}>&raquo;</button>
			</div>
			<div class="cmd-buttons">
				<button onClick=${() => setOpenDialog("spawn")}>Spawn fresh</button>
				<button onClick=${() => setOpenDialog("resume")}>Resume</button>
				<button onClick=${() => setOpenDialog("combine")}>Combine</button>
				<button onClick=${() => setOpenDialog("forceend")}>Force-end</button>
			</div>
			${openDialog === "spawn" ? html`<${SpawnDialog} onClose=${close} />` : null}
			${openDialog === "resume" ? html`<${ResumeDialog} store=${store} onClose=${close} />` : null}
			${openDialog === "combine" ? html`<${CombineDialog} store=${store} onClose=${close} />` : null}
			${openDialog === "forceend" ? html`<${ForceEndDialog} store=${store} onClose=${close} />` : null}
			${state.ui.awayOffDialogOpen
				? html`<${AwayOffDialog} store=${store} onClose=${() => store.setAwayOffDialogOpen(false)} />`
				: null}
		</aside>
	`;
}
