// Pure: what the mod remembers of a tool call in flight, and the report it
// sends once a call whose permission dialog the server may hold has been
// decided: allowed (it reached tool.run) or refused (it ended without).

// A tool call between tool.call and its result. `input` is the call as the
// permission check sees it; `asks` resolves to whether the engine's own check
// predicted a dialog; `ran` is set when the call reaches tool.run.
export type InFlight = {
	tool: string
	agentId: string | null
	input: Record<string, unknown>
	asks: Promise<boolean>
	ran: boolean
}

// The body of POST /sessions/{sid}/approvals/settled.
export type SettledBody = {
	tool_use_id: string
	tool: string
	agent_id: string | null
	input: Record<string, unknown>
	decision: string
	source: string
}

// The tools whose dialog the server may hold, so the mod reports on them. The
// switchboard's own tools are allowed by the plugin's PermissionRequest entry
// before the server sees them, and AskUserQuestion and ExitPlanMode dialogs
// are left to the terminal (server/approvals.py EXCLUDED_TOOLS).
export const REPORTED_TOOL = /^(?!mcp__switchboard__|AskUserQuestion$|ExitPlanMode$)/

// The keys tool.call carries beside the tool's own arguments. The engine strips
// them before the permission check, so the PermissionRequest hook's tool_input
// lacks them too.
const RESERVED = new Set(['tool', 'tool_use_id', 'consent', 'agentId', 'requestMeta'])

export function inFlightCall(call: Readonly<Record<string, unknown>>): Pick<InFlight, 'tool' | 'agentId' | 'input'> {
	const input: Record<string, unknown> = {}
	for (const [key, value] of Object.entries(call)) if (!RESERVED.has(key)) input[key] = value
	return { tool: String(call.tool), agentId: typeof call.agentId === 'string' ? call.agentId : null, input }
}

// Whether a $.tool.check verdict means a dialog opens. It is a prediction: the
// query asks no PreToolUse hook or classifier, so a hook's allow can still skip
// the dialog (the server then logs one unmatched report).
export function predictsDialog(verdict: unknown): boolean {
	return typeof verdict === 'object' && verdict !== null && (verdict as { decision?: unknown }).decision === 'ask'
}

// The report for a call that was decided: allowed once it reached tool.run,
// refused when it ended without. Either way the server releases the hook of an
// approval still open for it; one a phone answer already closed is a replay.
export function settledBody(toolUseId: string, call: Pick<InFlight, 'tool' | 'agentId' | 'input' | 'ran'>): SettledBody {
	return {
		tool_use_id: toolUseId,
		tool: call.tool,
		agent_id: call.agentId,
		input: call.input,
		decision: call.ran ? 'accept' : 'reject',
		source: call.ran ? 'tool_run' : 'tool_refused',
	}
}
