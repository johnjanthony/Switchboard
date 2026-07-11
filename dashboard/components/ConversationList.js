import { html, useState } from "../vendor/htm-preact.js";
import { pendingCountFor, isActive } from "../derive.js";
import { SessionsRail } from "./SessionsRail.js";

// Relative "last traffic" age from meta.last_activity_at (float epoch SECONDS,
// verified in server write_conversation_meta). Empty when never active.
function fmtLastTraffic(lastActivityAtSec) {
	if (!lastActivityAtSec) return "";
	const ageSec = Math.floor(Date.now() / 1000 - lastActivityAtSec);
	if (ageSec < 0) return "now";
	if (ageSec < 60) return `${ageSec}s`;
	if (ageSec < 3600) return `${Math.floor(ageSec / 60)}m`;
	if (ageSec < 86400) return `${Math.floor(ageSec / 3600)}h`;
	return `${Math.floor(ageSec / 86400)}d`;
}

// Board lamp: a calling line (active + pending) pulses amber; a connected line
// (active, quiet) glows green; an ended line is a cold, unlit bead.
function lampClass(meta, calling) {
	if (!isActive(meta)) return "lamp lamp-cold";
	return calling ? "lamp lamp-calling" : "lamp lamp-green";
}

export function ConversationList({ store }) {
	const state = store.getState();
	const collapsed = state.ui.leftCollapsed;
	const [showHidden, setShowHidden] = useState(false);

	// Sort by meta.last_activity_at: numeric epoch seconds, descending (newest first).
	const byActivity = (a, b) => (b.meta.last_activity_at || 0) - (a.meta.last_activity_at || 0);
	const all = Object.entries(state.conversations)
		.map(([id, c]) => ({ id, meta: (c && c.meta) || {}, pending: (c && c.pending) || {} }))
		.filter((r) => showHidden || !r.meta.hidden);
	// Hidden lines always sink to the bottom (below a divider), regardless of activity.
	const visibleRows = all.filter((r) => !r.meta.hidden).sort(byActivity);
	const hiddenRows = all.filter((r) => r.meta.hidden).sort(byActivity);
	const rows = visibleRows.concat(hiddenRows);

	const onHideToggle = (id, currentlyHidden) => {
		store.setHidden(id, !currentlyHidden);
	};

	const renderRow = (r) => {
		const count = isActive(r.meta) ? pendingCountFor(r.pending) : 0;
		const ended = !isActive(r.meta);
		const rowClass = "conv-row" +
			(r.id === state.selectedConversationId ? " selected" : "") +
			(ended ? " ended" : "") +
			(r.meta.hidden ? " hidden" : "");
		return html`
			<li key=${r.id} class=${rowClass} onClick=${() => store.selectConversation(r.id)}>
				<span class=${lampClass(r.meta, count > 0)}></span>
				<div class="conv-main">
					<div class="conv-line-1">
						<span class="conv-title">${r.meta.title || r.id}</span>
						${count > 0 ? html`<span class="badge">${count}</span>` : null}
						<span class="conv-time">${fmtLastTraffic(r.meta.last_activity_at)}</span>
					</div>
					<div class="conv-sub">
						${r.meta.hidden ? html`<span class="hidden-tag">hidden</span>` : null}
						<span class="conv-state-label">${r.meta.state || "active"}</span>
						<button
							class="conv-hide"
							title=${r.meta.hidden ? "Unhide" : "Hide"}
							onClick=${(e) => { e.stopPropagation(); onHideToggle(r.id, !!r.meta.hidden); }}
						>${r.meta.hidden ? "unhide" : "hide"}</button>
					</div>
				</div>
			</li>
		`;
	};

	if (collapsed) {
		return html`
			<aside class="rail rail-left rail-collapsed">
				<button class="rail-toggle" title="Expand board"
					onClick=${() => store.toggleLeftCollapsed()}>»</button>
				<div class="rail-icons">
					${rows.map((r) => {
						// Only an active line shows a pending badge; an ended line must not
						// surface a stale, answerable question count.
						const count = isActive(r.meta) ? pendingCountFor(r.pending) : 0;
						return html`
							<button
								key=${r.id}
								class=${"conv-dot" +
									(r.id === state.selectedConversationId ? " selected" : "") +
									(r.meta.hidden ? " hidden" : "")}
								title=${r.meta.title || r.id}
								onClick=${() => store.selectConversation(r.id)}
							>
								<span class=${lampClass(r.meta, count > 0)}></span>
								${count > 0 ? html`<span class="badge">${count}</span>` : null}
							</button>
						`;
					})}
				</div>
			</aside>
		`;
	}

	return html`
		<aside class="rail rail-left">
			<${SessionsRail} store=${store} />
			<div class="rail-head">
				<span class="rail-title">Board</span>
				<label class="show-hidden">
					<input type="checkbox" checked=${showHidden}
						onChange=${(e) => setShowHidden(e.target.checked)} /> show hidden
				</label>
				<button class="rail-toggle" title="Collapse board"
					onClick=${() => store.toggleLeftCollapsed()}>«</button>
			</div>
			<ul class="conv-list">
				${visibleRows.map(renderRow)}
				${hiddenRows.length
					? html`<li class="conv-divider" key="__hidden_divider__">Hidden</li>`
					: null}
				${hiddenRows.map(renderRow)}
			</ul>
		</aside>
	`;
}
