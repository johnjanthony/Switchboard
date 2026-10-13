import { expect, test } from 'claude-code/testing'

import { inFlightCall, settledReport } from '../approval'
import { postsTo, world } from './harness'

// The mod runtime provides timers; the typecheck's lib does not declare them.
declare function setTimeout(callback: (value: unknown) => void, ms: number): unknown

async function until(cond: () => boolean): Promise<void> {
	for (let i = 0; i < 200 && !cond(); i++) await new Promise(resolve => setTimeout(resolve, 1))
}

function decision(toolUseId: string, source: string) {
	return {
		to: 'collector' as const,
		event: 'tool_decision',
		attributes: { tool_use_id: toolUseId, tool_name: 'Bash', decision: source === 'user_reject' ? 'reject' : 'accept', source },
		loggedAt: '2026-10-12T00:00:00Z',
	}
}

test('inFlightCall keeps the tool arguments and drops the keys tool.call adds', async () => {
	expect(inFlightCall({ tool: 'Bash', tool_use_id: 'toolu_1', agentId: 'a1', consent: 'x', requestMeta: { k: 'v' }, command: 'ls', description: 'd' })).toEqual({
		tool: 'Bash', agentId: 'a1', input: { command: 'ls', description: 'd' },
	})
	expect(inFlightCall({ tool: 'Read', file_path: 'a.md' }).agentId).toBe(null)
})

test('settledReport answers only a person\'s decision on a call in flight', async () => {
	const inflight = new Map([['toolu_1', { tool: 'Bash', agentId: null, input: { command: 'ls' } }]])
	expect(settledReport({ tool_use_id: 'toolu_1', source: 'user_temporary', decision: 'accept' }, inflight)).toEqual({
		tool_use_id: 'toolu_1', tool: 'Bash', agent_id: null, input: { command: 'ls' }, decision: 'accept', source: 'user_temporary',
	})
	expect(settledReport({ tool_use_id: 'toolu_1', source: 'user_reject', decision: 'reject' }, inflight)?.decision).toBe('reject')
	expect(settledReport({ tool_use_id: 'toolu_1', source: 'hook', decision: 'accept' }, inflight)).toBe(undefined)
	expect(settledReport({ tool_use_id: 'toolu_1', source: 'config', decision: 'accept' }, inflight)).toBe(undefined)
	expect(settledReport({ tool_use_id: 'toolu_9', source: 'user_temporary', decision: 'accept' }, inflight)).toBe(undefined)
	expect(settledReport({ source: 'user_temporary' }, inflight)).toBe(undefined)
})

test('a terminal answer to a call in flight is reported to the server', async ($, on) => {
	const w = world(on)
	let release: () => void = () => {}
	w.toolGate = new Promise<void>(resolve => { release = resolve })
	const call = $.tool.call({ tool: 'Bash', command: 'echo hi > x.txt', description: 'marker' })
	await until(() => w.toolInputs.length === 1)
	const toolUseId = String(w.toolInputs[0]?.tool_use_id)
	await $.telemetry.log(decision(toolUseId, 'user_temporary'))
	release()
	await call
	await w.clock.settle()
	expect(postsTo(w, '/sessions/S1/approvals/settled').map(s => s.body)).toEqual([{
		tool_use_id: toolUseId, tool: 'Bash', agent_id: null,
		input: { command: 'echo hi > x.txt', description: 'marker' }, decision: 'accept', source: 'user_temporary',
	}])
})

test('a hook or rule decision, or one after the call ended, reports nothing', async ($, on) => {
	const w = world(on)
	let release: () => void = () => {}
	w.toolGate = new Promise<void>(resolve => { release = resolve })
	const call = $.tool.call({ tool: 'Bash', command: 'ls' })
	await until(() => w.toolInputs.length === 1)
	const toolUseId = String(w.toolInputs[0]?.tool_use_id)
	await $.telemetry.log(decision(toolUseId, 'hook'))
	await $.telemetry.log(decision(toolUseId, 'config'))
	release()
	await call
	await $.telemetry.log(decision(toolUseId, 'user_temporary'))
	await w.clock.settle()
	expect(postsTo(w, '/sessions/S1/approvals/settled')).toEqual([])
})

test('every tool_decision record passes through to the collector, reported or not', async ($, on) => {
	const w = world(on)
	let release: () => void = () => {}
	w.toolGate = new Promise<void>(resolve => { release = resolve })
	const call = $.tool.call({ tool: 'Bash', command: 'ls' })
	await until(() => w.toolInputs.length === 1)
	const toolUseId = String(w.toolInputs[0]?.tool_use_id)
	await $.telemetry.log(decision(toolUseId, 'user_temporary'))
	await $.telemetry.log(decision(toolUseId, 'hook'))
	await $.telemetry.log(decision(toolUseId, 'config'))
	release()
	await call
	await w.clock.settle()
	expect(w.telemetry.map(t => (t.attributes as Record<string, unknown>).source)).toEqual(['user_temporary', 'hook', 'config'])
	expect(postsTo(w, '/sessions/S1/approvals/settled')).toHaveLength(1)
})

test('a switchboard tool is reported with the ids the injector added', async ($, on) => {
	const w = world(on)
	let release: () => void = () => {}
	w.toolGate = new Promise<void>(resolve => { release = resolve })
	const call = $.tool.call({ tool: 'mcp__switchboard__notify_human', message: 'hi', sender: 'Claude' })
	await until(() => w.toolInputs.length === 1)
	await $.telemetry.log(decision(String(w.toolInputs[0]?.tool_use_id), 'user_temporary'))
	release()
	await call
	await w.clock.settle()
	const [report] = postsTo(w, '/sessions/S1/approvals/settled')
	expect(report?.body?.input).toEqual({ message: 'hi', sender: 'Claude', cli_session_id: 'S1', cwd: 'C:/Work/X' })
})
