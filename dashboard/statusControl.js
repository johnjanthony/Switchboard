// Same-origin control + display helpers for the server-owned Claude status watch.
// requestStatus POSTs to the server's /widget-status route (the server fetches
// status.claude.com and publishes widget/status, which the store listener picks up).

export function requestStatus(action) {
	return fetch('/widget-status', {
		method: 'POST',
		headers: { 'Content-Type': 'application/json' },
		body: JSON.stringify({ action }),
	});
}

// Map a Claude status level to a lamp color class (reusing the palette).
export function statusDotClass(level) {
	switch (level) {
		case 'operational': return 'lamp lamp-green';
		case 'minor': return 'lamp lamp-amber';
		case 'major':
		case 'critical': return 'lamp lamp-red';
		default: return 'lamp lamp-cold';
	}
}
