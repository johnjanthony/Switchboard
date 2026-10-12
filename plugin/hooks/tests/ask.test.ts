import { describe, expect, test } from 'claude-code/testing'

import { backgroundedTaskId, isTerminal, phoneQuestion, raceAbort } from '../ask'
import { world } from './harness'

const BRANCH = {
	question: 'Which branch?',
	header: 'Branch',
	options: [
		{ label: 'develop', description: 'the default' },
		{ label: 'main', description: 'protected' },
	],
	multiSelect: false,
}
const PUSH = {
	question: 'Push now?',
	header: 'Push',
	options: [
		{ label: 'yes', description: '' },
		{ label: 'no', description: '' },
	],
	multiSelect: false,
}

describe('phoneQuestion', () => {
	test('one question keeps its header and option descriptions', () => {
		expect(phoneQuestion(BRANCH, 0, 1)).toBe('Branch: Which branch?\n- develop: the default\n- main: protected')
	})

	test('several questions are numbered', () => {
		expect(phoneQuestion(PUSH, 1, 2)).toBe('(2 of 2) Push: Push now?')
	})

	test('a multi-select question says how to answer', () => {
		expect(phoneQuestion({ ...PUSH, multiSelect: true }, 0, 1)).toBe('Push: Push now?\nPick one or more, comma-separated.')
	})
})

test('ask_human terminal answers are recognised', () => {
	expect(isTerminal('{"status":"timeout"}')).toBe(true)
	expect(isTerminal('ERROR: John is at his desk. Ask in the terminal.')).toBe(true)
	expect(isTerminal('develop')).toBe(false)
})

test('at the desk AskUserQuestion runs as usual', async ($, on) => {
	const w = world(on)
	const ran = await $.tool.call({ tool: 'AskUserQuestion', questions: [BRANCH] })
	expect(ran.result).toBe('ran')
	expect(w.mcpCalls).toEqual([])
})

test('away, each question goes to the phone and the replies come back as answers', async ($, on) => {
	const w = world(on)
	w.inbox.push({ away: true })
	w.mcpReplies.push('develop', 'yes')
	const ran = await $.tool.call({ tool: 'AskUserQuestion', questions: [BRANCH, PUSH] })
	expect(ran.result).toEqual({ questions: [BRANCH, PUSH], answers: { 'Which branch?': 'develop', 'Push now?': 'yes' } })
	expect(w.mcpCalls).toEqual([
		{
			server: 'switchboard',
			tool: 'ask_human',
			args: {
				question: '(1 of 2) Branch: Which branch?\n- develop: the default\n- main: protected',
				suggestions: ['develop', 'main'],
				sender: 'Claude',
				cli_session_id: 'S1',
				cwd: 'C:/Work/X',
			},
		},
		{
			server: 'switchboard',
			tool: 'ask_human',
			args: { question: '(2 of 2) Push: Push now?', suggestions: ['yes', 'no'], sender: 'Claude', cli_session_id: 'S1', cwd: 'C:/Work/X' },
		},
	])
	expect(w.toolInputs).toEqual([])
})

test('a terminal answer from ask_human denies the call with the reason', async ($, on) => {
	const w = world(on)
	w.inbox.push({ away: true })
	w.mcpReplies.push('{"status":"timeout"}')
	const ran = await $.tool.call({ tool: 'AskUserQuestion', questions: [BRANCH] })
	expect(ran.deny).toBe('John\'s phone did not answer this question: {"status":"timeout"}')
})

test('with the server down AskUserQuestion runs in the terminal', async ($, on) => {
	const w = world(on)
	w.fetchError = 'connect ECONNREFUSED 127.0.0.1:9876'
	const ran = await $.tool.call({ tool: 'AskUserQuestion', questions: [BRANCH] })
	expect(ran.result).toBe('ran')
	expect(w.mcpCalls).toEqual([])
})

// $.mcp.call hands back each switchboard reply wrapped as {"result": "<reply>"}
// (verified live 2026-10-03); the bare replies above cover any server that does not.
test('a reply wrapped the way the live server sends it reaches the model unwrapped', async ($, on) => {
	const w = world(on)
	w.inbox.push({ away: true })
	w.mcpReplies.push('{"result":"develop"}')
	const ran = await $.tool.call({ tool: 'AskUserQuestion', questions: [BRANCH] })
	expect(ran.result).toEqual({ questions: [BRANCH], answers: { 'Which branch?': 'develop' } })
})

test('a wrapped terminal answer is recognised and denies the call', async ($, on) => {
	const w = world(on)
	w.inbox.push({ away: true })
	w.mcpReplies.push(JSON.stringify({ result: '{"status": "timeout"}' }))
	const ran = await $.tool.call({ tool: 'AskUserQuestion', questions: [BRANCH] })
	expect(ran.deny).toBe(`John's phone did not answer this question: {"status": "timeout"}`)
})

describe('raceAbort (a bridged question stops with its turn)', () => {
	test('a wait that finishes first resolves', async () => {
		expect(await raceAbort(Promise.resolve('develop'), new AbortController().signal)).toBe('develop')
	})

	test('an interrupted wait rejects at once', async () => {
		const controller = new AbortController()
		const waiting = raceAbort(new Promise<string>(() => {}), controller.signal)
		controller.abort()
		let error: unknown
		try {
			await waiting
		} catch (caught) {
			error = caught
		}
		expect(String(error)).toContain('interrupted')
	})

	test('a wait already interrupted rejects', async () => {
		let error: unknown
		try {
			await raceAbort(Promise.resolve('develop'), AbortSignal.abort())
		} catch (caught) {
			error = caught
		}
		expect(String(error)).toContain('interrupted')
	})
})

// Claude Code moves an MCP call still running after 120 s to a background task
// and hands the caller this text instead of the reply, the mod's own $.mcp.call
// included (seen live 2026-10-03, Claude Code 2.1.288).
const BACKGROUNDED = 'MCP tool "switchboard/ask_human" is still running after 120s. It was moved to the background as task ktc87fbll and keeps running; you\'ll receive a notification with the result when it completes. You can keep working in the meantime. To stop it, use TaskStop with task_id "ktc87fbll". Note: it does not survive exiting this session.'

describe('backgroundedTaskId', () => {
	test('the harness text for a backgrounded call names its task', () => {
		expect(backgroundedTaskId(BACKGROUNDED)).toBe('ktc87fbll')
	})

	test('a reply is not a backgrounded call', () => {
		expect(backgroundedTaskId('develop')).toBeUndefined()
		expect(backgroundedTaskId('run it in the background later')).toBeUndefined()
	})
})

test('a question moved to the background stops the bridge without asking the rest', async ($, on) => {
	const w = world(on)
	w.inbox.push({ away: true })
	w.mcpReplies.push(BACKGROUNDED)
	const ran = await $.tool.call({ tool: 'AskUserQuestion', questions: [BRANCH, PUSH] })
	expect(w.mcpCalls).toHaveLength(1)
	expect(ran.deny).toContain('background task ktc87fbll')
	expect(ran.deny).toContain('Which branch?')
	expect(ran.deny).toContain('Not asked yet: "Push now?"')
})

test('answers given before a question moved to the background are reported', async ($, on) => {
	const w = world(on)
	w.inbox.push({ away: true })
	w.mcpReplies.push('{"result":"develop"}', BACKGROUNDED)
	const ran = await $.tool.call({ tool: 'AskUserQuestion', questions: [BRANCH, PUSH] })
	expect(w.mcpCalls).toHaveLength(2)
	expect(ran.deny).toContain('"Which branch?": develop')
	expect(ran.deny).toContain('background task ktc87fbll')
})
