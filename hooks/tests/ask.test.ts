import { describe, expect, test } from 'claude-code/testing'

import { isTerminal, phoneQuestion } from '../ask'
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
