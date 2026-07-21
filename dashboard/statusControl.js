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
	if (!level) return 'lamp lamp-cold';
	const l = String(level).toLowerCase();
	if (l === 'operational' || l === 'none') return 'lamp lamp-green';
	if (l.includes('major') || l.includes('critical') || l.includes('outage')) return 'lamp lamp-red';
	if (l.includes('minor') || l.includes('degraded') || l.includes('partial')) return 'lamp lamp-amber';
	return 'lamp lamp-cold';
}
