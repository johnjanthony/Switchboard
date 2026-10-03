import { describe, expect, test } from 'claude-code/testing'

import { buildDetail, preToolState } from '../status'

describe('preToolState', () => {
	test('ask_human reports clear', () => {
		expect(preToolState('mcp__switchboard__ask_human', {})).toEqual({ state: 'clear' })
	})

	test('message_and_await_agent reports waiting', () => {
		expect(preToolState('mcp__switchboard__message_and_await_agent', {})).toEqual({ state: 'waiting' })
	})

	test('another tool reports tool:<name> with its detail', () => {
		expect(preToolState('Bash', { command: 'git status' })).toEqual({ state: 'tool:Bash', detail: 'git status' })
	})

	test('a tool with no detail carries no detail key', () => {
		expect(preToolState('TodoWrite', { todos: [] })).toEqual({ state: 'tool:TodoWrite' })
	})
})

describe('buildDetail', () => {
	test('file tools report the file name only', () => {
		expect(buildDetail('Edit', { file_path: 'C:\\Work\\Switchboard\\server\\main.py' })).toBe('main.py')
	})

	test('WebFetch reports the host', () => {
		expect(buildDetail('WebFetch', { url: 'https://code.claude.com/docs/en/plugins' })).toBe('code.claude.com')
	})

	test('an unparsable URL is reported as given', () => {
		expect(buildDetail('WebFetch', { url: 'not a url' })).toBe('not a url')
	})

	test('Glob and Grep report the pattern', () => {
		expect(buildDetail('Grep', { pattern: 'queue_notice' })).toBe('queue_notice')
	})

	test('details are capped at 200 characters', () => {
		expect(buildDetail('Bash', { command: 'x'.repeat(250) })).toHaveLength(200)
	})

	test('an empty field gives no detail', () => {
		expect(buildDetail('Bash', { command: '' })).toBeUndefined()
	})
})
