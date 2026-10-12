// The pure half of the session telemetry the mod reports: what goes in a
// report, how a title is read out of a transcript, and where transcripts live.
// The calls themselves live in switchboard.ts, because the engine follows `$`
// only into functions declared in the hooks module.

export const EFFORT_LEVELS = ['low', 'medium', 'high', 'xhigh', 'max'] as const

export type Title = { name: string; source: 'custom-title' | 'ai-title' }

export type MeasureLike = {
	context: { tokens?: number; window: number; percent?: number }
	rateLimits: ReadonlyArray<{ kind: string; percentUsed: number; resetsAt?: string }>
	changed: readonly string[]
}

// The telemetry body for one session.measure: the context when it moved, and
// the rate limits whenever an API response has arrived since they last went
// (`fresh`), moved or not, so the server can stamp the quota as confirmed. A
// measurement with no response behind it (before the first request, or after
// /clear) leaves them out: its figures may be hours old. The whole-number
// percent is left out; the server computes its own fraction.
export function measureReport(e: MeasureLike, fresh: boolean): Record<string, unknown> {
	const report: Record<string, unknown> = {}
	if (e.changed.includes('context')) {
		report.context = { ...(e.context.tokens === undefined ? {} : { tokens: e.context.tokens }), window: e.context.window }
	}
	if (fresh && e.rateLimits.length > 0) {
		report.rate_limits = e.rateLimits.map(r => ({
			kind: r.kind,
			percent_used: r.percentUsed,
			...(r.resetsAt === undefined ? {} : { resets_at: r.resetsAt }),
		}))
	}
	return report
}

// A numeric effort (a raw thinking budget) is reported as null: the server's
// label logic knows only the level names.
export function effortLevel(effort: string | number | undefined): string | null {
	return typeof effort === 'string' && (EFFORT_LEVELS as readonly string[]).includes(effort) ? effort : null
}

const TITLE_ROWS = [
	{ type: 'custom-title', field: 'customTitle' },
	{ type: 'ai-title', field: 'aiTitle' },
] as const

// The session's latest title: the last custom-title row (a /rename) wins over
// the last ai-title row. Rows are found by string search, not by parsing every
// line, since a transcript can run to megabytes.
export function latestTitle(transcript: string): Title | undefined {
	for (const row of TITLE_ROWS) {
		const name = lastRowField(transcript, row.type, row.field)
		if (name !== undefined) return { name, source: row.type }
	}
	return undefined
}

// The field of the last row of this type that parses, searching backwards so a
// half-written trailing row (Claude Code still appending) is skipped.
function lastRowField(transcript: string, type: string, field: string): string | undefined {
	const marker = `"type":"${type}"`
	let at = transcript.lastIndexOf(marker)
	while (at >= 0) {
		const start = transcript.lastIndexOf('\n', at) + 1
		const end = transcript.indexOf('\n', at)
		const value = rowField(transcript.slice(start, end < 0 ? undefined : end), type, field)
		if (value !== undefined) return value
		at = start === 0 ? -1 : transcript.lastIndexOf(marker, start - 1)
	}
	return undefined
}

function rowField(line: string, type: string, field: string): string | undefined {
	try {
		const row = JSON.parse(line) as Record<string, unknown>
		const value = row[field]
		return row.type === type && typeof value === 'string' && value.trim() !== '' ? value.trim() : undefined
	} catch {
		return undefined
	}
}

// Where Claude Code keeps its transcripts: CLAUDE_CONFIG_DIR, else ~/.claude.
export function claudeConfigDir(configDir: string | undefined, home: string | undefined, userProfile: string | undefined): string | undefined {
	if (configDir) return configDir
	const base = home ?? userProfile
	return base ? `${base}/.claude` : undefined
}
