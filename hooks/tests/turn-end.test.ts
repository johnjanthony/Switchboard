import { describe, expect, test } from 'claude-code/testing'

import { REDIRECT_REASON_AWAY_MODE, turnEndBlock } from '../turn-end'
import { postsTo, startSession, world } from './harness'

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

test('at the desk the turn ends', async ($, on) => {
	world(on)
	expect((await $.classic.Stop({ session_id: 'S1', stop_hook_active: false })).block).toBeUndefined()
})

test('away with no live ask the turn end is blocked with the redirect', async ($, on) => {
	const w = world(on)
	w.inbox.push({ away: true })
	expect((await $.classic.Stop({ session_id: 'S1', stop_hook_active: false })).block).toBe(REDIRECT_REASON_AWAY_MODE)
})

test('messages waiting at turn end block it, ahead of the redirect', async ($, on) => {
	const w = world(on)
	w.inbox.push({ away: true, notices: ['John (from phone): wait'] })
	expect((await $.classic.Stop({ session_id: 'S1', stop_hook_active: false })).block).toBe(
		`John (from phone): wait\n\n${REDIRECT_REASON_AWAY_MODE}`,
	)
})

test('held messages block the turn end even with the server down', async ($, on) => {
	const w = world(on)
	await startSession($, w)
	await $.turn.start({ text: '', turnId: 'T1' })
	w.inbox.push({ notices: ['John (from phone): held'] })
	await w.clock.advance(2000)
	w.fetchError = 'connect ECONNREFUSED 127.0.0.1:9876'
	expect((await $.classic.Stop({ session_id: 'S1', stop_hook_active: false })).block).toBe('John (from phone): held')
})

test('the turn end posts clear', async ($, on) => {
	const w = world(on)
	await $.classic.Stop({ session_id: 'S1', cwd: 'C:/Work/X', stop_hook_active: false })
	await w.clock.settle()
	expect(postsTo(w, '/agent_status').map(s => s.body)).toEqual([{ session_id: 'S1', cwd: 'C:/Work/X', event: 'Stop', state: 'clear' }])
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
