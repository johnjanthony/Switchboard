// The pure half of talking to the switchboard server: addresses, headers and
// the inbox answer's shape. The calls themselves live in switchboard.ts, because
// the engine follows `$` only into functions declared in the hooks module.

export const DEFAULT_BASE_URL = 'http://127.0.0.1:9876'

export type InboxAnswer = { notices: string[]; stop: boolean; away: boolean; pending_ask: boolean }

export type StatusBody = { session_id: string; state: string; event: string; cwd?: string; detail?: string }

// Base URL and token come from the variables the Python hooks read, so WSL
// sessions keep working behind the server's Bearer gate.
export function requestHeaders(token: string | undefined, hasBody: boolean): Record<string, string> {
	return {
		...(hasBody ? { 'Content-Type': 'application/json' } : {}),
		...(token ? { Authorization: `Bearer ${token}` } : {}),
	}
}

// Throws on a body that is not a JSON object (an HTML error page from a proxy),
// so the caller treats it as a failed poll.
export function parseInboxBody(text: string): InboxAnswer {
	const data: unknown = JSON.parse(text)
	if (typeof data !== 'object' || data === null) throw new Error('inbox answered a non-object')
	const raw = data as Record<string, unknown>
	const notices = Array.isArray(raw.notices) ? raw.notices.filter((n): n is string => typeof n === 'string') : []
	return { notices, stop: raw.stop === true, away: raw.away === true, pending_ask: raw.pending_ask === true }
}
