import type { On } from 'claude-code'
import type { Engine } from 'claude-code/testing'
import { mock } from 'claude-code/testing'

// A fake world beneath the switchboard mod for `claude plugin test`. Set its
// fields to shape what the "engine" answers; read them to see what the mod did.

export type Sent = { url: string; method: string; headers: Record<string, string>; body: Record<string, unknown> | null }

export type World = {
	clock: ReturnType<typeof mock.clock>
	sessionId: string
	draft: string
	inbox: Array<Record<string, unknown>>
	inboxStatus: number
	inboxRaw: string | null
	fetchError: string | null
	hangFetch: boolean
	rejectSubmit: boolean
	checkDecision: 'allow' | 'ask' | 'deny'
	mcpReplies: string[]
	sent: Sent[]
	prompts: Array<{ text: string; context: readonly string[] }>
	aborted: string[]
	logs: Array<{ text: string; to: string }>
	mcpCalls: Array<{ server: string; tool: string; args: Record<string, unknown> }>
	toolInputs: Array<Record<string, unknown>>
}

export function world(on: On, env: Record<string, string> = {}): World {
	const w: World = {
		clock: mock.clock(on),
		sessionId: 'S1',
		draft: '',
		inbox: [],
		inboxStatus: 200,
		inboxRaw: null,
		fetchError: null,
		hangFetch: false,
		rejectSubmit: false,
		checkDecision: 'allow',
		mcpReplies: [],
		sent: [],
		prompts: [],
		aborted: [],
		logs: [],
		mcpCalls: [],
		toolInputs: [],
	}
	mock.env(on, env)
	on('session.id', () => ({ value: w.sessionId }))
	on('session.cwd', () => ({ value: 'C:/Work/X' }))
	on('session.start', (_$, e) => ({ cwd: e.cwd }))
	on('session.end', (_$, e) => ({ sessionId: e.sessionId }))
	on('classic.SessionStart', () => ({}))
	on('classic.Stop', () => ({}))
	on('turn.start', (_$, e) => ({ turnId: e.turnId }))
	on('turn.complete', (_$, e) => ({ text: e.answer }))
	on('tool.check', () => ({ decision: w.checkDecision }))
	on('tool.call', (_$, e) => {
		w.toolInputs.push({ ...e })
		return { result: 'ran' }
	})
	on('prompt.read', () => ({ value: { text: w.draft, cursor: w.draft.length } }))
	on('prompt.submit', (_$, e) => {
		if (w.rejectSubmit) throw new Error('engine refused the prompt')
		w.prompts.push({ text: e.text, context: e.context ?? [] })
		return { text: e.text }
	})
	on('turn.abort', (_$, e) => {
		w.aborted.push(e.turnId)
		return { value: undefined }
	})
	on('ui.log', (_$, e) => {
		w.logs.push({ text: e.text, to: e.to })
		return { value: undefined }
	})
	on('mcp.call', (_$, e) => {
		w.mcpCalls.push({ server: e.server, tool: e.tool, args: e.args })
		return { value: { content: [{ type: 'text', text: w.mcpReplies.shift() ?? '' }], isError: false } }
	})
	on('http.fetch', (_$, e) => {
		const body = e.init?.body === undefined ? null : (JSON.parse(e.init.body) as Record<string, unknown>)
		w.sent.push({ url: e.url, method: e.init?.method ?? 'GET', headers: e.init?.headers ?? {}, body })
		if (w.fetchError !== null) return { deny: w.fetchError }
		// A server that accepts the connection and never answers.
		if (w.hangFetch) return new Promise<never>(() => {})
		if (!e.url.includes('/inbox')) return { value: { status: 200, ok: true, headers: {}, text: '{}' } }
		const answer = { notices: [], stop: false, away: false, pending_ask: false, ...(w.inbox.shift() ?? {}) }
		const ok = w.inboxStatus >= 200 && w.inboxStatus < 300
		return { value: { status: w.inboxStatus, ok, headers: {}, text: w.inboxRaw ?? JSON.stringify(answer) } }
	})
	return w
}

export function inboxPolls(w: World): Sent[] {
	return w.sent.filter(s => s.url.includes('/inbox'))
}

export function postsTo(w: World, path: string): Sent[] {
	return w.sent.filter(s => s.method === 'POST' && s.url.endsWith(path))
}

// An interactive session, idle, with its poller started.
export async function startSession($: Engine, w: World): Promise<void> {
	await $.session.start({ cwd: 'C:/Work/X', surface: 'terminal', isInteractive: true })
	await w.clock.settle()
}
