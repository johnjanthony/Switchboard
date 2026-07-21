import { html, useState } from "../vendor/htm-preact.js";
import {
	sessionChip,
	sessionAgeSeconds,
	formatAge,
	sortSessionEntries,
	sensorOffline,
	sessionLabel,
	needsAttention,
	wakePathHint,
	approvalHint,
	isConvenable,
	isActive,
} from "../derive.js";

// Sessions roster: every Claude Code session the hub knows about, conversation
// or not. Lives in the left rail above the conversation list; independently
// collapsible. Convenable rows (active/idle/awaiting_human/awaiting_agent) get
// a selection checkbox; selecting one or more shows the convene footer bar.
export function SessionsRail({ store, collapsed }) {
	const state = store.getState();
	const showEnded = state.ui.showEndedSessions;
	const allEntries = sortSessionEntries(state.sessions);
	const activeEntries = allEntries.filter(({ record }) => record.state !== 'ended' && record.state !== 'lost');
	const endedEntries = allEntries.filter(({ record }) => record.state === 'ended' || record.state === 'lost');
	const entries = showEnded ? allEntries : activeEntries;
	const offline = sensorOffline(state.widget.pushedAt, Date.now());
	const selectedIds = state.ui.selectedSessionIds;
	const [conveneTarget, setConveneTarget] = useState("new");
	const [conveneTitle, setConveneTitle] = useState("");

	const activeConversations = Object.entries(state.conversations)
		.filter(([, c]) => isActive(c && c.meta))
		.map(([id, c]) => ({ id, title: (c.meta && c.meta.title) || id }));

	const onConvene = () => {
		const title = conveneTarget === "new" ? (conveneTitle || null) : null;
		store.conveneSelected({ target: conveneTarget, title });
		setConveneTarget("new");
		setConveneTitle("");
	};

	if (collapsed) {
		return html`
			<div class="rail-icons">
				${entries.map(({ id, record }) => {
					const chip = sessionChip(record);
					const attn = needsAttention(record, state.sessionAcks[id]);
					return html`
						<button
							key=${id}
							class=${"conv-dot" + (selectedIds.includes(id) ? " selected" : "")}
							title=${sessionLabel(record)}
							onClick=${() => {
								record.conversation_id && store.selectConversation(record.conversation_id);
								store.ackSession(id);
								store.toggleSessionSelected(id);
							}}
						>
							<span class=${"lamp session-lamp " + chip.cls}></span>
							${attn ? html`<span class="badge attn-badge">!</span>` : null}
						</button>
					`;
				})}
			</div>
		`;
	}

	const renderSessionRow = ({ id, record }) => {
		const chip = sessionChip(record);
		const age = formatAge(sessionAgeSeconds(record, Date.now()));
		const ring = record.context_pct != null ? `${Math.round(record.context_pct * 100)}%` : "";
		const convenable = isConvenable(record);
		const attn = needsAttention(record, state.sessionAcks[id]);
		const tooltipParts = [
			record.cwd, record.last_transition_source, wakePathHint(record), approvalHint(record, Date.now()),
		].filter(Boolean);
		return html`
			<li class="session-row" key=${id}
				onClick=${() => {
					record.conversation_id && store.selectConversation(record.conversation_id);
					store.ackSession(id);
				}}
				title=${tooltipParts.join(" - ")}>
				${convenable ? html`
					<input type="checkbox" class="session-check" checked=${selectedIds.includes(id)}
						onClick=${(e) => { e.stopPropagation(); store.toggleSessionSelected(id); }} />
				` : html`<span class="session-check-spacer"></span>`}
				<span class=${"session-chip " + chip.cls}>${chip.label}</span>
				<span class="session-project">${sessionLabel(record)}</span>
				${attn ? html`<span class="session-attn" title="needs you"></span>` : null}
				<span class="session-meta">
					${record.sender ? html`<span class="session-sender">${record.sender}</span>` : null}
					${ring ? html`<span class="session-ring">${ring}</span>` : null}
					<span class="session-age">${age}</span>
					${record.conversation_id ? html`<span class="session-linked">⇢</span>` : null}
				</span>
			</li>
		`;
	};

	return html`
		<section class="sessions-rail">
			${offline ? html`<div class="sessions-sensor-offline">sensor offline - staleness unknown</div>` : null}
				<ul class="sessions-list">
					${activeEntries.map(renderSessionRow)}
					${endedEntries.length > 0 ? html`
						<li class="conv-divider clickable" onClick=${() => store.setShowEndedSessions(!showEnded)}>
							<span class="conv-divider-caret">${showEnded ? "▾" : "▸"}</span>
							${showEnded ? "Hide" : "Show"} ${endedEntries.length} ended
						</li>
					` : null}
					${showEnded ? endedEntries.map(renderSessionRow) : null}
				</ul>
				${selectedIds.length > 0 ? html`
					<div class="sessions-convene-bar">
						<span class="sessions-convene-count">${selectedIds.length} selected</span>
						<select class="sessions-convene-target" value=${conveneTarget}
							onChange=${(e) => setConveneTarget(e.target.value)}>
							<option value="new">New conversation</option>
							${activeConversations.map((c) => html`<option value=${c.id}>${c.title}</option>`)}
						</select>
						${conveneTarget === "new" ? html`
							<input class="sessions-convene-title" type="text" placeholder="Title (optional)"
								value=${conveneTitle} onInput=${(e) => setConveneTitle(e.target.value)} />
						` : null}
						<button class="sessions-convene-btn" onClick=${onConvene}>Convene</button>
					</div>
				` : null}
		</section>
	`;
}
