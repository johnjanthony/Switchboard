import type { EngineInterface, HttpInit, HttpResponse, McpToolResult, Register, Timer, ToolCallResult } from 'claude-code'
import { atom, read, update } from 'claude-code'

import type { AskQuestion } from './ask'
import { backgroundedDeny, backgroundedTaskId, isTerminal, phoneQuestion, raceAbort, replyText } from './ask'
import type { InboxAnswer, StatusBody } from './client'
import { DEFAULT_BASE_URL, FETCH_TIMEOUT_MS, parseInboxBody, requestHeaders } from './client'
import type { ModFile } from './fingerprint'
import { fromBase64, modFingerprint } from './fingerprint'
import { backoffMs, POLL_MS } from './inbox'
import { preToolState } from './status'
import type { Title } from './telemetry'
import { claudeConfigDir, effortLevel, latestTitle, measureReport } from './telemetry'
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

// -- telemetry ---------------------------------------------------------------
// The session's context, rate limits, model, effort and title, reported to the
// server as they change. Kept per session id, so after a /clear (a new id with
// no session.start) the new session reports afresh.

type Telemetry = {
	sessionId: string
	model?: string
	effort?: string | null
	title?: string
	// No more reads after turns: a title was found, or the transcript could not be read.
	titleDone: boolean
	// A model request has run since the rate limits were last sent, so the next
	// measurement's rate limits come from a fresh API response.
	freshLimits: boolean
	transcript?: string
}

let telemetry: Telemetry | undefined
let telemetryFailing = false

async function telemetryFor($: EngineInterface): Promise<Telemetry> {
	const sessionId = await $.session.id()
	if (telemetry?.sessionId !== sessionId) {
		telemetry = { sessionId, titleDone: false, freshLimits: false }
		void reportFingerprint($, sessionId)
	}
	return telemetry
}

// Fire-and-forget like the status posts, with no retry: the next measurement
// carries fresh figures. A failing server is logged once per failure streak.
function postTelemetry($: EngineInterface, sessionId: string, report: Record<string, unknown>): void {
	if (Object.keys(report).length === 0) return
	const failed = (reason: string): void => {
		if (!telemetryFailing) $.ui.log(`switchboard: telemetry post failed: ${reason}`, { to: 'debug' })
		telemetryFailing = true
	}
	postJson($, `/sessions/${encodeURIComponent(sessionId)}/telemetry`, report).then(
		res => {
			if (res.ok) telemetryFailing = false
			else failed(`answered ${res.status}`)
		},
		error => failed(String(error)),
	)
}

// The mod's own top-level hooks/*.ts, read as bytes so the digest matches the
// server's over the repo copy whatever the files' encoding.
async function readModFiles($: EngineInterface): Promise<ModFile[]> {
	const dir = `${$.plugin.root}/hooks`
	const files: ModFile[] = []
	for (const entry of await $.fs.list(dir)) {
		if (entry.kind !== 'file' || !entry.name.endsWith('.ts')) continue
		const { base64 } = await $.fs.read(`${dir}/${entry.name}`, { as: 'bytes' })
		files.push({ name: entry.name, bytes: fromBase64(base64) })
	}
	return files
}

// Once per session id (telemetryFor), so a /clear's new id reports too. The
// server compares it with the repo's plugin/hooks and flags an older mod.
async function reportFingerprint($: EngineInterface, sessionId: string): Promise<void> {
	try {
		const fingerprint = await modFingerprint(await readModFiles($))
		if (fingerprint !== undefined) postTelemetry($, sessionId, { mod_fingerprint: fingerprint })
	} catch (error) {
		$.ui.log(`switchboard: could not fingerprint the mod: ${String(error)}`, { to: 'debug' })
	}
}

async function noteModel($: EngineInterface, model: string, effort: string | null): Promise<void> {
	const t = await telemetryFor($)
	if (t.model === model && t.effort === effort) return
	t.model = model
	t.effort = effort
	postTelemetry($, t.sessionId, { model, effort })
}

async function findTranscript($: EngineInterface, sessionId: string): Promise<string | undefined> {
	const base = claudeConfigDir(await $.env.get('CLAUDE_CONFIG_DIR'), await $.env.get('HOME'), await $.env.get('USERPROFILE'))
	if (base === undefined) return undefined
	for (const entry of await $.fs.list(`${base}/projects`)) {
		if (entry.kind !== 'dir') continue
		const path = `${base}/projects/${entry.name}/${sessionId}.jsonl`
		if (await $.fs.exists(path)) return path
	}
	return undefined
}

// Reads the transcript for the session's latest title. $.fs.read refuses a file
// over 4 MiB, which ends the search for this session (the server keeps the name
// a resumed session already had); a /rename then falls back to its own args.
async function refreshTitle($: EngineInterface, renamedTo = ''): Promise<void> {
	const t = await telemetryFor($)
	let title: Title | undefined
	try {
		t.transcript ??= await findTranscript($, t.sessionId)
		if (t.transcript === undefined) return
		title = latestTitle(await $.fs.read(t.transcript))
	} catch (error) {
		t.titleDone = true
		$.ui.log(`switchboard: could not read the transcript for its title: ${String(error)}`, { to: 'debug' })
		if (renamedTo.trim() !== '') title = { name: renamedTo.trim(), source: 'custom-title' }
	}
	if (title === undefined) return
	t.titleDone = true
	const key = `${title.source}\n${title.name}`
	if (t.title === key) return
	t.title = key
	postTelemetry($, t.sessionId, { title })
}

// -- inbox -------------------------------------------------------------------
// The 2 s inbox poll and the delivery rules: idle, held items become a prompt;
// busy, they ride the next main-loop tool result, a typed prompt, or the
// turn-end prompt; a stop cancels the running turn.

// The poller's own bookkeeping. Module variables on purpose: a hot reload
// starts a fresh module, and the old module's timer is dropped with it.
let pollerStarted = false
let isPolling = false
let failures = 0
let resumeAt = 0
let lastAway = false

// Once per module load, whichever hook gets here first: two timers would
// double every poll and every delivery.
function startPoller($: EngineInterface): void {
	if (pollerStarted) return
	pollerStarted = true
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
		const taskId = backgroundedTaskId(reply)
		if (taskId !== undefined) return { deny: backgroundedDeny(questions, index, taskId, answers) }
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
	// post. A terminal polls from the start and the desktop app's session once
	// the app attaches; a -p run has neither and never polls, since a notice
	// popped into a run about to exit would be lost. session.start also fires
	// after a hot reload, when session.attach does not, so a surface already
	// attached starts the poller here too.
	on('session.start', async ($, e, next) => {
		try {
			await postJson($, '/session_start', { session_id: await $.session.id(), cwd: e.cwd })
		} catch (error) {
			$.ui.log(`switchboard: session start post failed: ${String(error)}`, { to: 'debug' })
		}
		// A resumed session's transcript may already hold its title.
		void refreshTitle($)
		if (e.isInteractive || (await $.session.surfaces()).length > 0) startPoller($)
		return next(e)
	})

	// The desktop app attaches as a surface after its session starts.
	on('session.attach', async ($, e, next) => {
		startPoller($)
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
		// Claude Code writes the AI title after the first turn; read until found.
		if (!(await telemetryFor($)).titleDone) void refreshTitle($)
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

	// After each main-thread turn, and when a rate-limit window moves a point.
	// It can fire before session.start; the server creates the record. The rate
	// limits go only when a model request has run since they last went: the
	// server takes every report carrying them as confirming the quota reading.
	on('session.measure', async ($, e, next) => {
		const t = await telemetryFor($)
		const report = measureReport(e, t.freshLimits)
		if ('rate_limits' in report) t.freshLimits = false
		postTelemetry($, t.sessionId, report)
		return next(e)
	})

	// Observe only: every chunk streams through untouched. Any step, a
	// subagent's included, means a fresh API response is coming, so the next
	// measurement's rate limits are current. Only the main thread's model and
	// effort are reported, since a subagent may run on another model.
	on('turn.step', async function* ($, e, next) {
		const t = await telemetryFor($)
		t.freshLimits = true
		if (e.agentId === undefined) await noteModel($, e.model, effortLevel(e.effort))
		return yield* next(e)
	})

	// A /rename has written its custom-title row by the time its command returns.
	on('command.run', { command: 'rename' }, async ($, e, next) => {
		const ran = await next(e)
		void refreshTitle($, e.args)
		return ran
	})
}
