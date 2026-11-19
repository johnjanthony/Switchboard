import { expect, test } from 'claude-code/testing'

import { inFlightCall, predictsDialog, REPORTED_TOOL, settledBody } from '../approval'
import { postsTo, world } from './harness'

// The mod runtime provides timers; the typecheck's lib does not declare them.
declare function setTimeout(callback: (value: unknown) => void, ms: number): unknown

async function until(cond: () => boolean): Promise<void> {
	for (let i = 0; i < 200 && !cond(); i++) await new Promise(resolve => setTimeout(resolve, 1))
}

const SETTLED = '/sessions/S1/approvals/settled'

test('inFlightCall keeps the tool arguments and drops the keys tool.call adds', async () => {
	expect(inFlightCall({ tool: 'Bash', tool_use_id: 'toolu_1', agentId: 'a1', consent: 'x', requestMeta: { k: 'v' }, command: 'ls', description: 'd' })).toEqual({
		tool: 'Bash', agentId: 'a1', input: { command: 'ls', description: 'd' },
	})
	expect(inFlightCall({ tool: 'Read', file_path: 'a.md' }).agentId).toBe(null)
})

test('only an ask verdict predicts a dialog', async () => {
	expect(predictsDialog({ decision: 'ask', reason: 'r' })).toBe(true)
	expect(predictsDialog({ decision: 'allow' })).toBe(false)
	expect(predictsDialog({ decision: 'deny' })).toBe(false)
	expect(predictsDialog(undefined)).toBe(false)
})

test('the reported tools leave out the switchboard tools and the dialogs left to the terminal', async () => {
	expect(['Bash', 'Write', 'mcp__slowprobe__slow', 'AskUserQuestionX'].every(t => REPORTED_TOOL.test(t))).toBe(true)
	expect(['mcp__switchboard__ask_human', 'AskUserQuestion', 'ExitPlanMode'].some(t => REPORTED_TOOL.test(t))).toBe(false)
})

test('settledBody reports an allowed call as accepted and a refused one as rejected', async () => {
	const call = { tool: 'Bash', agentId: null, input: { command: 'ls' } }
	expect(settledBody('toolu_1', { ...call, ran: true })).toEqual({
		tool_use_id: 'toolu_1', tool: 'Bash', agent_id: null, input: { command: 'ls' }, decision: 'accept', source: 'tool_run',
	})
	expect(settledBody('toolu_1', { ...call, ran: false })).toMatchObject({ decision: 'reject', source: 'tool_refused' })
})

test('an allowed call whose dialog was predicted is reported once, as it reaches tool.run', async ($, on) => {
	const w = world(on)
	w.checkDecision = 'ask'
	// With a tool.run hook in the test, the kit runs a call as the engine does:
	// its check, then tool.run (the world's tool.call fake is not reached).
	let release: () => void = () => {}
	const gate = new Promise<void>(resolve => { release = resolve })
	const runs: string[] = []
	on('tool.run', async (_$, e) => {
		runs.push(String(e.tool_use_id))
		await gate
		return { result: 'ran' }
	})
	const call = $.tool.call({ tool: 'Bash', command: 'echo hi > x.txt', description: 'marker' })
	await until(() => runs.length === 1)
	await w.clock.settle()
	// Reported while the tool is still running, not when it ends.
	const bodies = postsTo(w, SETTLED).map(s => s.body)
	expect(bodies).toEqual([{
		tool_use_id: runs[0], tool: 'Bash', agent_id: null,
		input: { command: 'echo hi > x.txt', description: 'marker' }, decision: 'accept', source: 'tool_run',
	}])
	expect(String(runs[0])).toMatch(/^toolu_/)
	release()
	await call
	await w.clock.settle()
	expect(postsTo(w, SETTLED)).toHaveLength(1)
	expect(w.checks).toEqual([{ tool: 'Bash', input: { command: 'echo hi > x.txt', description: 'marker' } }])
})

test('a call with a predicted dialog that never reaches tool.run is reported refused', async ($, on) => {
	const w = world(on)
	w.checkDecision = 'ask'
	w.callDecision = 'deny'
	const runs: string[] = []
	on('tool.run', (_$, e) => {
		runs.push(String(e.tool_use_id))
		return { result: 'ran' }
	})
	await $.tool.call({ tool: 'Bash', command: 'rm x.txt' }).catch(() => undefined)
	await w.clock.settle()
	expect(runs).toEqual([])
	const bodies = postsTo(w, SETTLED).map(s => s.body)
	expect(bodies).toHaveLength(1)
	expect(bodies[0]).toMatchObject({ tool: 'Bash', agent_id: null, input: { command: 'rm x.txt' }, decision: 'reject', source: 'tool_refused' })
	expect(String(bodies[0]?.tool_use_id)).toMatch(/^toolu_/)
})

test('a call with no predicted dialog reports nothing, allowed or not', async ($, on) => {
	const w = world(on)
	const runs: string[] = []
	on('tool.run', (_$, e) => {
		runs.push(String(e.tool_use_id))
		return { result: 'ran' }
	})
	await $.tool.call({ tool: 'Bash', command: 'ls' })
	w.callDecision = 'deny'
	await $.tool.call({ tool: 'Read', file_path: 'a.md' }).catch(() => undefined)
	await w.clock.settle()
	expect(runs).toHaveLength(1)
	expect(w.checks).toHaveLength(2)
	expect(postsTo(w, SETTLED)).toEqual([])
})

test('a switchboard tool is neither checked nor reported', async ($, on) => {
	const w = world(on)
	w.checkDecision = 'ask'
	await $.tool.call({ tool: 'mcp__switchboard__notify_human', message: 'hi', sender: 'Claude' })
	await w.clock.settle()
	expect(w.checks).toEqual([])
	expect(postsTo(w, SETTLED)).toEqual([])
})
