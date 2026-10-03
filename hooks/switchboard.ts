import type { EngineInterface, HttpResponse, Register } from 'claude-code'
import { atom, read, update } from 'claude-code'

import type { InboxAnswer, StatusBody } from './client'
import { DEFAULT_BASE_URL, parseInboxBody, requestHeaders } from './client'
import { preToolState } from './status'

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

// -- wiring ------------------------------------------------------------------

export const register: Register = on => {
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
		const { tool, tool_use_id: _toolUseId, agentId: _agentId, ...input } = e
		const sessionId = await $.session.id()
		const cwd = await $.session.cwd()
		postStatus($, { session_id: sessionId, cwd, event: 'PreToolUse', ...preToolState(tool, input as Record<string, unknown>) })
		const call = SWITCHBOARD_TOOL.test(tool) ? ({ ...e, cli_session_id: sessionId, cwd } as typeof e) : e
		const ran = await next(call)
		postStatus($, { session_id: sessionId, cwd, event: 'PostToolUse', state: 'thinking' })
		return ran
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
}
