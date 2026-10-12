import { expect, test } from 'claude-code/testing'

import { postsTo, world } from './harness'

test('session start posts the session to the server', async ($, on) => {
	const w = world(on)
	await $.session.start({ cwd: 'C:/Work/X', surface: 'terminal', isInteractive: true })
	const sent = postsTo(w, '/session_start')
	expect(sent).toHaveLength(1)
	expect(sent[0]?.url).toBe('http://127.0.0.1:9876/session_start')
	expect(sent[0]?.body).toEqual({ session_id: 'S1', cwd: 'C:/Work/X' })
	expect(sent[0]?.headers).toEqual({ 'Content-Type': 'application/json' })
})

test('a headless run posts its start too', async ($, on) => {
	const w = world(on)
	await $.session.start({ cwd: 'C:/Work/X', surface: null, isInteractive: false })
	expect(postsTo(w, '/session_start')).toHaveLength(1)
})

test('WSL settings: the base URL and Bearer token come from the environment', async ($, on) => {
	const w = world(on, { SWITCHBOARD_BASE_URL: 'http://172.20.0.1:9876', SWITCHBOARD_TOKEN: 'secret' })
	await $.session.start({ cwd: '/home/j/work', surface: 'terminal', isInteractive: true })
	const sent = postsTo(w, '/session_start')
	expect(sent[0]?.url).toBe('http://172.20.0.1:9876/session_start')
	expect(sent[0]?.headers).toEqual({ 'Content-Type': 'application/json', Authorization: 'Bearer secret' })
})

test('a server that is down does not break session start', async ($, on) => {
	const w = world(on)
	w.fetchError = 'connect ECONNREFUSED 127.0.0.1:9876'
	const result = await $.session.start({ cwd: 'C:/Work/X', surface: 'terminal', isInteractive: true })
	expect(result).toEqual({ cwd: 'C:/Work/X' })
	expect(w.logs.some(l => l.to === 'debug' && l.text.includes('session start post failed'))).toBe(true)
})

test('a server that never answers does not hold up session start', async ($, on) => {
	const w = world(on)
	w.hangFetch = true
	const started = $.session.start({ cwd: 'C:/Work/X', surface: 'terminal', isInteractive: true })
	await w.clock.advance(1500)
	expect(await started).toEqual({ cwd: 'C:/Work/X' })
	expect(w.logs.some(l => l.to === 'debug' && l.text.includes('no answer within 1500 ms'))).toBe(true)
})
