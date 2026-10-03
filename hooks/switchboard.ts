import type { EngineInterface, HttpInit, HttpResponse, McpToolResult, Register, Timer, ToolCallResult } from 'claude-code'
import { atom, read, update } from 'claude-code'

import type { AskQuestion } from './ask'
import { isTerminal, phoneQuestion, raceAbort, replyText } from './ask'
import type { InboxAnswer, StatusBody } from './client'
import { DEFAULT_BASE_URL, FETCH_TIMEOUT_MS, parseInboxBody, requestHeaders } from './client'
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

// $.http.fetch has no timeout of its own, and an awaited $ call does not count
// against a hook's budget, so every server call is bounded here.
async function fetchBounded($: EngineInterface, url: string, init: HttpInit): Promise<HttpResponse> {
	let timer: Timer | undefined
	const timedOut = new Promise<never>((_resolve, reject) => {
		timer = $.clock.after(FETCH_TIMEOUT_MS, () => reject(new Error(`no answer within ${FETCH_TIMEOUT_MS} ms`)))
	})
	try {
		return await Promise.race([$.http.fetch(url, init), timedOut])
	} finally {
		timer?.cancel()
	}
}

async function postJson($: EngineInterface, path: string, body: Record<string, unknown>): Promise<HttpResponse> {
	const res = await fetchBounded($, `${await baseUrl($)}${path}`, {
		method: 'POST',
		headers: await headers($, true),
		body: JSON.stringify(body),
	})
	await noteUnauthorized($, res.status)
	return res
}

async function fetchInbox($: EngineInterface, sessionId: string): Promise<InboxAnswer> {
	const res = await fetchBounded($, `${await baseUrl($)}/sessions/${encodeURIComponent(sessionId)}/inbox`, {
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
// turn-end prompt; a stop cancels the running turn.

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
	await submitPrompt($, items.join('\n\n'), items)
}

// Starts the agent's next turn with `text`, which carries the held `items`. Busy
// first, so the next tick cannot submit the same session twice; a refused
// submit puts the items back for the next poll.
async function submitPrompt($: EngineInterface, text: string, items: readonly string[]): Promise<void> {
	await update($, busy, () => true)
	try {
		const submitted = await $.prompt.submit({ text })
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

// -- AskUserQuestion from the phone --------------------------------------------
// In away mode each question goes to John's phone through ask_human, one after
// another, and his replies come back as the tool's answers.

async function askFromPhone($: EngineInterface, questions: readonly AskQuestion[], signal: AbortSignal): Promise<ToolCallResult> {
	const sessionId = await $.session.id()
	const cwd = await $.session.cwd()
	const answers: Record<string, string> = {}
	for (const [index, q] of questions.entries()) {
		let result: McpToolResult
		try {
			// $.mcp.call is the plugin's own call, so the injector never sees it.
			result = await raceAbort(
				$.mcp.call('switchboard', 'ask_human', {
					question: phoneQuestion(q, index, questions.length),
					suggestions: q.options.map(option => option.label),
					sender: 'Claude',
					cli_session_id: sessionId,
					cwd,
				}),
				signal,
			)
		} catch (error) {
			if (signal.aborted) return { deny: 'The turn was interrupted while the question was on John\'s phone.' }
			return { deny: `Could not reach John's phone through switchboard: ${String(error)}` }
		}
		const reply = replyText(result)
		if (result.isError || isTerminal(reply)) return { deny: `John's phone did not answer this question: ${reply}` }
		answers[q.question] = reply
	}
	return { result: { questions, answers } }
}

// undefined: run the dialog in the terminal as usual (at the desk, or away
// mode unreadable).
async function answerIfAway($: EngineInterface, questions: readonly AskQuestion[], signal: AbortSignal): Promise<ToolCallResult | undefined> {
	let away: boolean
	try {
		const answer = await fetchInbox($, await $.session.id())
		await absorb($, answer)
		away = answer.away
	} catch (error) {
		$.ui.log(`switchboard: could not read away mode, so AskUserQuestion runs in the terminal: ${String(error)}`, { to: 'debug' })
		return undefined
	}
	return away ? askFromPhone($, questions, signal) : undefined
}

// -- turn end ------------------------------------------------------------------
// The Python Stop hook's job. The org's security plugin bypasses a user mod's
// classic.* hooks, so instead of blocking the stop this runs after the turn and
// hands the agent its next turn with $.prompt.submit (never awaited: the next
// turn starts only after this hook returns).

async function checkTurnEnd($: EngineInterface): Promise<void> {
	let answer: InboxAnswer | null = null
	try {
		answer = await fetchInbox($, await $.session.id())
		lastAway = answer.away
		await holdNotices($, answer.notices)
	} catch (error) {
		$.ui.log(`switchboard: turn-end check could not reach the server: ${String(error)}`, { to: 'debug' })
	}
	// A live blocking ask is the agent's handback (the harness backgrounds
	// ask_human calls past ~120s), so it ends the turn quietly.
	if (answer !== null && answer.away && !answer.pending_ask) {
		const items = await takeHeld($)
		const text = turnEndBlock(items, answer)
		if (text !== undefined) {
			void submitPrompt($, text, items)
			return
		}
	}
	await update($, busy, () => false)
	void deliverIfIdle($)
}

// -- wiring ------------------------------------------------------------------

export const register: Register = on => {
	// session.start, not classic.SessionStart (which would carry `source`): the
	// org's security plugin bypasses a user mod's classic.* hooks. /clear raises
	// no session.start, so a cleared session is registered by its first status
	// post. session.start also fires after a hot reload, which restarts the
	// poller; a -p run is about to exit, so a notice popped into it would be lost.
	on('session.start', async ($, e, next) => {
		try {
			await postJson($, '/session_start', { session_id: await $.session.id(), cwd: e.cwd })
		} catch (error) {
			$.ui.log(`switchboard: session start post failed: ${String(error)}`, { to: 'debug' })
		}
		if (e.isInteractive) startPoller($)
		return next(e)
	})

	// A prompt John types while items are held carries them as context.
	on('prompt.submit', async ($, e, next) => {
		const items = await takeHeld($)
		return items.length === 0 ? next(e) : next({ ...e, context: [...(e.context ?? []), ...items] })
	})

	on('tool.call', async ($, e, next) => {
		const { tool, tool_use_id: _toolUseId, agentId, ...input } = e
		const sessionId = await $.session.id()
		const cwd = await $.session.cwd()
		postStatus($, { session_id: sessionId, cwd, event: 'PreToolUse', ...preToolState(tool, input as Record<string, unknown>) })
		const call = SWITCHBOARD_TOOL.test(tool) ? ({ ...e, cli_session_id: sessionId, cwd } as typeof e) : e
		const answered = e.tool === 'AskUserQuestion' ? await answerIfAway($, e.questions, next.signal) : undefined
		const ran = answered ?? (await next(call))
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

	// Main loop only. Busy stays set until the turn-end check decides, so a poll
	// cannot submit alongside it. An interrupted, refused or failed turn gets no
	// check (the Python Stop hook did not fire for those either); the poller
	// delivers anything held once it sees the session idle.
	on('turn.complete', async ($, e, next) => {
		if (e.agentId !== undefined) return next(e)
		await update($, turnId, () => null)
		postStatus($, { session_id: await $.session.id(), cwd: await $.session.cwd(), event: 'Stop', state: 'clear' })
		if (e.reason === 'answer') await checkTurnEnd($)
		else await update($, busy, () => false)
		return next(e)
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
