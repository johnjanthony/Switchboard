import { expect, test } from 'claude-code/testing'

import { postsTo, world } from './harness'

test('a switchboard tool call carries the session id and cwd', async ($, on) => {
	const w = world(on)
	await $.tool.call({ tool: 'mcp__switchboard__notify_human', sender: 'Claude', message: 'hi' })
	expect(w.toolInputs[0]).toMatchObject({ tool: 'mcp__switchboard__notify_human', message: 'hi', cli_session_id: 'S1', cwd: 'C:/Work/X' })
})

test('other tools reach the engine unchanged', async ($, on) => {
	const w = world(on)
	await $.tool.call({ tool: 'Bash', command: 'git status' })
	expect(w.toolInputs[0]).not.toHaveProperty('cli_session_id')
})

test('a tool call posts its tool status before it runs and thinking after', async ($, on) => {
	const w = world(on)
	await $.tool.call({ tool: 'Bash', command: 'git status' })
	await w.clock.settle()
	expect(postsTo(w, '/agent_status').map(s => s.body)).toEqual([
		{ session_id: 'S1', cwd: 'C:/Work/X', event: 'PreToolUse', state: 'tool:Bash', detail: 'git status' },
		{ session_id: 'S1', cwd: 'C:/Work/X', event: 'PostToolUse', state: 'thinking' },
	])
})

test('ask_human posts clear and message_and_await_agent posts waiting', async ($, on) => {
	const w = world(on)
	await $.tool.call({ tool: 'mcp__switchboard__ask_human', sender: 'Claude', question: 'ok?' })
	await $.tool.call({ tool: 'mcp__switchboard__message_and_await_agent', sender: 'Claude', message: 'hi' })
	await w.clock.settle()
	const pre = postsTo(w, '/agent_status').map(s => s.body).filter(b => b?.event === 'PreToolUse')
	expect(pre.map(b => b?.state)).toEqual(['clear', 'waiting'])
})

test('a turn start posts thinking', async ($, on) => {
	const w = world(on)
	await $.turn.start({ text: 'hello', turnId: 'T1' })
	await w.clock.settle()
	expect(postsTo(w, '/agent_status').map(s => s.body)).toEqual([
		{ session_id: 'S1', cwd: 'C:/Work/X', event: 'UserPromptSubmit', state: 'thinking' },
	])
})

test('a failed status post never fails the tool call', async ($, on) => {
	const w = world(on)
	w.fetchError = 'connect ECONNREFUSED 127.0.0.1:9876'
	const ran = await $.tool.call({ tool: 'Bash', command: 'ls' })
	await w.clock.settle()
	expect(ran.result).toBe('ran')
	expect(w.logs.filter(l => l.to === 'debug' && l.text.includes('status post failed'))).toHaveLength(2)
})
