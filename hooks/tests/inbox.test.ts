import { expect, test } from 'claude-code/testing'

import { backoffMs } from '../inbox'
import { inboxPolls, startSession, world } from './harness'

const HI = 'John (from phone): hi'

test('backoff doubles from 2 s and stops at 30 s', () => {
	expect([0, 1, 2, 3, 4, 9].map(backoffMs)).toEqual([2000, 4000, 8000, 16000, 30000, 30000])
})

test('an idle session submits held phone messages as one prompt', async ($, on) => {
	const w = world(on)
	await startSession($, w)
	w.inbox.push({ notices: ['John (from phone): run the tests', 'John (from phone): then push'] })
	await w.clock.advance(2000)
	expect(w.prompts.map(p => p.text)).toEqual(['John (from phone): run the tests\n\nJohn (from phone): then push'])
})

test('it polls the inbox of the current session id, so /clear carries over', async ($, on) => {
	const w = world(on)
	await startSession($, w)
	await w.clock.advance(2000)
	w.sessionId = 'S2'
	await w.clock.advance(2000)
	expect(inboxPolls(w).map(s => s.url)).toEqual([
		'http://127.0.0.1:9876/sessions/S1/inbox',
		'http://127.0.0.1:9876/sessions/S2/inbox',
	])
})

test('a headless run never polls', async ($, on) => {
	const w = world(on)
	await $.session.start({ cwd: 'C:/Work/X', surface: null, isInteractive: false })
	await w.clock.advance(10_000)
	expect(inboxPolls(w)).toEqual([])
})

test('a busy session holds messages and hands them to the next tool result', async ($, on) => {
	const w = world(on)
	await startSession($, w)
	await $.turn.start({ text: '', turnId: 'T1' })
	w.inbox.push({ notices: ['John (from phone): stop after this file'] })
	await w.clock.advance(2000)
	expect(w.prompts).toEqual([])
	const ran = await $.tool.call({ tool: 'Bash', command: 'ls' })
	expect(ran.context).toEqual(['John (from phone): stop after this file'])
	const again = await $.tool.call({ tool: 'Bash', command: 'ls' })
	expect(again.context).toBeUndefined()
})

test('the session stays busy after its own submit, so a second message waits for the turn', async ($, on) => {
	const w = world(on)
	await startSession($, w)
	w.inbox.push({ notices: ['John (from phone): one'] }, { notices: ['John (from phone): two'] })
	await w.clock.advance(4000)
	expect(w.prompts.map(p => p.text)).toEqual(['John (from phone): one'])
	await $.turn.complete({ answer: '', durationMs: 1, isAborted: false, turnId: 'T1', reason: 'answer' })
	await w.clock.advance(2000)
	expect(w.prompts.map(p => p.text)).toEqual(['John (from phone): one', 'John (from phone): two'])
})

test('at the desk a draft defers delivery, and the typed prompt carries the messages', async ($, on) => {
	const w = world(on)
	await startSession($, w)
	w.draft = 'half-typed'
	w.inbox.push({ notices: [HI], away: false })
	await w.clock.advance(2000)
	expect(w.prompts).toEqual([])
	await $.prompt.submit({ text: 'my own prompt', wait: false, origin: { kind: 'composer' } })
	expect(w.prompts).toEqual([{ text: 'my own prompt', context: [HI] }])
})

test('in away mode a leftover draft does not hold delivery back', async ($, on) => {
	const w = world(on)
	await startSession($, w)
	w.draft = 'left behind'
	w.inbox.push({ notices: [HI], away: true })
	await w.clock.advance(2000)
	expect(w.prompts.map(p => p.text)).toEqual([HI])
})

test('stop cancels the running turn', async ($, on) => {
	const w = world(on)
	await startSession($, w)
	await $.turn.start({ text: '', turnId: 'T7' })
	w.inbox.push({ stop: true })
	await w.clock.advance(2000)
	expect(w.aborted).toEqual(['T7'])
	expect(w.logs.map(l => l.text)).toContain('Stopped from phone')
})

test('a stop that arrives while idle is dropped, not applied to a later turn', async ($, on) => {
	const w = world(on)
	await startSession($, w)
	w.inbox.push({ stop: true })
	await w.clock.advance(2000)
	await $.turn.start({ text: '', turnId: 'T8' })
	await w.clock.advance(2000)
	expect(w.aborted).toEqual([])
})

test('a server outage backs off, and the first good poll delivers what was queued', async ($, on) => {
	const w = world(on)
	await startSession($, w)
	w.fetchError = 'connect ECONNREFUSED 127.0.0.1:9876'
	await w.clock.advance(2000)
	await w.clock.advance(2000)
	expect(inboxPolls(w)).toHaveLength(1)
	w.fetchError = null
	w.inbox.push({ notices: ['John (from phone): back?'] })
	await w.clock.advance(2000)
	expect(w.prompts.map(p => p.text)).toEqual(['John (from phone): back?'])
})

test('an inbox answer that is not JSON is a failed poll and submits nothing', async ($, on) => {
	const w = world(on)
	await startSession($, w)
	w.inboxRaw = '<html>502 Bad Gateway</html>'
	await w.clock.advance(2000)
	expect(w.prompts).toEqual([])
	expect(w.logs.some(l => l.to === 'debug' && l.text.includes('inbox poll failed'))).toBe(true)
})

test('a 401 is written to the transcript once, not on every poll', async ($, on) => {
	const w = world(on)
	await startSession($, w)
	w.inboxStatus = 401
	await w.clock.advance(2000)
	await w.clock.advance(4000)
	expect(inboxPolls(w)).toHaveLength(2)
	expect(w.logs.filter(l => l.to === 'transcript' && l.text.includes('401'))).toHaveLength(1)
})

test('a refused submit puts the messages back and the next poll retries', async ($, on) => {
	const w = world(on)
	await startSession($, w)
	w.rejectSubmit = true
	w.inbox.push({ notices: [HI] })
	await w.clock.advance(2000)
	expect(w.prompts).toEqual([])
	w.rejectSubmit = false
	await w.clock.advance(2000)
	expect(w.prompts.map(p => p.text)).toEqual([HI])
})
