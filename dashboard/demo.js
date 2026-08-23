// Demo mode for Switchboard Operator. Activated by ?demo in the URL.
// Bypasses Firebase entirely and populates the store with synthetic data.
// All write actions (answer, spawn, away toggle, etc.) are no-ops that
// resolve true so the UI feels responsive without touching any backend.

export function isDemoMode() {
	const params = new URLSearchParams(window.location.search);
	return params.has('demo');
}

// Stub firebase wrapper: every method is a no-op so the store never touches
// the real Firebase SDK. The sign-in and listener methods return unsubscribe
// functions (also no-ops) to satisfy the store's cleanup contracts.
export function createDemoFb() {
	const noop = () => {};
	const noopUnsub = () => noop;
	return {
		initFirebase: noop,
		signIn: () => Promise.resolve(),
		onAuth: noopUnsub,
		onValue: noopUnsub,
		onChildAdded: noopUnsub,
		onChildChanged: noopUnsub,
		onChildRemoved: noopUnsub,
		pushValue: () => Promise.resolve(),
		setValue: () => Promise.resolve(),
		nowIso: () => new Date().toISOString(),
	};
}

// Stub requestStatus for the status-control buttons.
export function demoRequestStatus() {
	return Promise.resolve({ ok: true });
}

// ---------------------------------------------------------------------------
// Synthetic data builders
// ---------------------------------------------------------------------------

function hoursAgo(h) { return new Date(Date.now() - h * 3600_000).toISOString(); }
function minsAgo(m) { return new Date(Date.now() - m * 60_000).toISOString(); }
function daysAgo(d) { return new Date(Date.now() - d * 86_400_000).toISOString(); }
function epochSecsAgo(s) { return Math.floor(Date.now() / 1000) - s; }

const CONV_IDS = [
	'conv-demo-001', 'conv-demo-002', 'conv-demo-003', 'conv-demo-004',
	'conv-demo-005', 'conv-demo-006', 'conv-demo-007', 'conv-demo-008',
	'conv-demo-009', 'conv-demo-010', 'conv-demo-011', 'conv-demo-012',
];

function buildConversationMetas() {
	return {
		[CONV_IDS[0]]: {
			meta: {
				title: 'Virtues of Switchboard',
				state: 'active',
				last_activity_at: epochSecsAgo(12 * 60),
				preview: 'Debating the design merits of Switchboard',
			},
		},
		[CONV_IDS[1]]: {
			meta: {
				title: 'Away-mode enforcement hook',
				state: 'active',
				last_activity_at: epochSecsAgo(35 * 60),
				preview: 'Implementing turn-end hook gating',
			},
		},
		[CONV_IDS[2]]: {
			meta: {
				title: 'Firebase schema migration',
				state: 'active',
				last_activity_at: epochSecsAgo(60 * 60),
				preview: 'Restructuring RTDB paths for conversations',
			},
		},
		[CONV_IDS[3]]: {
			meta: {
				title: 'Watchtower ring rendering',
				state: 'active',
				last_activity_at: epochSecsAgo(6 * 3600),
				preview: 'Context-% ring overlay on the widget',
			},
		},
		[CONV_IDS[4]]: {
			meta: {
				title: 'Dashboard quota readout',
				state: 'active',
				last_activity_at: epochSecsAgo(24 * 3600),
				preview: 'Session and weekly quota bar graphs',
			},
		},
		[CONV_IDS[5]]: {
			meta: {
				title: 'Android FCM notification channels',
				state: 'ended',
				last_activity_at: epochSecsAgo(28 * 3600),
				preview: 'Three-way notification channel split',
			},
		},
		[CONV_IDS[6]]: {
			meta: {
				title: 'Session spawner WSL support',
				state: 'ended',
				last_activity_at: epochSecsAgo(48 * 3600),
				preview: 'WSL surface detection and launcher integration',
			},
		},
		[CONV_IDS[7]]: {
			meta: {
				title: 'Hydration restart resilience',
				state: 'ended',
				last_activity_at: epochSecsAgo(56 * 3600),
				preview: 'Conversation state survives service restart',
			},
		},
		[CONV_IDS[8]]: {
			meta: {
				title: 'Rate limiter token bucket',
				state: 'ended',
				last_activity_at: epochSecsAgo(72 * 3600),
				preview: 'Per-channel rate limiting for notify_human',
			},
		},
		[CONV_IDS[9]]: {
			meta: {
				title: 'CLI session injector hook',
				state: 'ended',
				last_activity_at: epochSecsAgo(84 * 3600),
				preview: 'Injecting cli_session_id into MCP calls',
			},
		},
		[CONV_IDS[10]]: {
			meta: {
				title: 'Conversation combine logic',
				state: 'ended',
				last_activity_at: epochSecsAgo(96 * 3600),
				preview: 'Merging two conversations into one',
			},
		},
		[CONV_IDS[11]]: {
			meta: {
				title: 'Dispatch loop supervision',
				state: 'ended',
				last_activity_at: epochSecsAgo(120 * 3600),
				preview: 'SupervisedListener crash-count and restart',
			},
		},
	};
}

function buildConversation1Members() {
	return {
		'Claude': {
			sender: 'Claude',
			alive: true,
			surface: 'windows',
			cli_session_id: 'demo-1',
		},
		'Antigravity': {
			sender: 'Antigravity',
			alive: true,
			surface: 'windows',
			cli_session_id: 'demo-6',
		},
	};
}

function buildConversation1Messages() {
	// The debate, prompted by John's question about Switchboard's design virtues.
	const base = Date.now() - 12 * 60_000;
	const t = (offsetSec) => new Date(base + offsetSec * 1000).toISOString();

	return {
		'msg-001': {
			sender: 'John',
			text: "What do you see as the most beneficial and or well thought out aspects of Switchboard's design. Debate your reasons with your peer until you come to a consensus.",
			timestamp: t(0),
			format: 'markdown',
		},
		'msg-002': {
			sender: 'Claude',
			text: "I'll open with what I think is the most elegant piece: the **away-mode enforcement model**. The turn-end hook is a genuinely clever solution to a hard UX problem. Rather than requiring John to remember to redirect his agents, the system does it automatically - every agent turn ends by checking a server-side flag, and if John has stepped away, the agent is forced into `ask_human`. It's a single checkpoint that makes the entire phone-based interaction loop possible without any agent cooperation beyond honoring the hook contract.\n\nThe fact that it works across both Claude and Antigravity sessions, with different hook wiring for each, but the same server-side gate - that's good separation of concerns.",
			timestamp: t(30),
			format: 'markdown',
		},
		'msg-003': {
			sender: 'Antigravity',
			text: "That's a solid pick, but I'd argue the **conversation model itself** is the real architectural gem. The `conv-<uuid>` abstraction with its `members_active` roster, pending questions, and message timeline gives you something none of the individual AI coding tools provide on their own: a persistent, multi-agent collaboration surface.\n\nConsider what it enables: two agents from different vendors can work on the same problem, see each other's questions to the human, and coordinate through the shared conversation. The `session_to_conversation_id` routing map means any CLI session can join any conversation, and the hydration layer means conversations survive server restarts. That's a lot of capability from a fairly simple data model.",
			timestamp: t(75),
			format: 'markdown',
		},
		'msg-004': {
			sender: 'Claude',
			text: "I'll grant you the conversation model is powerful, but I'd push back on calling it the *most* well-thought-out piece. Its strength comes from being a natural extension of the away-mode loop. Without the enforcement hook, the conversation model is just a chat log. It's the hook that makes it a live coordination surface.\n\nThat said, you're right that the `members_active` roster with its three-state lifecycle (`alive` / `dormant` / `lost`) is particularly clean. The fact that a session ending marks a member dormant (not dead) and a resume command can bring them back - that's a design that respects the reality of how coding sessions work: they stop and start constantly.",
			timestamp: t(140),
			format: 'markdown',
		},
		'msg-005': {
			sender: 'Antigravity',
			text: "Fair point on the symbiosis between the hook and the model. Let me raise another contender though: the **\"no web UI\" stance that was deliberately reversed for Operator**. The original spec explicitly said \"no web UI\" because the phone is the primary away-mode surface. But when the need for a big-screen control surface became clear, the team didn't bolt on a generic admin panel - they built Operator as a zero-build Preact+htm app that reads Firebase RTDB directly.\n\nNo build step. No bundler. No framework churn. Import maps point at pinned ESM files in `/vendor/`. The entire dashboard is debuggable by reading the source files the browser actually executes. In a world of 400MB `node_modules`, that's a principled choice.",
			timestamp: t(210),
			format: 'markdown',
		},
		'msg-006': {
			sender: 'Claude',
			text: "I appreciate the zero-build stance, but I'd call that a *pragmatic* choice more than a *design* insight. The real design insight in the Operator is the **store pattern**: a single `createStore()` that owns all projected state, with components as pure functions of that state. The store subscribes to Firebase listeners on auth, merges updates through typed setters, and the render loop is just `store.subscribe(draw)`. No virtual DOM diffing framework, no state management library - just a reactive loop that re-renders on every state change.\n\nCombine that with the `commands.js` pattern where every write action is a pure `{path, value}` builder - fully testable without Firebase - and you have a clean separation between \"what to write\" and \"how to write it\".",
			timestamp: t(280),
			format: 'markdown',
		},
		'msg-007': {
			sender: 'Antigravity',
			text: "Now we're converging. I think the through-line in all of these is: **Switchboard treats the human as a first-class participant in the agent loop, not an afterthought.** The away-mode hook makes the human reachable. The conversation model makes the interaction persistent. The phone app makes it mobile. The Operator makes it manageable. The Watchtower widget makes it observable.\n\nEvery layer exists because someone asked \"what does John need to see or do right now?\" and built the minimum viable answer. There's no speculative infrastructure - the Firebase schema evolved through concrete specs, each one solving a real problem that appeared in daily use.",
			timestamp: t(350),
			format: 'markdown',
		},
		'msg-008': {
			sender: 'Claude',
			text: "I think that's the consensus then. The most well-thought-out aspect isn't any single feature - it's the **design discipline of building each layer as a response to a real, observed need**. Away-mode because agents kept running while John was away. Conversations because agents needed shared context. The phone app because the terminal wasn't always visible. Operator because managing it all from the phone was too cramped.\n\nEach spec cites the concrete problem it solves, and each implementation is the smallest thing that addresses it. That's the virtue: pragmatic incrementalism over speculative architecture.",
			timestamp: t(420),
			format: 'markdown',
		},
		'msg-pending': {
			sender: 'Claude',
			text: "Which of these design principles resonates most with your own experience using Switchboard day-to-day? Is there an aspect we missed that you consider particularly well-designed?",
			timestamp: t(450),
			format: 'markdown',
		},
	};
}

function buildConversation1Pending() {
	return {
		'req-demo-001': {
			sender: 'Claude',
			questionText: "Which of these design principles resonates most with your own experience using Switchboard day-to-day? Is there an aspect we missed that you consider particularly well-designed?",
			suggestions: [
				"The away-mode enforcement loop",
				"The conversation model",
				"The pragmatic incrementalism",
			],
			msgId: 'msg-pending',
			askedAt: minsAgo(2),
		},
	};
}

function buildConversation1AgentStatus() {
	return {
		'Claude': {
			state: 'idle',
			detail: null,
			updated_at: Date.now(),
		},
		'Antigravity': {
			state: 'idle',
			detail: null,
			updated_at: Date.now(),
		},
	};
}

// Conversation #2: active singleton, agent thinking
function buildConversation2Members() {
	return {
		'Claude': { sender: 'Claude', alive: true, surface: 'windows', cli_session_id: 'demo-2' },
	};
}

function buildConversation2AgentStatus() {
	return {
		'Claude': { state: 'thinking', detail: null, updated_at: Date.now() },
	};
}

function buildConversation2Messages() {
	return {
		'msg-201': {
			sender: 'Claude',
			text: 'Analyzing the turn-end hook contract to verify the away-mode gating is consistent across both Claude and Antigravity sessions...',
			timestamp: minsAgo(35),
			format: 'markdown',
		},
	};
}

// Conversation #3: active singleton
function buildConversation3Members() {
	return {
		'Antigravity': { sender: 'Antigravity', alive: true, surface: 'windows', cli_session_id: 'demo-3' },
	};
}

function buildConversation3AgentStatus() {
	return {
		'Antigravity': { state: 'running', detail: 'Read: firebase.py', updated_at: Date.now() },
	};
}

function buildConversation3Messages() {
	return {
		'msg-301': {
			sender: 'Antigravity',
			text: 'Reading the current RTDB schema to map out the migration path for the conversation restructuring...',
			timestamp: hoursAgo(1),
			format: 'markdown',
		},
	};
}

// Ended conversations get minimal members (dormant) and a couple of messages
function buildEndedConvMembers(senders) {
	const members = {};
	for (const s of senders) {
		members[s] = { sender: s, alive: false, surface: 'windows' };
	}
	return members;
}

function buildEndedConvMessages(sender, summary, timestamp) {
	return {
		['msg-' + Math.random().toString(36).slice(2, 8)]: {
			sender,
			text: summary,
			timestamp,
			format: 'markdown',
		},
	};
}

// Conversation #4: Watchtower ring rendering
function buildConversation4Members() {
	return {
		'Claude': { sender: 'Claude', alive: true, surface: 'windows', cli_session_id: 'demo-4' },
		'Antigravity': { sender: 'Antigravity', alive: false, surface: 'windows' },
	};
}

function buildConversation4AgentStatus() {
	return {
		'Claude': { state: 'idle', detail: null, updated_at: Date.now() },
	};
}

function buildConversation4Messages() {
	return {
		'msg-401': {
			sender: 'Claude',
			text: 'Completed the context-% ring overlay rendering on the Watchtower widget face.',
			timestamp: hoursAgo(6),
			format: 'markdown',
		},
	};
}

// Conversation #5: Dashboard quota readout
function buildConversation5Members() {
	return {
		'Claude': { sender: 'Claude', alive: true, surface: 'windows', cli_session_id: 'demo-5' },
	};
}

function buildConversation5AgentStatus() {
	return {
		'Claude': { state: 'idle', detail: null, updated_at: Date.now() },
	};
}

function buildConversation5Messages() {
	return {
		'msg-501': {
			sender: 'Claude',
			text: 'Finished the dual-bar quota readout with session and weekly breakdown.',
			timestamp: daysAgo(1),
			format: 'markdown',
		},
	};
}

// ---------------------------------------------------------------------------
// Session data (mirrors Watchtower demo)
// ---------------------------------------------------------------------------

// Session titles are what Watchtower reads out of each Claude Code transcript,
// and what the server adopts as the conversation title for a solo room. The
// names below therefore match their conversation's title: that equality IS the
// feature, so a demo that omitted it would show the one thing it cannot check.
// demo-1 and demo-6 are the two live agents of the debate; a shared room keeps
// its own title and gets no single-agent chip, so neither name matches it.
function buildSessions() {
	return {
		'demo-1': {
			name: 'Switchboard design virtues',
			name_source: 'ai-title',
			sender: 'Claude',
			cwd: 'C:\\Work\\Switchboard',
			state: 'active',
			last_event_at: minsAgo(2),
			conversation_id: CONV_IDS[0],
			context_pct: 0.56,
			model: 'claude-3-7-fable',
		},
		'demo-6': {
			name: 'Critique of the conversation model',
			name_source: 'ai-title',
			sender: 'Antigravity',
			cwd: 'C:\\Work\\Switchboard',
			state: 'active',
			last_event_at: minsAgo(4),
			conversation_id: CONV_IDS[0],
			context_pct: 0.44,
			model: 'gemini-2.5-pro',
		},
		'demo-2': {
			name: 'Away-mode enforcement hook',
			name_source: 'ai-title',
			sender: 'Claude',
			cwd: 'C:\\Work\\Switchboard',
			state: 'active',
			last_event_at: minsAgo(35),
			conversation_id: CONV_IDS[1],
			context_pct: 0.47,
			model: 'claude-3-5-sonnet',
		},
		'demo-3': {
			name: 'Firebase schema migration',
			name_source: 'custom-title',
			sender: 'Antigravity',
			cwd: 'C:\\Work\\Switchboard',
			state: 'active',
			last_event_at: hoursAgo(1),
			conversation_id: CONV_IDS[2],
			context_pct: 0.38,
			model: 'gemini-2.5-pro',
		},
		'demo-4': {
			name: 'Watchtower ring rendering',
			name_source: 'ai-title',
			sender: 'Claude',
			cwd: 'C:\\Work\\Switchboard',
			state: 'active',
			last_event_at: hoursAgo(2),
			conversation_id: CONV_IDS[3],
			context_pct: 0.22,
			model: 'claude-3-opus',
		},
		'demo-5': {
			name: 'Dashboard quota readout',
			name_source: 'ai-title',
			sender: 'Claude',
			cwd: 'C:\\Work\\Switchboard',
			state: 'idle',
			last_event_at: hoursAgo(3),
			conversation_id: CONV_IDS[4],
			context_pct: 0.12,
			model: 'claude-3-5-haiku',
		},
	};
}

// ---------------------------------------------------------------------------
// Widget data (rings, quota)
// ---------------------------------------------------------------------------

function buildWidgetRings() {
	return {
		'demo-1': { pct: 0.56, tokens: 112000, limit: 200000 },
		'demo-2': { pct: 0.47, tokens: 94000, limit: 200000 },
		'demo-3': { pct: 0.38, tokens: 380000, limit: 1000000 },
		'demo-4': { pct: 0.22, tokens: 44000, limit: 200000 },
		'demo-5': { pct: 0.12, tokens: 24000, limit: 200000 },
	};
}

function buildWidgetQuota() {
	const now = Date.now();
	return {
		session: {
			pct: 0.84,
			resets_at: new Date(now + 1.5 * 3600_000).toISOString(),
		},
		weekly: {
			pct: 0.85,
			resets_at: new Date(now + 15.12 * 3600_000).toISOString(),
		},
		antigravity: [
			{
				display_name: 'Gemini 2.5 Pro',
				session: { pct: 0.45, resets_at: new Date(now + 1.9 * 3600_000).toISOString() },
				weekly: { pct: 0.72, resets_at: new Date(now + 35.3 * 3600_000).toISOString() },
			},
			{
				display_name: 'Claude 3.7 Sonnet',
				session: { pct: 0.76, resets_at: new Date(now + 2.1 * 3600_000).toISOString() },
				weekly: { pct: 0.88, resets_at: new Date(now + 10.1 * 3600_000).toISOString() },
			},
		],
	};
}

// ---------------------------------------------------------------------------
// Main entry point: populate the store with all synthetic data
// ---------------------------------------------------------------------------

export function applyDemoData(store) {
	// Auth gate bypass
	store.setAuthed(true, { displayName: 'Demo User', email: 'demo@switchboard.local' });

	// Global state
	store.setGlobalAway(false);
	store.setHealth({ reachable: true, healthy: true, totalAnswered: 47 });

	// Conversations
	const convMetas = buildConversationMetas();
	for (const [id, data] of Object.entries(convMetas)) {
		store.upsertConversationMeta(id, data.meta);
	}

	// Active conversation #1: the debate (collab: Claude + Antigravity)
	store.mergeConversationMembers(CONV_IDS[0], buildConversation1Members());
	store.mergeConversationMessages(CONV_IDS[0], buildConversation1Messages());
	store.mergeConversationPending(CONV_IDS[0], buildConversation1Pending());
	store.mergeConversationAgentStatus(CONV_IDS[0], buildConversation1AgentStatus());

	// Active conversation #2: singleton, agent thinking
	store.mergeConversationMembers(CONV_IDS[1], buildConversation2Members());
	store.mergeConversationMessages(CONV_IDS[1], buildConversation2Messages());
	store.mergeConversationAgentStatus(CONV_IDS[1], buildConversation2AgentStatus());

	// Active conversation #3: singleton, agent running
	store.mergeConversationMembers(CONV_IDS[2], buildConversation3Members());
	store.mergeConversationMessages(CONV_IDS[2], buildConversation3Messages());
	store.mergeConversationAgentStatus(CONV_IDS[2], buildConversation3AgentStatus());

	// Active conversation #4: singleton, agent idle
	store.mergeConversationMembers(CONV_IDS[3], buildConversation4Members());
	store.mergeConversationMessages(CONV_IDS[3], buildConversation4Messages());
	store.mergeConversationAgentStatus(CONV_IDS[3], buildConversation4AgentStatus());

	// Active conversation #5: singleton, agent idle
	store.mergeConversationMembers(CONV_IDS[4], buildConversation5Members());
	store.mergeConversationMessages(CONV_IDS[4], buildConversation5Messages());
	store.mergeConversationAgentStatus(CONV_IDS[4], buildConversation5AgentStatus());

	// Ended conversations with minimal data
	const endedData = [
		{ idx: 5, senders: ['Claude'], msg: 'Set up three FCM notification channels: questions, status, and admin.', ts: hoursAgo(28) },
		{ idx: 6, senders: ['Antigravity'], msg: 'Added WSL surface detection to the session spawner with launcher integration.', ts: daysAgo(2) },
		{ idx: 7, senders: ['Claude'], msg: 'Implemented conversation hydration so state survives service restarts.', ts: hoursAgo(56) },
		{ idx: 8, senders: ['Claude', 'Antigravity'], msg: 'Built per-channel token-bucket rate limiter for notify_human and send_document_human.', ts: daysAgo(3) },
		{ idx: 9, senders: ['Claude'], msg: 'Wired the cli-session-injector hook to inject session_id into every MCP tool call.', ts: hoursAgo(84) },
		{ idx: 10, senders: ['Antigravity'], msg: 'Implemented combine_conversations with member migration and queue-for-intro logic.', ts: daysAgo(4) },
		{ idx: 11, senders: ['Claude'], msg: 'Built SupervisedListener and LoopSupervisor for Firebase listener crash recovery.', ts: daysAgo(5) },
	];

	for (const { idx, senders, msg, ts } of endedData) {
		store.mergeConversationMembers(CONV_IDS[idx], buildEndedConvMembers(senders));
		store.mergeConversationMessages(CONV_IDS[idx], buildEndedConvMessages(senders[0], msg, ts));
	}

	// Sessions (mirrors Watchtower demo)
	store.setSessions(buildSessions());

	// Widget data
	store.setWidgetRings(buildWidgetRings());
	store.setWidgetQuota(buildWidgetQuota());
	store.setWidgetPushedAt(new Date().toISOString());

	// Auto-select conversation #1
	store.selectConversation(CONV_IDS[0]);
}
