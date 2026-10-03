import type { EngineInterface, HttpResponse, Register, ToolCallResult } from 'claude-code'
import { atom, read, update } from 'claude-code'

import type { InboxAnswer, StatusBody } from './client'
import { DEFAULT_BASE_URL, parseInboxBody, requestHeaders } from './client'
import { backoffMs, POLL_MS } from './inbox'
import { preToolState } from './status'
import { turnEndBlock } from './turn-end'

const SWITCHBOARD_TOOL = /^mcp__switchboard__/

// The switchboard plugin's Claude Code mod: the session's link to the
// switchboard server. Every function that takes `$`, and every $.state atom,
// lives in this file, because the engine's load-time scan follows `$` and state
// references only within the hooks module; the pure logic sits beside it.

// -- session state (contract: types/index.d.ts) -------------------------------
// $.state survives a hot reload; module variables do not.

// Inbox notices received but not yet delivered to the model.
const held = atom({ plugin: 'switchboard', key: 'held' } as const, [] as string[])
// True from a main-loop turn's start (or the mod's own submit) to its turn.complete.
const busy = atom({ plugin: 'switchboard', key: 'busy' } as const, false)
// The running main-loop turn, for $.turn.abort.
const turnId = atom({ plugin: 'switchboard', key: 'turnId' } as const, null as string | null)
// Whether this session has already been told about a 401.
const warned401 = atom({ plugin: 'switchboard', key: 'warned401' } as const, false)

// -- server client -----------------------------------------------------------

async function baseUrl($: EngineInterface): Promise<string> {
	return (await $.env.get('SWITCHBOARD_BASE_URL')) ?? DEFAULT_BASE_URL
}

async function headers($: EngineInterface, hasBody: boolean): Promise<Record<string, string>> {
	return requestHeaders(await $.env.get('SWITCHBOARD_TOKEN'), hasBody)
}

// A 401 means phone messages can never reach this session, so it is the one
// server failure written to the transcript, once per session.
async function noteUnauthorized($: EngineInterface, status: number): Promise<void> {
	if (status !== 401 || (await read($, warned401))) return
	await update($, warned401, () => true)
	$.ui.log('switchboard: the server refused this session (401). Set SWITCHBOARD_TOKEN to the server token; until then phone messages cannot reach this session.')
}

async function postJson($: EngineInterface, path: string, body: Record<string, unknown>): Promise<HttpResponse> {
	const res = await $.http.fetch(`${await baseUrl($)}${path}`, {
		method: 'POST',
		headers: await headers($, true),
		body: JSON.stringify(body),
	})
	await noteUnauthorized($, res.status)
	return res
}

async function fetchInbox($: EngineInterface, sessionId: string): Promise<InboxAnswer> {
	const res = await $.http.fetch(`${await baseUrl($)}/sessions/${encodeURIComponent(sessionId)}/inbox`, {
		headers: await headers($, false),
	})
	await noteUnauthorized($, res.status)
	if (!res.ok) throw new Error(`inbox answered ${res.status}`)
	return parseInboxBody(res.text)
}

// Fire-and-forget: a slow or absent server must never delay a tool call.
function postStatus($: EngineInterface, body: StatusBody): void {
	postJson($, '/agent_status', body).catch(error =>
		$.ui.log(`switchboard: status post failed: ${String(error)}`, { to: 'debug' }),
	)
}

// -- inbox -------------------------------------------------------------------
// The 2 s inbox poll and the delivery rules: idle, held items become a prompt;
// busy, they ride the next main-loop tool result, a typed prompt, or the
// turn-end block; a stop cancels the running turn.

// The poller's own bookkeeping. Module variables on purpose: a hot reload
// starts a fresh poller, and the old module's timer is dropped with it.
let isPolling = false
let failures = 0
let resumeAt = 0
let lastAway = false

function startPoller($: EngineInterface): void {
	isPolling = false
	failures = 0
	resumeAt = 0
	$.clock.every(POLL_MS, () => {
		void tick($)
	})
}

async function tick($: EngineInterface): Promise<void> {
	if (isPolling) return
	const now = await $.clock.now()
	if (now < resumeAt) return
	isPolling = true
	try {
		const answer = await fetchInbox($, await $.session.id())
		failures = 0
		resumeAt = 0
		await absorb($, answer)
		await deliverIfIdle($)
	} catch (error) {
		failures += 1
		resumeAt = now + backoffMs(failures)
		$.ui.log(`switchboard: inbox poll failed (${failures} in a row): ${String(error)}`, { to: 'debug' })
	} finally {
		isPolling = false
	}
}

async function holdNotices($: EngineInterface, notices: readonly string[]): Promise<void> {
	if (notices.length > 0) await update($, held, list => [...list, ...notices])
}

async function absorb($: EngineInterface, answer: InboxAnswer): Promise<void> {
	lastAway = answer.away
	await holdNotices($, answer.notices)
	if (answer.stop) await stopRunningTurn($)
}

// A stop applies only to a turn running now; one that finds the session idle
// is dropped, so it can never cancel a turn John starts later.
async function stopRunningTurn($: EngineInterface): Promise<void> {
	const running = await read($, turnId)
	if (!(await read($, busy)) || running === null) return
	try {
		await $.turn.abort({ turnId: running })
		$.ui.log('Stopped from phone')
	} catch (error) {
		$.ui.log(`switchboard: stop from phone did not apply: ${String(error)}`, { to: 'debug' })
	}
}

async function takeHeld($: EngineInterface): Promise<string[]> {
	let taken: string[] = []
	await update($, held, list => {
		taken = list
		return []
	})
	return taken
}

async function deliverIfIdle($: EngineInterface): Promise<void> {
	if (await read($, busy)) return
	if ((await read($, held)).length === 0) return
	// At the desk John's half-typed prompt wins; the items ride on it when sent.
	if (!lastAway && (await $.prompt.read()).text.trim() !== '') return
	const items = await takeHeld($)
	if (items.length === 0) return
	// Busy first: the next tick must not submit the same session twice.
	await update($, busy, () => true)
	try {
		const submitted = await $.prompt.submit({ text: items.join('\n\n') })
		if ('drop' in submitted) {
			await update($, busy, () => false)
			$.ui.log('switchboard: a hook dropped the phone-message prompt', { to: 'debug' })
		}
	} catch (error) {
		await update($, held, list => [...items, ...list])
		await update($, busy, () => false)
		$.ui.log(`switchboard: could not submit a phone message: ${String(error)}`, { to: 'debug' })
	}
}

async function withHeldContext($: EngineInterface, ran: ToolCallResult): Promise<ToolCallResult> {
	if ('deny' in ran && ran.deny !== undefined) return ran
	const items = await takeHeld($)
	if (items.length === 0) return ran
	return { ...ran, context: [...(ran.context ?? []), ...items] }
}

// -- wiring ------------------------------------------------------------------

export const register: Register = on => {
	// session.start also fires after a hot reload, which restarts the poller.
	// A -p run is about to exit, so a notice popped into it would be lost.
	on('session.start', async ($, e, next) => {
		if (e.isInteractive) startPoller($)
		return next(e)
	})

	// A prompt John types while items are held carries them as context.
	on('prompt.submit', async ($, e, next) => {
		const items = await takeHeld($)
		return items.length === 0 ? next(e) : next({ ...e, context: [...(e.context ?? []), ...items] })
	})

	// classic.SessionStart, not session.start: only it carries `source`
	// (resume, clear), and /clear raises no session.start at all.
	on('classic.SessionStart', async ($, e, next) => {
		try {
			await postJson($, '/session_start', { session_id: e.session_id, cwd: e.cwd, source: e.source })
		} catch (error) {
			$.ui.log(`switchboard: session start post failed: ${String(error)}`, { to: 'debug' })
		}
		return next(e)
	})

	// The Python injector answered permissionDecision "allow"; this keeps the
	// switchboard tools pre-approved in sessions that are not in bypass mode.
	on('tool.check', ($, e, next) =>
		SWITCHBOARD_TOOL.test(e.tool)
			? { decision: 'allow', reason: 'The switchboard plugin pre-approves its own tools.' }
			: next(e),
	)

	on('tool.call', async ($, e, next) => {
		const { tool, tool_use_id: _toolUseId, agentId, ...input } = e
		const sessionId = await $.session.id()
		const cwd = await $.session.cwd()
		postStatus($, { session_id: sessionId, cwd, event: 'PreToolUse', ...preToolState(tool, input as Record<string, unknown>) })
		const call = SWITCHBOARD_TOOL.test(tool) ? ({ ...e, cli_session_id: sessionId, cwd } as typeof e) : e
		const ran = await next(call)
		postStatus($, { session_id: sessionId, cwd, event: 'PostToolUse', state: 'thinking' })
		// Main loop only: a subagent must never receive John's message.
		return agentId === undefined ? withHeldContext($, ran) : ran
	})

	// turn.start fires for the main loop only (a subagent's run raises none).
	on('turn.start', async ($, e, next) => {
		await update($, busy, () => true)
		await update($, turnId, () => e.turnId)
		postStatus($, { session_id: await $.session.id(), cwd: await $.session.cwd(), event: 'UserPromptSubmit', state: 'thinking' })
		return next(e)
	})

	on('turn.complete', async ($, e, next) => {
		if (e.agentId === undefined) {
			await update($, busy, () => false)
			await update($, turnId, () => null)
		}
		return next(e)
	})

	// Same contract as scripts/turn-end-hook-away-mode.py. A stop found here is
	// dropped (the turn is ending anyway), so this reads notices only.
	on('classic.Stop', async ($, e, next) => {
		postStatus($, { session_id: e.session_id, cwd: e.cwd, event: 'Stop', state: 'clear' })
		let answer: InboxAnswer | null = null
		try {
			const fetched = await fetchInbox($, e.session_id)
			await holdNotices($, fetched.notices)
			answer = fetched
		} catch (error) {
			$.ui.log(`switchboard: turn-end check could not reach the server: ${String(error)}`, { to: 'debug' })
		}
		const block = turnEndBlock(await takeHeld($), answer)
		const result = await next(e)
		if (block === undefined) return result
		return { ...result, block: result.block === undefined ? block : `${result.block}\n\n${block}` }
	})

	// Awaited inside session.end's short bound: unlike a classic SessionEnd
	// hook, this POST lands before the process exits. The silence sweep is the
	// backstop for one that does not.
	on('session.end', async ($, e, next) => {
		try {
			await postJson($, '/session_end', { session_id: e.sessionId, reason: e.reason })
		} catch (error) {
			$.ui.log(`switchboard: session end post failed: ${String(error)}`, { to: 'debug' })
		}
		return next(e)
	})
}
