import { expect, test } from 'claude-code/testing'

import { absolutePosix, isInside, READ_RANGE_BYTES, readRanges, readsInSession } from '../document'
import { world } from './harness'

// In a WSL session the switchboard server (LocalSystem on Windows) cannot open
// the session's files, so the mod resolves send_document_human's path inside
// the session, holds it to the cwd, and passes the bytes.

const WSL = { WSL_DISTRO_NAME: 'Ubuntu-22.04' }
const CWD = '/home/janthony/work/rpdm'
const TOOL = 'mcp__switchboard__send_document_human'

test('a WSL session sends a path relative to its cwd with the file read in the session', async ($, on) => {
	const w = world(on, WSL)
	w.cwd = CWD
	w.folders = [CWD]
	w.files[`${CWD}/notes/report.md`] = '# report\n'
	const ran = await $.tool.call({ tool: TOOL, path: 'notes/report.md', sender: 'Claude' })
	expect(ran.deny).toBeUndefined()
	expect(w.toolInputs[0]).toMatchObject({
		path: 'notes/report.md',
		cli_session_id: 'S1',
		cwd: CWD,
		content_base64: btoa('# report\n'),
		content_path: `${CWD}/notes/report.md`,
	})
})

test('a WSL session sends an absolute WSL path inside its cwd', async ($, on) => {
	const w = world(on, WSL)
	w.cwd = CWD
	w.folders = [CWD]
	w.files[`${CWD}/out/run.log`] = 'done\n'
	const ran = await $.tool.call({ tool: TOOL, path: `${CWD}/out/run.log`, sender: 'Claude' })
	expect(ran.deny).toBeUndefined()
	expect(w.toolInputs[0]).toMatchObject({ content_base64: btoa('done\n'), content_path: `${CWD}/out/run.log` })
})

test('a path that climbs out of the cwd is refused before anything is read', async ($, on) => {
	const w = world(on, WSL)
	w.cwd = CWD
	w.folders = [CWD]
	w.files['/home/janthony/work/secret.md'] = 'not for the phone\n'
	const ran = await $.tool.call({ tool: TOOL, path: '../secret.md', sender: 'Claude' })
	expect(ran.deny).toBe('ERROR: Path escapes project directory: ../secret.md')
	expect(w.toolInputs).toHaveLength(0)
	expect(w.reads).toHaveLength(0)
})

test('a link inside the cwd that leads out of it is refused', async ($, on) => {
	const w = world(on, WSL)
	w.cwd = CWD
	w.folders = [CWD]
	w.files['/home/janthony/.ssh/notes.md'] = 'not for the phone\n'
	w.links[`${CWD}/notes.md`] = '/home/janthony/.ssh/notes.md'
	const ran = await $.tool.call({ tool: TOOL, path: 'notes.md', sender: 'Claude' })
	expect(ran.deny).toBe('ERROR: Path escapes project directory: notes.md')
	expect(w.toolInputs).toHaveLength(0)
})

test('a missing file is refused with the server\'s wording', async ($, on) => {
	const w = world(on, WSL)
	w.cwd = CWD
	w.folders = [CWD]
	const ran = await $.tool.call({ tool: TOOL, path: 'gone.md', sender: 'Claude' })
	expect(ran.deny).toBe('ERROR: File not found: gone.md')
	expect(w.toolInputs).toHaveLength(0)
})

test('a folder is refused as not a file', async ($, on) => {
	const w = world(on, WSL)
	w.cwd = CWD
	w.folders = [CWD, `${CWD}/notes`]
	const ran = await $.tool.call({ tool: TOOL, path: 'notes', sender: 'Claude' })
	expect(ran.deny).toBe('ERROR: Not a file: notes')
	expect(w.reads).toHaveLength(0)
})

test('a file over the server\'s 5 MB cap is refused before it is read', async ($, on) => {
	const w = world(on, WSL)
	w.cwd = CWD
	w.folders = [CWD]
	w.files[`${CWD}/big.log`] = 'x'
	w.statSizes[`${CWD}/big.log`] = 5 * 1024 * 1024 + 1
	const ran = await $.tool.call({ tool: TOOL, path: 'big.log', sender: 'Claude' })
	expect(ran.deny).toBe('ERROR: File too large (5242881 bytes, max 5242880)')
	expect(w.reads).toHaveLength(0)
})

test('a file over one read\'s 4 MiB cap is read in ranges and sent whole', async ($, on) => {
	const w = world(on, WSL)
	w.cwd = CWD
	w.folders = [CWD]
	const text = `${'a'.repeat(4 * 1024 * 1024)}0123456789`
	w.files[`${CWD}/big.log`] = text
	const ran = await $.tool.call({ tool: TOOL, path: 'big.log', sender: 'Claude' })
	expect(ran.deny).toBeUndefined()
	// Compared as a flag: a failure would otherwise print 5 MB of base64.
	const sent = String(w.toolInputs[0]?.content_base64 ?? '')
	expect(sent.length).toBe(btoa(text).length)
	expect(sent === btoa(text)).toBe(true)
	expect(w.reads.length).toBeGreaterThan(1)
})

test('a read that fails is refused, never sent on without the session id', async ($, on) => {
	const w = world(on, WSL)
	w.cwd = CWD
	w.folders = [CWD]
	w.files[`${CWD}/locked.md`] = 'x'
	w.readError = 'EACCES: permission denied'
	const ran = await $.tool.call({ tool: TOOL, path: 'locked.md', sender: 'Claude' })
	expect(ran.deny).toContain('ERROR: Could not read locked.md: ')
	expect(ran.deny).toContain('EACCES')
	expect(w.toolInputs).toHaveLength(0)
})

test('a session whose cwd cannot be resolved refuses rather than sending the call bare', async ($, on) => {
	const w = world(on, WSL)
	w.cwd = CWD
	w.files[`${CWD}/report.md`] = 'x'
	const ran = await $.tool.call({ tool: TOOL, path: 'report.md', sender: 'Claude' })
	expect(ran.deny).toContain('ERROR: Could not read report.md: ')
	expect(w.toolInputs).toHaveLength(0)
})

test('a Windows session sends send_document_human to the server as a path, as before', async ($, on) => {
	const w = world(on)
	w.files['C:/Work/X/report.md'] = 'x'
	await $.tool.call({ tool: TOOL, path: 'report.md', sender: 'Claude' })
	expect(w.toolInputs[0]).toMatchObject({ path: 'report.md', cli_session_id: 'S1', cwd: 'C:/Work/X' })
	expect(w.toolInputs[0]).not.toHaveProperty('content_base64')
	expect(w.reads).toHaveLength(0)
})

test('only a WSL distro with a POSIX cwd reads in the session', () => {
	expect(readsInSession('Ubuntu-22.04', CWD)).toBe(true)
	expect(readsInSession(undefined, CWD)).toBe(false)
	expect(readsInSession('', CWD)).toBe(false)
	expect(readsInSession('Ubuntu-22.04', 'C:/Work/X')).toBe(false)
})

test('containment is by whole path segments', () => {
	expect(isInside(CWD, absolutePosix(CWD, 'a/./b/../c.md'))).toBe(true)
	expect(isInside(CWD, absolutePosix(CWD, '.'))).toBe(true)
	expect(isInside(CWD, absolutePosix(CWD, '../rpdm2/x.md'))).toBe(false)
	expect(isInside(CWD, absolutePosix(CWD, '/etc/passwd'))).toBe(false)
	expect(isInside('/', '/etc/passwd')).toBe(true)
})

test('read ranges cover a file exactly, each a multiple of 3 bytes but the last', () => {
	expect(readRanges(0)).toEqual([])
	expect(readRanges(10)).toEqual([{ offset: 0, length: 10 }])
	expect(readRanges(READ_RANGE_BYTES + 1)).toEqual([{ offset: 0, length: READ_RANGE_BYTES }, { offset: READ_RANGE_BYTES, length: 1 }])
	expect(READ_RANGE_BYTES % 3).toBe(0)
})
