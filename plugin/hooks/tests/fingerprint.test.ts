import { expect, test } from 'claude-code/testing'

import { modFingerprint } from '../fingerprint'

const encode = (text: string): Uint8Array => new TextEncoder().encode(text)

// The same vector pins server/build_info.py's mod_fingerprint (tests/test_build_info.py).
const VECTOR = '32495c7f04ecccc8c3dddc24cf463335502ae7eb098a8cffce64442bb67c06b5'

test('the fingerprint matches the vector the server pins', async () => {
	expect(await modFingerprint([
		{ name: 'b.ts', bytes: encode('three') },
		{ name: 'a.ts', bytes: encode('one\r\ntwo\n') },
	])).toBe(VECTOR)
})

test('a line-ending flip does not change the fingerprint', async () => {
	const lf = await modFingerprint([{ name: 'a.ts', bytes: encode('x\ny\n') }])
	const crlf = await modFingerprint([{ name: 'a.ts', bytes: encode('x\r\ny\r\n') }])
	expect(crlf).toBe(lf)
})

test('content, names and the file set all change it', async () => {
	const base = await modFingerprint([{ name: 'a.ts', bytes: encode('x') }])
	expect(await modFingerprint([{ name: 'a.ts', bytes: encode('y') }])).not.toBe(base)
	expect(await modFingerprint([{ name: 'b.ts', bytes: encode('x') }])).not.toBe(base)
	expect(await modFingerprint([{ name: 'a.ts', bytes: encode('x') }, { name: 'b.ts', bytes: encode('') }])).not.toBe(base)
})

test('no files, no fingerprint', async () => {
	expect(await modFingerprint([])).toBeUndefined()
})
