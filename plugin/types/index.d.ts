// The switchboard mod's $.state contract: every value it keeps for the session,
// declared once so `claude plugin validate` holds the mod to it.
declare module 'claude-code' {
	interface PluginState {
		switchboard: { held: string[]; busy: boolean; turnId: string | null; warned401: boolean }
	}
}
