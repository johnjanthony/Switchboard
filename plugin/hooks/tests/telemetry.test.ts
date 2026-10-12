import { describe, expect, test } from 'claude-code/testing'

import { claudeConfigRoots, effortLevel, latestTitle, measureReport } from '../telemetry'

const LIMITS = [
	{ kind: 'five_hour', percentUsed: 10, resetsAt: '2026-10-10T23:20:00.000Z' },
	{ kind: 'seven_day', percentUsed: 73, resetsAt: '2026-10-13T19:00:00.000Z' },
]
const REPORTED = [
	{ kind: 'five_hour', percent_used: 10, resets_at: '2026-10-10T23:20:00.000Z' },
	{ kind: 'seven_day', percent_used: 73, resets_at: '2026-10-13T19:00:00.000Z' },
]

describe('measureReport', () => {
	test('a first measurement after a response reports the context and both rate-limit windows', () => {
		expect(measureReport({ context: { tokens: 65416, window: 1000000, percent: 7 }, rateLimits: LIMITS, changed: ['context', 'rateLimits', 'cost'] }, true))
			.toEqual({ context: { tokens: 65416, window: 1000000 }, rate_limits: REPORTED })
	})

	test('after a response the rate limits go even when they have not moved', () => {
		expect(measureReport({ context: { tokens: 10, window: 100 }, rateLimits: LIMITS, changed: ['context'] }, true))
			.toEqual({ context: { tokens: 10, window: 100 }, rate_limits: REPORTED })
	})

	test('with no response since they last went, the rate limits stay out even when listed as moved', () => {
		expect(measureReport({ context: { tokens: 10, window: 100 }, rateLimits: LIMITS, changed: ['context', 'rateLimits'] }, false))
			.toEqual({ context: { tokens: 10, window: 100 } })
	})

	test('a cost-only change reports nothing', () => {
		expect(measureReport({ context: { window: 100 }, rateLimits: [], changed: ['cost'] }, false)).toEqual({})
	})

	test('a window without a fill reports the window alone', () => {
		expect(measureReport({ context: { window: 200000 }, rateLimits: [], changed: ['context'] }, false)).toEqual({ context: { window: 200000 } })
	})

	test('an empty rate-limit list, as off a subscription, is not reported', () => {
		expect(measureReport({ context: { window: 1 }, rateLimits: [], changed: ['rateLimits'] }, true)).toEqual({})
	})
})

describe('effortLevel', () => {
	test('a level name passes through', () => {
		expect(effortLevel('xhigh')).toBe('xhigh')
	})

	test('no effort, a numeric budget, or an unknown name is null', () => {
		expect(effortLevel(undefined)).toBeNull()
		expect(effortLevel(32000)).toBeNull()
		expect(effortLevel('turbo')).toBeNull()
	})
})

describe('latestTitle', () => {
	const ai = (name: string) => JSON.stringify({ type: 'ai-title', aiTitle: name, sessionId: 'S1' })
	const custom = (name: string) => JSON.stringify({ type: 'custom-title', customTitle: name, sessionId: 'S1' })
	const user = (text: string) => JSON.stringify({ type: 'user', message: { content: text } })

	test('the last ai-title row wins among ai-titles', () => {
		expect(latestTitle([ai('First'), user('hi'), ai('Second'), ''].join('\n'))).toEqual({ name: 'Second', source: 'ai-title' })
	})

	test('a custom title beats a later ai-title', () => {
		expect(latestTitle([custom('Mine'), ai('Generated'), ''].join('\n'))).toEqual({ name: 'Mine', source: 'custom-title' })
	})

	test('no title rows means no title', () => {
		expect(latestTitle([user('hi'), ''].join('\n'))).toBeUndefined()
	})

	test('a half-written last row falls back to the previous one', () => {
		const text = [custom('Done'), user('x'), custom('Half').slice(0, 30)].join('\n')
		expect(latestTitle(text)).toEqual({ name: 'Done', source: 'custom-title' })
	})

	test('a title marker inside a line that is not a title row is ignored', () => {
		const text = `${ai('Real')}\n{"type":"user","text":"x"} "type":"custom-title","customTitle":"Fake"\n`
		expect(latestTitle(text)).toEqual({ name: 'Real', source: 'ai-title' })
	})

	test('a blank title is skipped', () => {
		expect(latestTitle([ai('Kept'), ai('   '), ''].join('\n'))).toEqual({ name: 'Kept', source: 'ai-title' })
	})

	test('CRLF rows parse', () => {
		expect(latestTitle(`${ai('Windows')}\r\n`)).toEqual({ name: 'Windows', source: 'ai-title' })
	})
})

describe('claudeConfigRoots', () => {
	test('CLAUDE_CONFIG_DIR alone when set', () => {
		expect(claudeConfigRoots('D:/cc', 'C:/Users/J', 'C:/Users/J')).toEqual(['D:/cc'])
	})

	test('else HOME then USERPROFILE, each plus /.claude', () => {
		expect(claudeConfigRoots(undefined, 'C:/Users/H', 'C:/Users/J')).toEqual(['C:/Users/H/.claude', 'C:/Users/J/.claude'])
	})

	test('a root named twice is tried once', () => {
		expect(claudeConfigRoots(undefined, 'C:/Users/J', 'C:/Users/J')).toEqual(['C:/Users/J/.claude'])
	})

	test('only one set, or none', () => {
		expect(claudeConfigRoots(undefined, '/home/j', undefined)).toEqual(['/home/j/.claude'])
		expect(claudeConfigRoots(undefined, undefined, 'C:\\Users\\J')).toEqual(['C:\\Users\\J/.claude'])
		expect(claudeConfigRoots(undefined, undefined, undefined)).toEqual([])
	})
})
