// The agent-status mapping, ported from scripts/agent-status-hook.py: the state
// and one-line detail the phone shows while a session works.

const CLEAR_TOOLS = new Set(['mcp__switchboard__ask_human'])
const WAITING_TOOLS = new Set(['mcp__switchboard__message_and_await_agent'])
const FILE_TOOLS = new Set(['Edit', 'Write', 'Read', 'NotebookEdit'])
const DETAIL_CAP = 200

export type ToolState = { state: string; detail?: string }

function text(input: Record<string, unknown>, key: string): string | undefined {
	const value = input[key]
	return typeof value === 'string' && value !== '' ? value : undefined
}

export function buildDetail(tool: string, input: Record<string, unknown>): string | undefined {
	if (tool === 'Bash') return text(input, 'command')?.slice(0, DETAIL_CAP)
	if (FILE_TOOLS.has(tool)) {
		const path = text(input, 'file_path')
		if (path === undefined) return undefined
		return (path.replace(/\\/g, '/').split('/').pop() ?? path).slice(0, DETAIL_CAP)
	}
	if (tool === 'WebFetch') {
		const url = text(input, 'url')
		if (url === undefined) return undefined
		try {
			return (new URL(url).host || url).slice(0, DETAIL_CAP)
		} catch {
			return url.slice(0, DETAIL_CAP)
		}
	}
	if (tool === 'Glob' || tool === 'Grep') return text(input, 'pattern')?.slice(0, DETAIL_CAP)
	return undefined
}

export function preToolState(tool: string, input: Record<string, unknown>): ToolState {
	if (CLEAR_TOOLS.has(tool)) return { state: 'clear' }
	if (WAITING_TOOLS.has(tool)) return { state: 'waiting' }
	const detail = buildDetail(tool, input)
	return detail === undefined ? { state: `tool:${tool}` } : { state: `tool:${tool}`, detail }
}
