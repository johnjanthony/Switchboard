import { html, useState } from "../vendor/htm-preact.js";
import * as fb from "../firebase.js";
import { memberState, isActive } from "../derive.js";
import { renderMarkdown } from "../markdown.js";
import { answerCmd } from "../commands.js";
import { PaneBanner } from "./PaneBanner.js";

function escapePlain(s) {
	return String(s == null ? "" : s)
		.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
}

function Roster({ conv }) {
	const members = (conv && conv.members) || {};
	const agentStatus = (conv && conv.agentStatus) || {};
	const entries = Object.entries(members);
	if (entries.length === 0) return html`<div class="roster-empty">No members.</div>`;
	return html`
		<div class="roster">
			${entries.map(([sender, m]) => {
				const status = agentStatus[sender];
				return html`
					<div class="member" key=${sender}>
						<span class=${"dot member-" + memberState(m)}></span>
						<span class="member-name">${sender}</span>
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

function MessageBody({ msg }) {
	const inner = msg.format === "markdown"
		? renderMarkdown(msg.text)
		: `<p>${escapePlain(msg.text).replace(/\n/g, "<br />")}</p>`;
	return html`<div class="msg-body" dangerouslySetInnerHTML=${{ __html: inner }}></div>`;
}

function Transcript({ conv }) {
	const messages = (conv && conv.messages) || {};
	const ordered = Object.entries(messages)
		.map(([msgId, m]) => ({ msgId, m: m || {} }))
		.sort((a, b) => String(a.m.timestamp || "").localeCompare(String(b.m.timestamp || "")));
	if (ordered.length === 0) return html`<div class="transcript-empty">No messages yet.</div>`;
	return html`
		<div class="transcript">
			${ordered.map(({ msgId, m }) => {
				const cls = ["msg", m.cancelled ? "msg-cancelled" : "", m.rejected ? "msg-rejected" : ""]
					.filter(Boolean).join(" ");
				return html`
					<div class=${cls} key=${msgId}>
						<div class="msg-head">
							<span class="msg-sender">${m.sender}</span>
							<span class="msg-time">${m.timestamp || ""}</span>
						</div>
						<${MessageBody} msg=${m} />
					</div>
				`;
			})}
		</div>
	`;
}

function AnswerBox({ convId, pending }) {
	const [text, setText] = useState("");
	const send = () => {
		if (!text.trim()) return;
		const { path, value } = answerCmd(convId, pending.requestId, text, pending.sender, fb.nowIso);
		fb.setValue(path, value);
		setText("");
	};
	return html`
		<div class="answer-box" key=${pending.requestId}>
			<div class="pending-question">${pending.questionText}</div>
			${(pending.suggestions && pending.suggestions.length)
				? html`<div class="suggestions">
						${pending.suggestions.map((s, i) => html`
							<button key=${i} class="suggestion" onClick=${() => setText(s)}>${s}</button>
						`)}
					</div>`
				: null}
			<textarea class="answer-input" value=${text}
				onInput=${(e) => setText(e.target.value)} placeholder="Type your answer..."></textarea>
			<button class="answer-send" onClick=${send}>Send to ${pending.sender}</button>
		</div>
	`;
}

export function ConversationDetail({ store }) {
	const state = store.getState();
	const id = state.selectedConversationId;
	const conv = id ? state.conversations[id] : null;

	const banner = state.paneErrors.detail
		? html`<${PaneBanner} message=${state.paneErrors.detail}
				onRetry=${() => store.retrySelectedConversation()} />`
		: null;

	if (!id) {
		return html`<section class="detail">${banner}
			<div class="detail-empty">Select a conversation.</div></section>`;
	}

	// pendingsFlat carries camelCase questionText/suggestions per add_pending_question_record.
	// Only an active conversation renders answer boxes; an ended/force-ended conversation
	// shows none, so a stale pending question is never presented as answerable.
	const pendings = isActive(conv && conv.meta)
		? state.pendingsFlat.filter((p) => p.convId === id)
		: [];

	return html`
		<section class="detail">
			${banner}
			<h2 class="detail-title">${(conv && conv.meta && conv.meta.title) || id}</h2>
			<${Roster} conv=${conv} />
			<${Transcript} conv=${conv} />
			<div class="pending-stack">
				${pendings.map((p) => html`<${AnswerBox} key=${p.requestId} convId=${id} pending=${p} />`)}
			</div>
		</section>
	`;
}
