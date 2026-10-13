// Pure: what the mod remembers of a tool call in flight, and the report it
// sends when a person answers that call's permission dialog at the terminal.

// A tool call between tool.call and its result: what a tool_decision record
// lacks. `input` is the call as the permission check sees it.
export type InFlight = { tool: string; agentId: string | null; input: Record<string, unknown> }

// The body of POST /sessions/{sid}/approvals/settled.
export type SettledBody = {
	tool_use_id: string
	tool: string
	agent_id: string | null
	input: Record<string, unknown>
	decision: string
	source: string
}

// The keys tool.call carries beside the tool's own arguments. The engine strips
// them before the permission check, so the PermissionRequest hook's tool_input
// lacks them too.
const RESERVED = new Set(['tool', 'tool_use_id', 'consent', 'agentId', 'requestMeta'])

export function inFlightCall(call: Readonly<Record<string, unknown>>): InFlight {
	const input: Record<string, unknown> = {}
	for (const [key, value] of Object.entries(call)) if (!RESERVED.has(key)) input[key] = value
	return { tool: String(call.tool), agentId: typeof call.agentId === 'string' ? call.agentId : null, input }
}

// The report for a tool_decision record a person made at the terminal (source
// user_*) on a call the mod saw start; undefined otherwise. A hook's or a rule's
// decision reports nothing: the server already knows, or no dialog opened.
export function settledReport(
	attributes: Readonly<Record<string, unknown>>,
	inflight: ReadonlyMap<string, InFlight>,
): SettledBody | undefined {
	const source = typeof attributes.source === 'string' ? attributes.source : ''
	if (!source.startsWith('user')) return undefined
	const toolUseId = typeof attributes.tool_use_id === 'string' ? attributes.tool_use_id : ''
	const call = inflight.get(toolUseId)
	if (call === undefined) return undefined
	return {
		tool_use_id: toolUseId,
		tool: call.tool,
		agent_id: call.agentId,
		input: call.input,
		decision: typeof attributes.decision === 'string' ? attributes.decision : '',
		source,
	}
}
