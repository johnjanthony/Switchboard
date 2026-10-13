import type { Engine } from 'claude-code/testing'
import { describe, expect, test } from 'claude-code/testing'

import { askedJohn, isOwnRedirect, REDIRECT_CAP, REDIRECT_CAP_NOTICE, REDIRECT_REASON_AWAY_MODE, redirectPlan } from '../turn-end'
import { inboxPolls, postsTo, startSession, world } from './harness'

const R = REDIRECT_REASON_AWAY_MODE

describe('redirectPlan (an away-mode turn end with no live ask)', () => {
	test('below the cap: the bare redirect, counted', () => {
		expect(redirectPlan([], 0)).toEqual({ submit: R, redirects: 1 })
		expect(redirectPlan([], 1)).toEqual({ submit: R, redirects: 2 })
	})

	test('at the cap: stop and tell John, once', () => {
		expect(REDIRECT_CAP).toBe(2)
		expect(redirectPlan([], 2)).toEqual({ cap: true, redirects: 3 })
		expect(redirectPlan([], 3)).toEqual({ quiet: true, redirects: 3 })
	})

	test('held items always go, ahead of the redirect, whatever the count', () => {
		expect(redirectPlan(['a', 'b'], 3)).toEqual({ submit: `a\n\nb\n\n${R}`, redirects: 3 })
	})
})

describe('isOwnRedirect', () => {
	test('the mod submitting the bare redirect', () => {
		expect(isOwnRedirect({ kind: 'plugin', name: 'switchboard' }, R)).toBe(true)
	})

	test('anything else is new input', () => {
		expect(isOwnRedirect({ kind: 'composer' }, R)).toBe(false)
		expect(isOwnRedirect({ kind: 'plugin', name: 'cerebro' }, R)).toBe(false)
		expect(isOwnRedirect({ kind: 'plugin', name: 'switchboard' }, `John (from phone): go\n\n${R}`)).toBe(false)
		expect(isOwnRedirect(undefined, R)).toBe(false)
	})
})

describe('askedJohn', () => {
	test('a blocking ask_human or an AskUserQuestion that ran', () => {
		expect(askedJohn('mcp__switchboard__ask_human', { question: 'ok?' }, { isError: undefined })).toBe(true)
		expect(askedJohn('AskUserQuestion', { questions: [] }, {})).toBe(true)
	})

	test('a rejected, refused or background ask does not count', () => {
		expect(askedJohn('mcp__switchboard__ask_human', { question: 'ok?' }, { isError: true })).toBe(false)
		expect(askedJohn('mcp__switchboard__ask_human', { question: 'ok?' }, { deny: 'no' })).toBe(false)
		expect(askedJohn('mcp__switchboard__ask_human', { question: 'ok?', background: true }, {})).toBe(false)
		expect(askedJohn('AskUserQuestion', { questions: [] }, { deny: "John's phone did not answer" })).toBe(false)
		expect(askedJohn('mcp__switchboard__notify_human', { message: 'hi' }, {})).toBe(false)
	})
})

test('the redirect text keeps its first and last lines and says a failed ask is not a live question', () => {
	expect(R.startsWith('You are in away mode. John is on his phone, not watching the terminal.\n')).toBe(true)
	expect(R.endsWith('Only call set_away_mode(False) if John has explicitly told you he is back.')).toBe(true)
	expect(R).toContain('Only if your ask_human call came back saying it was moved to a background task (~120s) is a question still live')
	expect(R).toContain('An ask_human that was denied, rejected or returned an error is NOT a live question: call ask_human again.')
	expect(REDIRECT_CAP_NOTICE).toBe('This session ended 3 turns in a row without asking you anything, so the away-mode reminder has stopped. It is idle now; reply here to give it its next instruction.')
})

const DONE = { answer: '', durationMs: 1, isAborted: false, reason: 'answer' as const }

async function runTurn($: Engine, id: string, done: Record<string, unknown> = {}): Promise<void> {
	await $.turn.start({ text: 'go', turnId: id })
	await $.turn.complete({ ...DONE, turnId: id, ...done })
}

test('at the desk the turn ends quietly', async ($, on) => {
	const w = world(on)
	await runTurn($, 'T1')
	await w.clock.settle()
	expect(w.prompts).toEqual([])
})

test('away with no live ask, the turn end hands the agent the redirect', async ($, on) => {
	const w = world(on)
	w.inbox.push({ away: true })
	await runTurn($, 'T1')
	await w.clock.settle()
	expect(w.prompts).toEqual([{ text: REDIRECT_REASON_AWAY_MODE, context: [] }])
})

test('away with a live ask, the turn ends quietly', async ($, on) => {
	const w = world(on)
	w.inbox.push({ away: true, pending_ask: true })
	await runTurn($, 'T1')
	await w.clock.settle()
	expect(w.prompts).toEqual([])
})

test('messages waiting at turn end ride ahead of the redirect', async ($, on) => {
	const w = world(on)
	w.inbox.push({ away: true, notices: ['John (from phone): wait'] })
	await runTurn($, 'T1')
	await w.clock.settle()
	expect(w.prompts.map(p => p.text)).toEqual([`John (from phone): wait\n\n${REDIRECT_REASON_AWAY_MODE}`])
})

// One away-mode turn end with no live ask.
async function awayTurn($: Engine, w: ReturnType<typeof world>, id: string, inbox: Record<string, unknown> = {}): Promise<void> {
	w.inbox.push({ away: true, ...inbox })
	await runTurn($, id)
	await w.clock.settle()
}

const CAP_CALL = { server: 'switchboard', tool: 'notify_human', args: { message: REDIRECT_CAP_NOTICE, sender: 'Claude', cli_session_id: 'S1', cwd: 'C:/Work/X' } }

test('an agent that keeps answering in text gets two redirects, then John is told once', async ($, on) => {
	const w = world(on)
	for (const id of ['T1', 'T2', 'T3', 'T4']) await awayTurn($, w, id)
	expect(w.prompts.map(p => p.text)).toEqual([R, R])
	expect(w.mcpCalls).toEqual([CAP_CALL])
})

test('a typed prompt starts a new chain', async ($, on) => {
	const w = world(on)
	for (const id of ['T1', 'T2', 'T3']) await awayTurn($, w, id)
	await $.prompt.submit({ text: 'carry on', wait: false, origin: { kind: 'composer' } })
	await awayTurn($, w, 'T4')
	expect(w.prompts.map(p => p.text)).toEqual([R, R, 'carry on', R])
	expect(w.mcpCalls).toEqual([CAP_CALL])
})

test('messages riding the redirect start a new chain', async ($, on) => {
	const w = world(on)
	await awayTurn($, w, 'T1')
	await awayTurn($, w, 'T2')
	await awayTurn($, w, 'T3', { notices: ['John (from phone): wait'] })
	for (const id of ['T4', 'T5', 'T6']) await awayTurn($, w, id)
	expect(w.prompts.map(p => p.text)).toEqual([R, R, `John (from phone): wait\n\n${R}`, R, R])
	expect(w.mcpCalls).toEqual([CAP_CALL])
})

test('a phone message after the cap starts a new chain', async ($, on) => {
	const w = world(on)
	await startSession($, w)
	for (const id of ['T1', 'T2', 'T3']) await awayTurn($, w, id)
	expect(w.mcpCalls).toEqual([CAP_CALL])
	w.inbox.push({ away: true, notices: ['John (from phone): carry on'] })
	await w.clock.advance(2000)
	await awayTurn($, w, 'T4')
	expect(w.prompts.map(p => p.text)).toEqual([R, R, 'John (from phone): carry on', R])
})

test('a blocking ask_human that ran starts a new chain', async ($, on) => {
	const w = world(on)
	await awayTurn($, w, 'T1')
	await awayTurn($, w, 'T2')
	await $.tool.call({ tool: 'mcp__switchboard__ask_human', sender: 'Claude', question: 'ok?' })
	await awayTurn($, w, 'T3')
	expect(w.prompts.map(p => p.text)).toEqual([R, R, R])
	expect(w.mcpCalls).toEqual([])
})

test('an answered AskUserQuestion starts a new chain', async ($, on) => {
	const w = world(on)
	await awayTurn($, w, 'T1')
	await awayTurn($, w, 'T2')
	w.inbox.push({ away: true })
	w.mcpReplies.push('yes')
	const question = { question: 'Push now?', header: 'Push', options: [{ label: 'yes', description: '' }], multiSelect: false }
	await $.tool.call({ tool: 'AskUserQuestion', questions: [question] })
	await awayTurn($, w, 'T3')
	expect(w.prompts.map(p => p.text)).toEqual([R, R, R])
	expect(w.mcpCalls.map(c => c.tool)).toEqual(['ask_human'])
})

test('a rejected ask_human does not start a new chain', async ($, on) => {
	const w = world(on)
	await awayTurn($, w, 'T1')
	w.toolError = "The user doesn't want to proceed with this tool use."
	await $.tool.call({ tool: 'mcp__switchboard__ask_human', sender: 'Claude', question: 'ok?' })
	w.toolError = null
	await awayTurn($, w, 'T2')
	await awayTurn($, w, 'T3')
	expect(w.prompts.map(p => p.text)).toEqual([R, R])
	expect(w.mcpCalls).toEqual([CAP_CALL])
})

test('a background ask_human does not start a new chain', async ($, on) => {
	const w = world(on)
	await awayTurn($, w, 'T1')
	await $.tool.call({ tool: 'mcp__switchboard__ask_human', sender: 'Claude', question: 'ok?', background: true })
	await awayTurn($, w, 'T2')
	await awayTurn($, w, 'T3')
	expect(w.prompts.map(p => p.text)).toEqual([R, R])
	expect(w.mcpCalls).toEqual([CAP_CALL])
})

test('a live ask, or away mode off, at a turn end starts a new chain', async ($, on) => {
	const w = world(on)
	await awayTurn($, w, 'T1')
	await awayTurn($, w, 'T2')
	await awayTurn($, w, 'T3', { pending_ask: true })
	await awayTurn($, w, 'T4')
	await awayTurn($, w, 'T5')
	w.inbox.push({ away: false })
	await runTurn($, 'T6')
	await w.clock.settle()
	await awayTurn($, w, 'T7')
	expect(w.prompts.map(p => p.text)).toEqual([R, R, R, R, R])
	expect(w.mcpCalls).toEqual([])
})

test('a failed cap notice is only logged, and the session still goes idle', async ($, on) => {
	const w = world(on)
	w.mcpError = 'switchboard is not connected'
	for (const id of ['T1', 'T2', 'T3']) await awayTurn($, w, id)
	expect(w.prompts.map(p => p.text)).toEqual([R, R])
	expect(w.logs.some(l => l.to === 'debug' && l.text.includes('could not tell John the away-mode reminder stopped'))).toBe(true)
	await $.prompt.submit({ text: 'next', wait: false, origin: { kind: 'composer' } })
	expect(w.prompts.map(p => p.text)).toEqual([R, R, 'next'])
})

test('at the desk, messages waiting at turn end are delivered on their own', async ($, on) => {
	const w = world(on)
	w.inbox.push({ notices: ['John (from phone): wait'] })
	await runTurn($, 'T1')
	await w.clock.settle()
	expect(w.prompts.map(p => p.text)).toEqual(['John (from phone): wait'])
})

test('held messages are delivered at turn end even with the server down', async ($, on) => {
	const w = world(on)
	await startSession($, w)
	await $.turn.start({ text: '', turnId: 'T1' })
	w.inbox.push({ notices: ['John (from phone): held'] })
	await w.clock.advance(2000)
	w.fetchError = 'connect ECONNREFUSED 127.0.0.1:9876'
	await $.turn.complete({ ...DONE, turnId: 'T1' })
	await w.clock.settle()
	expect(w.prompts.map(p => p.text)).toEqual(['John (from phone): held'])
})

test('an interrupted turn gets no redirect and no turn-end check', async ($, on) => {
	const w = world(on)
	w.inbox.push({ away: true })
	await runTurn($, 'T1', { isAborted: true, reason: 'aborted' })
	await w.clock.settle()
	expect(w.prompts).toEqual([])
	expect(inboxPolls(w)).toEqual([])
})

test('a refused redirect puts the messages back for the next poll', async ($, on) => {
	const w = world(on)
	await startSession($, w)
	w.rejectSubmit = true
	w.inbox.push({ away: true, notices: ['John (from phone): wait'] })
	await runTurn($, 'T1')
	await w.clock.settle()
	expect(w.prompts).toEqual([])
	expect(w.logs.some(l => l.to === 'debug' && l.text.includes('could not submit'))).toBe(true)
	w.rejectSubmit = false
	await w.clock.advance(2000)
	expect(w.prompts.map(p => p.text)).toEqual(['John (from phone): wait'])
})

test('a server that never answers does not hold up the turn end', async ($, on) => {
	const w = world(on)
	w.hangFetch = true
	await $.turn.start({ text: 'go', turnId: 'T1' })
	const ended = $.turn.complete({ ...DONE, turnId: 'T1' })
	await w.clock.advance(1500)
	await ended
	expect(w.prompts).toEqual([])
	expect(w.logs.some(l => l.to === 'debug' && l.text.includes('no answer within 1500 ms'))).toBe(true)
})

test('the turn end posts clear', async ($, on) => {
	const w = world(on)
	await runTurn($, 'T1')
	await w.clock.settle()
	const bodies = postsTo(w, '/agent_status').map(s => s.body)
	expect(bodies[bodies.length - 1]).toEqual({ session_id: 'S1', cwd: 'C:/Work/X', event: 'Stop', state: 'clear' })
})

test('session end posts the session and the reason', async ($, on) => {
	const w = world(on)
	await $.session.end({ reason: 'prompt_input_exit', sessionId: 'S1', resume: { id: 'S1' } })
	expect(postsTo(w, '/session_end').map(s => s.body)).toEqual([{ session_id: 'S1', reason: 'prompt_input_exit' }])
})

test('session end with the server down still ends', async ($, on) => {
	const w = world(on)
	w.fetchError = 'connect ECONNREFUSED 127.0.0.1:9876'
	expect(await $.session.end({ reason: 'other', sessionId: 'S1', resume: { id: 'S1' } })).toEqual({ sessionId: 'S1' })
})
