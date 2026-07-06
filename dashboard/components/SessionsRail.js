import { html } from "../vendor/htm-preact.js";
import {
	sessionChip,
	projectTail,
	sessionAgeSeconds,
	formatAge,
	sortSessionEntries,
	sensorOffline,
} from "../derive.js";

// Sessions roster: every Claude Code session the hub knows about, conversation
// or not. Lives in the left rail above the conversation list; independently
// collapsible. Read-only in this chunk - connect/convene actions arrive with
// the convening work.
export function SessionsRail({ store }) {
	const state = store.getState();
	const entries = sortSessionEntries(state.sessions);
	const offline = sensorOffline(state.widget.pushedAt, Date.now());
	const collapsed = state.ui.sessionsCollapsed;

	return html`
		<section class="sessions-rail">
			<div class="sessions-header" onClick=${() => store.toggleSessionsCollapsed()}>
				<span class="sessions-title">Sessions</span>
				<span class="sessions-count">${entries.length}</span>
				<span class="sessions-caret">${collapsed ? "▸" : "▾"}</span>
			</div>
			${collapsed ? null : html`
				${offline ? html`<div class="sessions-sensor-offline">sensor offline - staleness unknown</div>` : null}
				<ul class="sessions-list">
					${entries.map(({ id, record }) => {
						const chip = sessionChip(record);
						const age = formatAge(sessionAgeSeconds(record, Date.now()));
						const ring = record.context_pct != null ? `${Math.round(record.context_pct)}%` : "";
						return html`
							<li class="session-row" key=${id}
								onClick=${() => record.conversation_id && store.selectConversation(record.conversation_id)}
								title=${record.cwd || ""}>
								<span class=${"session-chip " + chip.cls}>${chip.label}</span>
								<span class="session-project">${projectTail(record.cwd) || "(unknown)"}</span>
								<span class="session-meta">
									${record.sender ? html`<span class="session-sender">${record.sender}</span>` : null}
									${ring ? html`<span class="session-ring">${ring}</span>` : null}
									<span class="session-age">${age}</span>
									${record.conversation_id ? html`<span class="session-linked">⇢</span>` : null}
								</span>
							</li>
						`;
					})}
				</ul>
			`}
		</section>
	`;
}
