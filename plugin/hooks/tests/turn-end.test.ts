import type { Engine } from 'claude-code/testing'
import { describe, expect, test } from 'claude-code/testing'

import { REDIRECT_REASON_AWAY_MODE, turnEndBlock } from '../turn-end'
import { inboxPolls, postsTo, startSession, world } from './harness'

const ANSWER = { notices: [], stop: false, away: false, pending_ask: false }

describe('turnEndBlock (the Python hook decision table)', () => {
	test('at the desk with nothing held: no block', () => {
		expect(turnEndBlock([], ANSWER)).toBeUndefined()
	})

	test('away with no live ask: the redirect', () => {
		expect(turnEndBlock([], { ...ANSWER, away: true })).toBe(REDIRECT_REASON_AWAY_MODE)
	})

	test('away with a live ask: no block', () => {
		expect(turnEndBlock([], { ...ANSWER, away: true, pending_ask: true })).toBeUndefined()
	})

	test('held items block, ahead of the redirect when away', () => {
		expect(turnEndBlock(['a', 'b'], { ...ANSWER, away: true })).toBe(`a\n\nb\n\n${REDIRECT_REASON_AWAY_MODE}`)
	})

	test('held items still block when away mode could not be read', () => {
		expect(turnEndBlock(['a'], null)).toBe('a')
	})
})

test('the redirect text is the Python hook text, word for word', () => {
	expect(REDIRECT_REASON_AWAY_MODE.startsWith('You are in away mode. John is on his phone, not watching the terminal.\n')).toBe(true)
	expect(REDIRECT_REASON_AWAY_MODE.endsWith('Only call set_away_mode(False) if John has explicitly told you he is back.')).toBe(true)
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
