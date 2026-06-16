import { html, useState } from "../vendor/htm-preact.js";
import * as fb from "../firebase.js";
import { pendingCountFor } from "../derive.js";
import { setHiddenCmd } from "../commands.js";

export function ConversationList({ store }) {
	const state = store.getState();
	const collapsed = state.ui.leftCollapsed;
	const [showHidden, setShowHidden] = useState(false);

	// Sort by meta.last_activity_at: NUMERIC epoch float, descending (newest first).
	// Verified in server/firebase.py write_conversation_meta: last_activity_at is a
	// float seconds-since-epoch (Android reads it as Double), so a numeric compare is correct.
	const rows = Object.entries(state.conversations)
		.map(([id, c]) => ({ id, meta: (c && c.meta) || {}, pending: (c && c.pending) || {} }))
		.filter((r) => showHidden || !r.meta.hidden)
		.sort((a, b) => (b.meta.last_activity_at || 0) - (a.meta.last_activity_at || 0));

	const onHideToggle = (id, currentlyHidden) => {
		const { path, value } = setHiddenCmd(id, !currentlyHidden);
		fb.setValue(path, value);
	};

	if (collapsed) {
		return html`
			<aside class="rail rail-left rail-collapsed">
				<button class="rail-toggle" title="Expand conversations"
					onClick=${() => store.toggleLeftCollapsed()}>&raquo;</button>
				<div class="rail-icons">
					${rows.map((r) => {
						const count = pendingCountFor(r.pending);
						return html`
							<button
								key=${r.id}
								class=${"conv-dot conv-state-" + (r.meta.state || "active") +
									(r.id === state.selectedConversationId ? " selected" : "") +
										(r.id === state.openConversationId ? " open" : "")}
								title=${r.meta.title || r.id}
								onClick=${() => store.selectConversation(r.id)}
							>${count > 0 ? html`<span class="badge">${count}</span>` : null}</button>
						`;
					})}
				</div>
			</aside>
		`;
	}

	return html`
		<aside class="rail rail-left">
			<div class="rail-head">
				<span class="rail-title">Conversations</span>
				<label class="show-hidden">
					<input type="checkbox" checked=${showHidden}
						onChange=${(e) => setShowHidden(e.target.checked)} /> show hidden
				</label>
				<button class="rail-toggle" title="Collapse conversations"
					onClick=${() => store.toggleLeftCollapsed()}>&laquo;</button>
			</div>
			<ul class="conv-list">
				${rows.map((r) => {
					const count = pendingCountFor(r.pending);
					return html`
						<li
							key=${r.id}
							class=${"conv-row" + (r.id === state.selectedConversationId ? " selected" : "") + (r.id === state.openConversationId ? " open" : "")}
							onClick=${() => store.selectConversation(r.id)}
						>
							<span class=${"dot conv-state-" + (r.meta.state || "active")}></span>
							<span class="conv-title">${r.meta.title || r.id}</span>
							<span class="conv-state-label">${r.meta.state || "active"}</span>
							${count > 0 ? html`<span class="badge">${count}</span>` : null}
							<button
								class="conv-hide"
								title=${r.meta.hidden ? "Unhide" : "Hide"}
								onClick=${(e) => { e.stopPropagation(); onHideToggle(r.id, !!r.meta.hidden); }}
							>${r.meta.hidden ? "unhide" : "hide"}</button>
						</li>
					`;
				})}
			</ul>
		</aside>
	`;
}
