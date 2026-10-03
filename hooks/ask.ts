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

// Claude Code moves an MCP call still running after 120 s to a background task,
// the plugin's own $.mcp.call included, and hands the caller its own text in
// place of the reply. The question is then still live on John's phone, and his
// answer reaches the model as that task's result, never the bridge. Matching
// Claude Code's wording is fragile: a test pins the text seen on 2.1.288.
export function backgroundedTaskId(reply: string): string | undefined {
	return /^MCP tool "[^"]+" is still running after \d+s\. It was moved to the background as task (\S+?) /.exec(reply)?.[1]
}

// What the model is told when a question went to the background: where the
// answer will come from, what was answered already, and what was never asked.
export function backgroundedDeny(
	questions: readonly AskQuestion[],
	index: number,
	taskId: string,
	answers: Readonly<Record<string, string>>,
): string {
	const parts = [
		`John has not answered yet: question ${index + 1} of ${questions.length} ("${questions[index]?.question ?? ''}") is still live on his phone as background task ${taskId}, and his reply will arrive as that task's result.`,
		'Do not ask it again or call ask_human before it arrives: a new ask would replace it on his phone.',
	]
	const answered = Object.entries(answers).map(([question, reply]) => `"${question}": ${reply}`)
	if (answered.length > 0) parts.push(`Answered so far: ${answered.join('; ')}.`)
	const rest = questions.slice(index + 1).map(q => `"${q.question}"`)
	if (rest.length > 0) parts.push(`Not asked yet: ${rest.join('; ')}. Ask them with ask_human once his reply arrives.`)
	return parts.join(' ')
}

// ask_human answers a terminal state with a JSON envelope ({"status": ...}) and
// a refusal with text starting "ERROR:"; anything else is John's reply.
export function isTerminal(reply: string): boolean {
	return reply.startsWith('{"status":') || reply.startsWith('ERROR:')
}

// The engine leaves stopping in-flight work to the plugin: an interrupted turn
// must not keep waiting on John's phone, or send it the next question.
export function raceAbort<T>(work: Promise<T>, signal: AbortSignal): Promise<T> {
	if (signal.aborted) return Promise.reject(new Error('interrupted'))
	return new Promise<T>((resolve, reject) => {
		const onAbort = (): void => reject(new Error('interrupted'))
		signal.addEventListener('abort', onAbort, { once: true })
		work.then(
			value => {
				signal.removeEventListener('abort', onAbort)
				resolve(value)
			},
			error => {
				signal.removeEventListener('abort', onAbort)
				reject(error)
			},
		)
	})
}
