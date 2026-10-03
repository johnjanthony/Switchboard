// The inbox poll's timing. The poller and the delivery rules themselves live in
// switchboard.ts, because the engine follows `$` only within the hooks module.

export const POLL_MS = 2_000
export const MAX_BACKOFF_MS = 30_000

export function backoffMs(failureCount: number): number {
	return Math.min(POLL_MS * 2 ** failureCount, MAX_BACKOFF_MS)
}
