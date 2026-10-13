// The away-mode instruction for an agent ending its turn while John is away.
export const REDIRECT_REASON_AWAY_MODE = [
	'You are in away mode. John is on his phone, not watching the terminal.',
	'To hand your turn back, call ask_human() and wait for his reply. This is the correct and ONLY way to end your turn while away.',
	"Only if your ask_human call came back saying it was moved to a background task (~120s) is a question still live: then do NOT call ask_human again (that would supersede it). Just end your turn: the pending question is your handback, this check will allow it, and John's reply arrives as the background task's result. An ask_human that was denied, rejected or returned an error is NOT a live question: call ask_human again.",
	'Do NOT end your turn with notify_human(): it is non-blocking, so it will not end the turn and you will loop straight back to this message. (You may call notify_human() to push a status update, but you must still end on ask_human().)',
	'Only call set_away_mode(False) if John has explicitly told you he is back.',
].join('\n')

// Bare redirects in a row before the turn-end check stops and tells John. An agent
// that answers the redirect in text would otherwise loop about once a second.
export const REDIRECT_CAP = 2

export const REDIRECT_CAP_NOTICE = `This session ended ${REDIRECT_CAP + 1} turns in a row without asking you anything, so the away-mode reminder has stopped. It is idle now; reply here to give it its next instruction.`

export type RedirectPlan = { submit: string; redirects: number } | { cap: true; redirects: number } | { quiet: true; redirects: number }

// An away-mode turn end with no live ask. `redirects` counts the bare redirects
// (the redirect with no held items) since the session's last real input. Held
// items are new input and always go, the redirect after them. At the cap the
// count moves past it, so John is told once and a later turn end stays quiet.
export function redirectPlan(heldItems: readonly string[], redirects: number): RedirectPlan {
	if (heldItems.length > 0) return { submit: [...heldItems, REDIRECT_REASON_AWAY_MODE].join('\n\n'), redirects }
	if (redirects < REDIRECT_CAP) return { submit: REDIRECT_REASON_AWAY_MODE, redirects: redirects + 1 }
	if (redirects === REDIRECT_CAP) return { cap: true, redirects: redirects + 1 }
	return { quiet: true, redirects }
}

// The mod's own bare redirect is the one prompt that does not start a new chain.
export function isOwnRedirect(origin: { kind: string; name?: string } | undefined, text: string): boolean {
	return origin !== undefined && origin.kind === 'plugin' && origin.name === 'switchboard' && text === REDIRECT_REASON_AWAY_MODE
}

// A question that reached John and ran: a blocking ask_human, or AskUserQuestion
// (from the phone or the terminal). A rejected dialog comes back as isError, not
// deny. A background ask does not count: it does not end an away-mode turn either.
export function askedJohn(tool: string, input: Readonly<Record<string, unknown>>, ran: { deny?: string; isError?: boolean }): boolean {
	if (ran.deny !== undefined || ran.isError === true) return false
	if (tool === 'AskUserQuestion') return true
	return tool === 'mcp__switchboard__ask_human' && input.background !== true
}
