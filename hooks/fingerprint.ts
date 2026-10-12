// The mod's content fingerprint, so the server can tell a session running an
// older mod than the repo's. SHA-256 over the top-level hooks/*.ts files in
// name order, each framed as `${name}\n${length}\n` and then its bytes with CRLF
// normalized to LF (length counts the normalized bytes): the framing of
// server/build_info.py's source_fingerprint. Its mod_fingerprint computes the
// same digest over the repo's copy, and both test suites pin one vector.

export type ModFile = { name: string; bytes: Uint8Array }

export function withoutCr(bytes: Uint8Array): Uint8Array {
	const out = new Uint8Array(bytes.length)
	let length = 0
	for (let i = 0; i < bytes.length; i++) {
		const byte = bytes[i]
		if (byte === undefined || (byte === 0x0d && bytes[i + 1] === 0x0a)) continue
		out[length++] = byte
	}
	return out.slice(0, length)
}

// The engine hands file bytes as base64. Uint8Array.fromBase64 exists in the
// mod's runtime but not in every lib the typecheck may use.
export function fromBase64(base64: string): Uint8Array {
	return (Uint8Array as unknown as { fromBase64(text: string): Uint8Array }).fromBase64(base64)
}

export async function modFingerprint(files: readonly ModFile[]): Promise<string | undefined> {
	if (files.length === 0) return undefined
	const encoder = new TextEncoder()
	const parts: Uint8Array[] = []
	for (const file of [...files].sort((a, b) => (a.name < b.name ? -1 : a.name > b.name ? 1 : 0))) {
		const data = withoutCr(file.bytes)
		parts.push(encoder.encode(`${file.name}\n${data.length}\n`), data)
	}
	const joined = new Uint8Array(parts.reduce((total, part) => total + part.length, 0))
	let at = 0
	for (const part of parts) {
		joined.set(part, at)
		at += part.length
	}
	const digest = await crypto.subtle.digest('SHA-256', joined)
	return [...new Uint8Array(digest)].map(byte => byte.toString(16).padStart(2, '0')).join('')
}
