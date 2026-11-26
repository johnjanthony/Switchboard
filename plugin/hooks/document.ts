// Pure: send_document_human from a WSL session. The switchboard server runs as
// LocalSystem on Windows, where WSL distros are per user, so it cannot open a
// WSL path; the mod resolves the path inside the session, holds it to the
// session's cwd, and passes the bytes. The error texts match the server's own
// (server/gateway/document.py).

export const SEND_DOCUMENT_TOOL = 'mcp__switchboard__send_document_human'

// server/gateway/document.py _MAX_DOCUMENT_BYTES: a larger file is refused before it is read.
export const MAX_DOCUMENT_BYTES = 5 * 1024 * 1024

// A multiple of 3, so each range's base64 joins the next without padding, and
// one range's base64 is exactly 4 MiB, within $.fs.read's cap however it counts.
export const READ_RANGE_BYTES = 3 * 1024 * 1024

export function readsInSession(distro: string | undefined, cwd: string): boolean {
	return distro !== undefined && distro !== '' && cwd.startsWith('/')
}

// The absolute path, `.` and `..` folded as the server's resolve folds them,
// without touching the disk.
export function absolutePosix(cwd: string, path: string): string {
	const parts: string[] = []
	for (const part of (path.startsWith('/') ? path : `${cwd}/${path}`).split('/')) {
		if (part === '' || part === '.') continue
		if (part === '..') parts.pop()
		else parts.push(part)
	}
	return `/${parts.join('/')}`
}

export function readRanges(size: number): Array<{ offset: number; length: number }> {
	const ranges: Array<{ offset: number; length: number }> = []
	for (let offset = 0; offset < size; offset += READ_RANGE_BYTES) {
		ranges.push({ offset, length: Math.min(READ_RANGE_BYTES, size - offset) })
	}
	return ranges
}

export function isInside(root: string, path: string): boolean {
	return path === root || path.startsWith(root.endsWith('/') ? root : `${root}/`)
}

export const escapesText = (path: string): string => `ERROR: Path escapes project directory: ${path}`
export const notFoundText = (path: string): string => `ERROR: File not found: ${path}`
export const notFileText = (path: string): string => `ERROR: Not a file: ${path}`
export const tooLargeText = (size: number): string => `ERROR: File too large (${size} bytes, max ${MAX_DOCUMENT_BYTES})`
export const unreadableText = (path: string, error: unknown): string => `ERROR: Could not read ${path}: ${String(error)}`
