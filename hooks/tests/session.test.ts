import { expect, test } from 'claude-code/testing'

import { postsTo, world } from './harness'

test('session start posts the session to the server', async ($, on) => {
	const w = world(on)
	await $.classic.SessionStart({ session_id: 'S1', cwd: 'C:/Work/X', source: 'resume' })
	const sent = postsTo(w, '/session_start')
	expect(sent).toHaveLength(1)
	expect(sent[0]?.url).toBe('http://127.0.0.1:9876/session_start')
	expect(sent[0]?.body).toEqual({ session_id: 'S1', cwd: 'C:/Work/X', source: 'resume' })
	expect(sent[0]?.headers).toEqual({ 'Content-Type': 'application/json' })
})

test('WSL settings: the base URL and Bearer token come from the environment', async ($, on) => {
	const w = world(on, { SWITCHBOARD_BASE_URL: 'http://172.20.0.1:9876', SWITCHBOARD_TOKEN: 'secret' })
	await $.classic.SessionStart({ session_id: 'S1', cwd: '/home/j/work', source: 'startup' })
	const sent = postsTo(w, '/session_start')
	expect(sent[0]?.url).toBe('http://172.20.0.1:9876/session_start')
	expect(sent[0]?.headers).toEqual({ 'Content-Type': 'application/json', Authorization: 'Bearer secret' })
})

test('a server that is down does not break session start', async ($, on) => {
	const w = world(on)
	w.fetchError = 'connect ECONNREFUSED 127.0.0.1:9876'
	const result = await $.classic.SessionStart({ session_id: 'S1', cwd: 'C:/Work/X', source: 'startup' })
	expect(result.block).toBeUndefined()
	expect(w.logs.some(l => l.to === 'debug' && l.text.includes('session start post failed'))).toBe(true)
})
