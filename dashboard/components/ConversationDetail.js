import { html, useState } from "../vendor/htm-preact.js";
import { memberState, isActive, predecessorTitle, ringForMember, ringSeverity } from "../derive.js";
import { renderMarkdown } from "../markdown.js";
import { documentPillHtml } from "../document.js";
import { PaneBanner } from "./PaneBanner.js";

function escapePlain(s) {
	return String(s == null ? "" : s)
		.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
}

// Member lamp on the roster: alive glows green, dormant sits amber (resumable),
// lost burns red.
const MEMBER_LAMP = { alive: "lamp lamp-green", dormant: "lamp lamp-amber", lost: "lamp lamp-red" };

// Render an ISO timestamp as a local clock time; fall back to the raw value if
// it does not parse (never silently blank a real timestamp).
function fmtMsgTime(ts) {
	if (!ts) return "";
	const d = new Date(ts);
	return Number.isNaN(d.getTime()) ? String(ts) : d.toLocaleTimeString();
}

function Roster({ conv, rings }) {
	const members = (conv && conv.members) || {};
	const agentStatus = (conv && conv.agentStatus) || {};
	const entries = Object.entries(members);
	if (entries.length === 0) return html`<div class="roster-empty">No members on the line.</div>`;
	return html`
		<div class="roster">
			${entries.map(([sender, m]) => {
				const status = agentStatus[sender];
				const ring = ringForMember(m, rings);
				const pct = ring ? Math.round((Number(ring.pct) || 0) * 100) : null;
				return html`
					<div class="member" key=${sender}>
						<span class=${MEMBER_LAMP[memberState(m)] || "lamp lamp-cold"}></span>
						<span class="member-name">${sender}</span>
						${ring ? html`<span class=${"ctx-pct ctx-" + ringSeverity(ring.pct)} title=${"Context window " + pct + "% full"}>${pct}%</span>` : null}
						<span class="member-surface">${m.surface || ""}</span>
						${status
							? html`<span class="member-status">${status.state}${status.detail ? ": " + status.detail : ""}</span>`
							: html`<span class="member-status idle">idle</span>`}
					</div>
				`;
			})}
		</div>
	`;
}

function MessageBody({ msg, convId, msgId }) {
	const body = msg.format === "markdown"
		? renderMarkdown(msg.text)
		: `<p>${escapePlain(msg.text).replace(/\n/g, "<br />")}</p>`;
	// A document message carries the caption in text (rendered above) plus a url +
	// filename; append the pill linking to the preview page.
	const inner = body + documentPillHtml(msg, convId, msgId);
	return html`<div class="msg-body" dangerouslySetInnerHTML=${{ __html: inner }}></div>`;
}

function Transcript({ conv, convId, pendingMsgIds }) {
	const messages = (conv && conv.messages) || {};
	// A currently-pending question already shows in its own answer box below, so
	// suppress its transcript copy. Once answered it is no longer pending and
	// reappears here as history.
	const ordered = Object.entries(messages)
		.map(([msgId, m]) => ({ msgId, m: m || {} }))
		.filter(({ msgId }) => !pendingMsgIds.has(msgId))
		.sort((a, b) => String(a.m.timestamp || "").localeCompare(String(b.m.timestamp || "")));
	if (ordered.length === 0) {
		// Don't claim "no traffic" when the only message is the suppressed pending
		// question (its answer box is rendered separately).
		return pendingMsgIds.size === 0
			? html`<div class="transcript-empty">No traffic on this line yet.</div>`
			: null;
	}
	return html`
		<div class="transcript">
			${ordered.map(({ msgId, m }) => {
				const cls = ["msg", m.cancelled ? "msg-cancelled" : "", m.rejected ? "msg-rejected" : ""]
					.filter(Boolean).join(" ");
				// Sender + timestamp render OUTSIDE the bubble, above the body.
				return html`
					<div class=${cls} key=${msgId}>
						<div class="msg-meta">
							<span class="msg-sender">${m.sender}</span>
							<span class="msg-time">${fmtMsgTime(m.timestamp)}</span>
						</div>
						<div class="msg-bubble"><${MessageBody} msg=${m} convId=${convId} msgId=${msgId} /></div>
					</div>
				`;
			})}
		</div>
	`;
}

function AnswerBox({ store, convId, pending }) {
	const [text, setText] = useState("");
	// Single send path: the Send button submits the textarea; a suggestion submits
	// itself directly (one click, no intermediate edit step).
	const sendText = async (value) => {
		const v = String(value == null ? "" : value).trim();
		if (!v) return;
		const ok = await store.sendAnswer(convId, pending.requestId, v, pending.sender);
		if (ok) setText("");
	};
	return html`
		<div class="answer-box" key=${pending.requestId}>
			<div class="pending-question">${pending.questionText}</div>
			${(pending.suggestions && pending.suggestions.length)
				? html`<div class="suggestions">
						${pending.suggestions.map((s, i) => html`
							<button key=${i} class="suggestion" onClick=${() => sendText(s)}>${s}</button>
						`)}
					</div>`
				: null}
			<textarea class="answer-input" value=${text}
				onInput=${(e) => setText(e.target.value)} placeholder="Type your answer..."></textarea>
			<button class="answer-send" onClick=${() => sendText(text)}>Send to ${pending.sender}</button>
		</div>
	`;
}

// --- Line lifecycle dialogs (act on the SELECTED line, in place) -------------

function RestoreDialog({ store, conv, convId, onClose }) {
	const members = (conv && conv.members) || {};
	const memberList = Object.values(members);
	// A line is restorable when it has members and all of them are dormant
	// (not alive, not permanently lost).
	const restorable = memberList.length > 0 && memberList.every((m) => memberState(m) === "dormant");
	const [prompt, setPrompt] = useState("");
	const submit = () => {
		store.restoreLine(convId, prompt || undefined);
		onClose();
	};
	return html`
		<div class="dialog" role="dialog">
			<h3>Restore line</h3>
			<p>Bring the dormant agents on <code>${convId}</code> back online?</p>
			${!restorable
				? html`<p class="resume-hint">Not restorable: the line needs members, and every one must be dormant.</p>`
				: null}
			<label>Opening prompt (optional)
				<textarea value=${prompt} onInput=${(e) => setPrompt(e.target.value)}></textarea></label>
			<div class="dialog-actions">
				<button onClick=${submit} disabled=${!restorable}>Restore</button>
				<button class="ghost" onClick=${onClose}>Cancel</button>
			</div>
		</div>
	`;
}

function PatchDialog({ store, convId, onClose }) {
	const state = store.getState();
	const targets = Object.entries(state.conversations)
		.filter(([id, c]) => id !== convId && c && c.meta && c.meta.state === "active")
		.map(([id, c]) => ({ id, title: (c.meta && c.meta.title) || id }));
	const [target, setTarget] = useState(targets.length ? targets[0].id : "");
	const [confirming, setConfirming] = useState(false);
	const submit = () => {
		store.patchLine(convId, target);
		onClose();
	};
	return html`
		<div class="dialog" role="dialog">
			<h3>Patch into another line</h3>
			<p>Move <code>${convId}</code> onto:</p>
			${targets.length === 0
				? html`<p class="resume-hint">No other active line to patch into.</p>`
				: html`<label>Target line
					<select value=${target} onChange=${(e) => setTarget(e.target.value)}>
						${targets.map((t) => html`<option key=${t.id} value=${t.id}>${t.title}</option>`)}
					</select>
				</label>`}
			<div class="dialog-actions">
				${confirming
					? html`<span class="confirm">Patch ${convId} into ${target}?</span>
						<button onClick=${submit} disabled=${!target}>Confirm patch</button>`
					: html`<button onClick=${() => setConfirming(true)} disabled=${!target}>Patch in...</button>`}
				<button class="ghost" onClick=${onClose}>Cancel</button>
			</div>
		</div>
	`;
}

function DropDialog({ store, convId, onClose }) {
	const submit = () => {
		store.dropLine(convId);
		onClose();
	};
	return html`
		<div class="dialog" role="dialog">
			<h3>Drop line</h3>
			<p>End <code>${convId}</code> for good? This pulls the cord and cannot be undone.</p>
			<div class="dialog-actions">
				<button onClick=${submit}>Drop line</button>
				<button class="ghost" onClick=${onClose}>Cancel</button>
			</div>
		</div>
	`;
}

// Slim tappable banner shown under the title row when this line was continued from
// a predecessor (meta.continued_from resolves to a loaded conversation). Clicking it
// selects the predecessor in the center pane; multi-hop chains walk back one at a time.
function PredecessorBanner({ title, onOpen }) {
	return html`
		<button class="predecessor-banner" onClick=${onOpen} title="Open the predecessor line">
			<span class="predecessor-arrow">↩</span>
			<span>Continued from "<span class="predecessor-title">${title}</span>"</span>
		</button>
	`;
}

export function ConversationDetail({ store }) {
	const state = store.getState();
	const id = state.selectedConversationId;
	const conv = id ? state.conversations[id] : null;
	const [dialog, setDialog] = useState(null); // 'restore' | 'patch' | 'drop' | null
	const close = () => setDialog(null);

	const banner = state.paneErrors.detail
		? html`<${PaneBanner} message=${state.paneErrors.detail}
				onRetry=${() => store.retrySelectedConversation()} />`
		: null;

	if (!id) {
		return html`<section class="detail">${banner}
			<div class="detail-empty">Select a line.</div></section>`;
	}

	const meta = conv && conv.meta;
	const active = isActive(meta);
	const predTitle = predecessorTitle(conv, state.conversations);
	const predecessorId = meta ? meta.continued_from : null;
	const members = (conv && conv.members) || {};
	const memberList = Object.values(members);
	const restorable = memberList.length > 0 && memberList.every((m) => memberState(m) === "dormant");

	// pendingsFlat carries camelCase questionText/suggestions. Only an active line
	// renders answer boxes, so a stale pending is never presented as answerable.
	const pendings = active ? state.pendingsFlat.filter((p) => p.convId === id) : [];
	// msgIds of the still-pending questions, so the transcript can suppress their
	// duplicate copies (they render in answer boxes instead).
	const pendingMsgIds = new Set(pendings.map((p) => p.msgId).filter(Boolean));

	return html`
		<section class="detail">
			<div class="detail-head">
				${banner}
				<div class="detail-title-row">
					<h2 class="detail-title">${(meta && meta.title) || id}</h2>
					<span class=${"line-state " + (active ? "active" : "ended")}>${(meta && meta.state) || "active"}</span>
					<div class="line-actions">
						<button class="line-action" disabled=${!restorable}
							title=${restorable ? "Restore the dormant line" : "Only a fully dormant line can be restored"}
							onClick=${() => setDialog("restore")}>Restore</button>
						<button class="line-action" onClick=${() => setDialog("patch")}>Patch into…</button>
						<button class="line-action danger" disabled=${!active}
							title=${active ? "Force-end this line" : "Line already ended"}
							onClick=${() => setDialog("drop")}>Drop line</button>
					</div>
				</div>
				${predTitle ? html`<${PredecessorBanner} title=${predTitle} onOpen=${() => store.selectConversation(predecessorId)} />` : null}
				<${Roster} conv=${conv} rings=${state.widget.rings} />
				${dialog === "restore" ? html`<${RestoreDialog} store=${store} conv=${conv} convId=${id} onClose=${close} />` : null}
				${dialog === "patch" ? html`<${PatchDialog} store=${store} convId=${id} onClose=${close} />` : null}
				${dialog === "drop" ? html`<${DropDialog} store=${store} convId=${id} onClose=${close} />` : null}
			</div>
			<div class="detail-body">
				<${Transcript} conv=${conv} convId=${id} pendingMsgIds=${pendingMsgIds} />
				<div class="pending-stack">
					${pendings.map((p) => html`<${AnswerBox} key=${p.requestId} store=${store} convId=${id} pending=${p} />`)}
				</div>
			</div>
		</section>
	`;
}
