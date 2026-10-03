import type { InboxAnswer } from './client'

// The away-mode instruction for an agent ending its turn while John is away.
// Verbatim REDIRECT_REASON_AWAY_MODE from scripts/turn-end-hook-away-mode.py.
export const REDIRECT_REASON_AWAY_MODE = [
	'You are in away mode. John is on his phone, not watching the terminal.',
	'To hand your turn back, call ask_human() and wait for his reply. This is the correct and ONLY way to end your turn while away.',
	"If the harness moves your ask_human call to a background task (~120s), do NOT call ask_human again - that would supersede the live question. Just end your turn: the pending question is your handback, this check will allow it, and John's reply arrives as the background task's result.",
	'Do NOT end your turn with notify_human(): it is non-blocking, so it will not end the turn and you will loop straight back to this message. (You may call notify_human() to push a status update, but you must still end on ask_human().)',
	'Only call set_away_mode(False) if John has explicitly told you he is back.',
].join('\n')

// Held items block the turn end so the agent reads them; away mode with no live
// ask adds the redirect. A live blocking ask is the agent's handback, so it lets
// the turn end (the harness backgrounds ask_human calls past ~120s).
export function turnEndBlock(heldItems: readonly string[], answer: InboxAnswer | null): string | undefined {
	const parts: string[] = []
	if (heldItems.length > 0) parts.push(heldItems.join('\n\n'))
	if (answer !== null && answer.away && !answer.pending_ask) parts.push(REDIRECT_REASON_AWAY_MODE)
	return parts.length === 0 ? undefined : parts.join('\n\n')
}
