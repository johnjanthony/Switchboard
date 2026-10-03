import type { McpToolResult } from 'claude-code'

// In away mode AskUserQuestion would render only in the terminal John is not
// watching, so each question goes to his phone through ask_human instead. This
// file holds the wording and the reading of replies; the calls themselves live
// in switchboard.ts, because the engine follows `$` only within the hooks module.

export type AskOption = { label: string; description?: string }
export type AskQuestion = { question: string; header: string; options: readonly AskOption[]; multiSelect: boolean }

export function phoneQuestion(q: AskQuestion, index: number, total: number): string {
	const numbered = total > 1 ? `(${index + 1} of ${total}) ` : ''
	const lines = [`${numbered}${q.header}: ${q.question}`]
	for (const option of q.options) {
		if (option.description) lines.push(`- ${option.label}: ${option.description}`)
	}
	if (q.multiSelect) lines.push('Pick one or more, comma-separated.')
	return lines.join('\n')
}

// $.mcp.call hands back a switchboard tool's text wrapped as {"result": "<reply>"}
// (verified live 2026-10-03), so the reply is unwrapped; any other text is taken
// as the reply itself.
export function replyText(result: McpToolResult): string {
	const text = result.content.map(block => (block.type === 'text' ? (block.text ?? '') : '')).join('')
	try {
		const parsed: unknown = JSON.parse(text)
		if (typeof parsed === 'object' && parsed !== null && !Array.isArray(parsed)) {
			const fields = parsed as Record<string, unknown>
			if (Object.keys(fields).length === 1 && typeof fields.result === 'string') return fields.result
		}
	} catch {
		// Not JSON: a bare reply.
	}
	return text
}

// ask_human answers a terminal state with a JSON envelope ({"status": ...}) and
// a refusal with text starting "ERROR:"; anything else is John's reply.
export function isTerminal(reply: string): boolean {
	return reply.startsWith('{"status":') || reply.startsWith('ERROR:')
}
