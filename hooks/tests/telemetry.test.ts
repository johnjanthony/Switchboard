import { describe, expect, test } from 'claude-code/testing'

import { claudeConfigDir, effortLevel, latestTitle, measureReport } from '../telemetry'

describe('measureReport', () => {
	test('a first measurement reports the context and both rate-limit windows', () => {
		expect(measureReport({
			context: { tokens: 65416, window: 1000000, percent: 7 },
			rateLimits: [
				{ kind: 'five_hour', percentUsed: 10, resetsAt: '2026-10-10T23:20:00.000Z' },
				{ kind: 'seven_day', percentUsed: 73, resetsAt: '2026-10-13T19:00:00.000Z' },
			],
			changed: ['context', 'rateLimits', 'cost'],
		})).toEqual({
			context: { tokens: 65416, window: 1000000 },
			rate_limits: [
				{ kind: 'five_hour', percent_used: 10, resets_at: '2026-10-10T23:20:00.000Z' },
				{ kind: 'seven_day', percent_used: 73, resets_at: '2026-10-13T19:00:00.000Z' },
			],
		})
	})

	test('only the units that moved are reported', () => {
		expect(measureReport({ context: { tokens: 10, window: 100 }, rateLimits: [{ kind: 'five_hour', percentUsed: 1 }], changed: ['context'] }))
			.toEqual({ context: { tokens: 10, window: 100 } })
	})

	test('a cost-only change reports nothing', () => {
		expect(measureReport({ context: { window: 100 }, rateLimits: [], changed: ['cost'] })).toEqual({})
	})

	test('a window without a fill reports the window alone', () => {
		expect(measureReport({ context: { window: 200000 }, rateLimits: [], changed: ['context'] })).toEqual({ context: { window: 200000 } })
	})

	test('an empty rate-limit list, as off a subscription, is not reported', () => {
		expect(measureReport({ context: { window: 1 }, rateLimits: [], changed: ['rateLimits'] })).toEqual({})
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

describe('claudeConfigDir', () => {
	test('CLAUDE_CONFIG_DIR wins', () => {
		expect(claudeConfigDir('D:/cc', 'C:/Users/J', 'C:/Users/J')).toBe('D:/cc')
	})

	test('else HOME, else USERPROFILE, plus /.claude', () => {
		expect(claudeConfigDir(undefined, '/home/j', undefined)).toBe('/home/j/.claude')
		expect(claudeConfigDir(undefined, undefined, 'C:\\Users\\J')).toBe('C:\\Users\\J/.claude')
	})

	test('nothing set means no directory', () => {
		expect(claudeConfigDir(undefined, undefined, undefined)).toBeUndefined()
	})
})
