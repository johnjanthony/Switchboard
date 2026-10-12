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
	dirs: string[]
	files: Record<string, string>
	modFiles: Record<string, string>
	readError: string | null
	reads: string[]
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
		dirs: [],
		files: {},
		modFiles: {},
		readError: null,
		reads: [],
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
	on('session.measure', (_$, e) => ({ changed: e.changed }))
	on('command.run', (_$, e) => ({ text: `ran ${e.command}` }))
	on('turn.step', async function* (_$, e) {
		return { turnId: e.turnId, index: e.index, answer: '', toolUses: [], stopReason: 'end_turn' as const, usage: null }
	})
	// The engine hands fs handlers native paths (backslashes on Windows); the
	// world keys files by the forward-slash paths the mod builds.
	const slashed = (path: string): string => path.replaceAll('\\', '/')
	// The mod lists and reads its own hooks folder (under $.plugin.root, the
	// real plugin folder even in tests) for its fingerprint; modFiles answers
	// any path ending /hooks, so tests need not know the root.
	on('fs.list', (_$, e) => {
		if (slashed(e.path).endsWith('/hooks')) {
			return { value: Object.entries(w.modFiles).map(([name, text]) => ({ name, kind: 'file' as const, size: text.length, mtimeMs: 0, isLink: false })) }
		}
		return { value: w.dirs.map(name => ({ name, kind: 'dir' as const, size: 0, mtimeMs: 0, isLink: false })) }
	})
	on('fs.exists', (_$, e) => ({ value: slashed(e.path) in w.files }))
	on('fs.read', (_$, e) => {
		const path = slashed(e.path)
		w.reads.push(path)
		if (w.readError !== null) return { deny: w.readError }
		const name = path.slice(path.lastIndexOf('/') + 1)
		const mod = w.modFiles[name]
		if (e.as === 'bytes' && path.endsWith(`/hooks/${name}`) && mod !== undefined) return { value: { base64: btoa(mod) } }
		const text = w.files[path]
		return text === undefined ? { deny: `no such file: ${path}` } : { value: text }
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

// One model request through the turn.step chain. The test engine leaves a
// stream's `result` undefined, so the stream is drained by hand.
export async function step(
	$: Engine,
	input: { model: string; effort?: 'low' | 'medium' | 'high' | 'xhigh' | 'max' | number; agentId?: string },
): Promise<void> {
	const stream = $.turn.step({ turnId: 'T1', index: 0, messageCount: 1, ...input })
	let chunk = await stream.next()
	while (!chunk.done) chunk = await stream.next()
}

export async function runCommand($: Engine, command: string, args = ''): Promise<void> {
	await $.command.run({ command, args, origin: { kind: 'composer' }, presentation: { isFullscreen: false, columns: 80 } })
}
