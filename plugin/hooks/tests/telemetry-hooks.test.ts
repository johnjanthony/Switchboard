import { expect, test } from 'claude-code/testing'

import type { World } from './harness'
import { postsTo, runCommand, startSession, step, world } from './harness'

const HOME = { HOME: 'C:/Users/J' }
const PATH = 'C:/Users/J/.claude/projects/C--Work-X/S1.jsonl'
const REFUSED = 'refused: the file is over the 4194304-byte limit'
const LIMITS = [{ kind: 'five_hour', percentUsed: 10, resetsAt: '2026-10-10T23:20:00.000Z' }]
const REPORTED = [{ kind: 'five_hour', percent_used: 10, resets_at: '2026-10-10T23:20:00.000Z' }]
const ai = (name: string) => `${JSON.stringify({ type: 'ai-title', aiTitle: name, sessionId: 'S1' })}\n`
const custom = (name: string) => `${JSON.stringify({ type: 'custom-title', customTitle: name, sessionId: 'S1' })}\n`

function telemetry(w: World, sessionId = 'S1'): Array<Record<string, unknown> | null> {
	return postsTo(w, `/sessions/${sessionId}/telemetry`).map(s => s.body)
}

async function turn($: Parameters<typeof startSession>[0], w: World, id: string): Promise<void> {
	await $.turn.start({ text: 'go', turnId: id })
	await $.turn.complete({ answer: '', durationMs: 1, isAborted: false, turnId: id, reason: 'answer' })
	await w.clock.settle()
}

test('a measurement after a model request posts the context and the rate limits', async ($, on) => {
	const w = world(on)
	await step($, { model: 'claude-opus-5-5', effort: 'high' })
	await $.session.measure({ context: { tokens: 65416, window: 1000000, percent: 7 }, rateLimits: LIMITS, changed: ['context'] })
	await w.clock.settle()
	expect(telemetry(w)).toEqual([
		{ model: 'claude-opus-5-5', effort: 'high' },
		{ context: { tokens: 65416, window: 1000000 }, rate_limits: REPORTED },
	])
})

test('a measurement before any model request, as at startup, leaves the rate limits out', async ($, on) => {
	const w = world(on)
	await $.session.measure({ context: { window: 1000000 }, rateLimits: LIMITS, changed: ['context', 'rateLimits'] })
	await w.clock.settle()
	expect(telemetry(w)).toEqual([{ context: { window: 1000000 } }])
})

test('the rate limits go once per model request, a subagent request included', async ($, on) => {
	const w = world(on)
	await step($, { model: 'claude-haiku-5-5', agentId: 'A1' })
	await $.session.measure({ context: { tokens: 1, window: 100 }, rateLimits: LIMITS, changed: ['context'] })
	await $.session.measure({ context: { tokens: 2, window: 100 }, rateLimits: LIMITS, changed: ['context'] })
	await w.clock.settle()
	expect(telemetry(w)).toEqual([
		{ context: { tokens: 1, window: 100 }, rate_limits: REPORTED },
		{ context: { tokens: 2, window: 100 } },
	])
})

test('after /clear the new session id sends no rate limits until its own model request', async ($, on) => {
	const w = world(on)
	await step($, { model: 'claude-haiku-5-5', agentId: 'A1' })
	w.sessionId = 'S2'
	await $.session.measure({ context: { tokens: 1, window: 100 }, rateLimits: LIMITS, changed: ['context', 'rateLimits'] })
	await w.clock.settle()
	expect(telemetry(w, 'S2')).toEqual([{ context: { tokens: 1, window: 100 } }])
})

test('a measurement that moved only cost posts nothing', async ($, on) => {
	const w = world(on)
	await $.session.measure({ context: { window: 1000000 }, rateLimits: [], cost: { usd: 1 }, changed: ['cost'] })
	await w.clock.settle()
	expect(telemetry(w)).toEqual([])
})

test('the main loop model and effort post once, then again only on a change', async ($, on) => {
	const w = world(on)
	await step($, { model: 'claude-opus-5-5', effort: 'high' })
	await step($, { model: 'claude-opus-5-5', effort: 'high' })
	await step($, { model: 'claude-opus-5-5', effort: 'xhigh' })
	await step($, { model: 'claude-haiku-5-5' })
	await w.clock.settle()
	expect(telemetry(w)).toEqual([
		{ model: 'claude-opus-5-5', effort: 'high' },
		{ model: 'claude-opus-5-5', effort: 'xhigh' },
		{ model: 'claude-haiku-5-5', effort: null },
	])
})

test('a subagent step reports nothing', async ($, on) => {
	const w = world(on)
	await step($, { model: 'claude-haiku-5-5', agentId: 'A1' })
	await w.clock.settle()
	expect(telemetry(w)).toEqual([])
})

test('after /clear the new session id reports its model afresh', async ($, on) => {
	const w = world(on)
	await step($, { model: 'claude-opus-5-5', effort: 'high' })
	w.sessionId = 'S2'
	await step($, { model: 'claude-opus-5-5', effort: 'high' })
	await w.clock.settle()
	expect(telemetry(w, 'S1')).toHaveLength(1)
	expect(telemetry(w, 'S2')).toEqual([{ model: 'claude-opus-5-5', effort: 'high' }])
})

test('the title is read after a turn, posted once, and not read again', async ($, on) => {
	const w = world(on, HOME)
	w.dirs = ['C--Other', 'C--Work-X']
	await startSession($, w) // the transcript is not written yet
	expect(telemetry(w)).toEqual([])
	w.files[PATH] = ai('Fix undo')
	await turn($, w, 'T1')
	await turn($, w, 'T2')
	expect(telemetry(w)).toEqual([{ title: { name: 'Fix undo', source: 'ai-title' } }])
	expect(w.reads).toEqual([PATH])
})

test('a transcript over 4 MiB ends the title search for the session', async ($, on) => {
	const w = world(on, HOME)
	w.dirs = ['C--Work-X']
	w.files[PATH] = ''
	w.readError = REFUSED
	await turn($, w, 'T1')
	await turn($, w, 'T2')
	expect(w.reads).toHaveLength(1)
	expect(telemetry(w)).toEqual([])
	expect(w.logs.some(l => l.to === 'debug' && l.text.includes('could not read the transcript'))).toBe(true)
})

test('/rename reads the transcript again and posts the custom title', async ($, on) => {
	const w = world(on, HOME)
	w.dirs = ['C--Work-X']
	w.files[PATH] = ai('Generated')
	await turn($, w, 'T1')
	w.files[PATH] = ai('Generated') + custom('Mine')
	await runCommand($, 'rename', 'Mine')
	await w.clock.settle()
	expect(telemetry(w)).toEqual([
		{ title: { name: 'Generated', source: 'ai-title' } },
		{ title: { name: 'Mine', source: 'custom-title' } },
	])
})

test('/rename on a transcript over 4 MiB falls back to its args', async ($, on) => {
	const w = world(on, HOME)
	w.dirs = ['C--Work-X']
	w.files[PATH] = ''
	w.readError = REFUSED
	await runCommand($, 'rename', '  From args  ')
	await w.clock.settle()
	expect(telemetry(w)).toEqual([{ title: { name: 'From args', source: 'custom-title' } }])
})

test('another command does not read the transcript', async ($, on) => {
	const w = world(on, HOME)
	w.dirs = ['C--Work-X']
	w.files[PATH] = ai('Generated')
	await runCommand($, 'compact')
	await w.clock.settle()
	expect(w.reads).toEqual([])
})

test('a failing server is logged once per failure streak', async ($, on) => {
	const w = world(on)
	const failures = () => w.logs.filter(l => l.text.includes('telemetry post failed')).length
	const measure = (tokens: number) =>
		$.session.measure({ context: { tokens, window: 100 }, rateLimits: [], changed: ['context'] })
	w.fetchError = 'connect ECONNREFUSED 127.0.0.1:9876'
	await measure(1)
	await measure(2)
	await w.clock.settle()
	expect(failures()).toBe(1)
	w.fetchError = null
	await measure(3)
	await w.clock.settle()
	w.fetchError = 'connect ECONNREFUSED 127.0.0.1:9876'
	await measure(4)
	await w.clock.settle()
	expect(failures()).toBe(2)
})

const VECTOR = '32495c7f04ecccc8c3dddc24cf463335502ae7eb098a8cffce64442bb67c06b5'
const MOD = { 'a.ts': 'one\r\ntwo\n', 'b.ts': 'three' }

function fingerprints(w: World, sessionId = 'S1'): Array<Record<string, unknown> | null> {
	return telemetry(w, sessionId).filter(body => body !== null && 'mod_fingerprint' in body)
}

test('a session reports the fingerprint of its mod once', async ($, on) => {
	const w = world(on)
	w.modFiles = { ...MOD }
	await step($, { model: 'claude-opus-5-5', effort: 'high' })
	await step($, { model: 'claude-sonnet-5-5', effort: 'high' })
	await w.clock.settle()
	expect(fingerprints(w)).toEqual([{ mod_fingerprint: VECTOR }])
})

test('a new session id gets its own fingerprint', async ($, on) => {
	const w = world(on)
	w.modFiles = { ...MOD }
	await step($, { model: 'claude-opus-5-5' })
	w.sessionId = 'S2'
	await step($, { model: 'claude-opus-5-5' })
	await w.clock.settle()
	expect(fingerprints(w, 'S1')).toEqual([{ mod_fingerprint: VECTOR }])
	expect(fingerprints(w, 'S2')).toEqual([{ mod_fingerprint: VECTOR }])
})

test('a mod with no hook files reports no fingerprint', async ($, on) => {
	const w = world(on)
	await step($, { model: 'claude-opus-5-5' })
	await w.clock.settle()
	expect(fingerprints(w)).toEqual([])
})

test('a failed read reports no fingerprint and says why in the debug log', async ($, on) => {
	const w = world(on)
	w.modFiles = { ...MOD }
	w.readError = 'refused'
	await step($, { model: 'claude-opus-5-5' })
	await w.clock.settle()
	expect(fingerprints(w)).toEqual([])
	expect(w.logs.some(l => l.to === 'debug' && l.text.includes('could not fingerprint the mod'))).toBe(true)
})
