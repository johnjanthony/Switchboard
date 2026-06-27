# Document Preview in Operator — Design

Date: 2026-06-23

## Problem

`send_document_human` uploads a file to Firebase Storage and writes a `type: "document"` message carrying a signed `url` and `filename`. Operator now renders a download pill (the just-added `documentPillHtml`) that links to that signed URL. Clicking it opens the raw file in a new browser tab, so a markdown document shows as raw markdown source, code/text/JSON shows unstyled, and the experience is poor for exactly the documents agents send most. Images and PDFs already display acceptably when opened raw, but the text family does not.

## Goal

Clicking a document pill opens a dedicated Operator preview page (new browser tab) that renders the file properly for every type: markdown formatted, code/text syntax-highlighted, images inline, PDFs embedded, anything else offered as a download.

## Decisions (locked during brainstorming)

1. **Scope:** everything previewable. Markdown and text/code render formatted; images render inline; PDFs embed; all other types fall back to a download affordance.
2. **Placement:** a separate preview tab (a new static page served under `/dashboard`), not an in-app modal or inline transcript expansion.
3. **Fetch path:** a same-origin proxy endpoint on the switchboard server that downloads the blob through the Firebase Admin SDK by its stored blob path. This avoids the cross-origin `fetch()` CORS problem for text rendering and, by re-deriving from the blob path, also fixes the current 7-day signed-URL expiry.

## Why a proxy (the CORS constraint)

To render markdown/text the page must read the response body in JS. The file lives on `storage.googleapis.com`; Operator is served from the switchboard server, so a direct browser `fetch()` is cross-origin and the GCS bucket is not CORS-configured by default. Images and PDFs dodge this because `<img>`/`<iframe>` can load cross-origin without CORS, but text cannot. A same-origin server proxy sidesteps CORS entirely for all types and keeps signed URLs from leaking into query strings.

## Architecture

### Server

1. **Store the blob path.** `_upload_file` returns `(signed_url, blob_name)` where `blob_name` is `documents/<uuid>/<filename>`. The document-message write stores a new `storage_path` field on the message alongside `url` and `filename`. Messages written before this change have no `storage_path`; the proxy falls back to server-side fetching the stored signed `url` so older documents still preview until that URL expires.

2. **New endpoint `GET /document?conv=<id>&msg=<id>[&download=1]`.** Registered alongside `/stats`, under the same localhost-trust model. It:
   - resolves `conversations/<conv>/messages/<msg>` via the backend's database access,
   - reads `storage_path` (or falls back to the stored signed `url`),
   - downloads the bytes with the Admin SDK (`bucket.blob(storage_path).download_as_bytes()`, run in a thread like `_upload_file`),
   - sniffs a content-type from the filename (`mimetypes.guess_type` plus a small override map, e.g. `.md` to `text/markdown`, `.log` to `text/plain`),
   - streams the bytes with `Content-Type` set and `Content-Disposition: inline` (or `attachment` when `download=1`).
   
   Failures are loud: 400 for missing/invalid params, 404 when the message is absent or is not a document, 502 when the download itself fails.

### Dashboard

3. **New static page `dashboard/doc-view.html` + `dashboard/doc-view.js`** (named to avoid colliding with the existing mock `preview.html`). The page needs no Firebase SDK because all data comes from the same-origin `/document` proxy. It reads `conv`, `msg`, and `name` from the query string, classifies the file by the extension of `name`, and renders:
   - markdown: `fetch('/document?...').then(r => r.text())` then `renderMarkdown()` from `markdown.js`
   - code/text: fetch text, then `hljs` from the vendored `highlight.bundle.js` (map extension to language; fall back to an escaped plain `<pre>` when the language is unknown)
   - image: `<img src="/document?...">`
   - pdf: `<iframe src="/document?...">`
   - other: a Download button linking to `/document?...&download=1`
   
   It reuses `styles.css` and `vendor/highlight-theme.css`, with a small header showing the filename and a Download button. A fetch failure shows a clear error state rather than a blank page.

4. **Pill href changes.** `documentPillHtml` stops linking to the raw signed URL and instead links to `/dashboard/doc-view.html?conv=<conv>&msg=<msg>&name=<filename>` (each query value URL-encoded), keeping `target="_blank"` and `rel="noopener noreferrer"`. This requires threading `convId` and `msgId` into the pill builder; the transcript already has both (`id` and `msgId` in `ConversationDetail.js`). A pure `classifyDocument(filename)` helper is added to `document.js` next to the pill builder and is shared by the preview page.

### Data flow

1. Agent sends a document. The server uploads the blob and writes the message with `url`, `filename`, and the new `storage_path`.
2. Operator's transcript renders the pill as `<a href="/dashboard/doc-view.html?conv=C&msg=M&name=F" target="_blank">filename</a>`.
3. Clicking opens the preview tab.
4. The page reads `conv`/`msg`/`name`, classifies by extension, and either fetches text from `/document?conv=C&msg=M` and renders it, or points an `<img>`/`<iframe>` at that same URL, or shows a download button.
5. The `/document` endpoint resolves the message, downloads the blob by `storage_path`, and streams the bytes with the sniffed content-type and inline disposition.

## Error handling

- Proxy: 400 missing/invalid params, 404 message or blob missing, 502 download failure. No silent fallbacks.
- Preview page: a friendly error state on any fetch failure (for example an expired older-document URL). The page never renders blank.

## Security and trust

The `/document` endpoint follows the same localhost-trust model as `/stats`, `/healthz`, and `/away-mode` (unauthenticated). With `SWITCHBOARD_HOST=0.0.0.0` this is reachable on the LAN/WSL subnet exactly like those endpoints and the static dashboard, so it introduces no new exposure beyond what already exists. The endpoint only serves blobs referenced by an existing document message (it must resolve `conv`+`msg` to a real message and its `storage_path`), so it cannot be used to fetch arbitrary storage objects. Authentication can be layered on later if desired; matching the existing endpoints is the chosen baseline.

## Testing

- Server (pytest): `storage_path` is stored on upload (extend `tests/test_firebase_document_upload.py`); `/document` resolves `conv`+`msg`, downloads, and sets the correct content-type and disposition, including the 404 and `download=1` cases (extend `tests/test_gateway_document.py`); the extension-to-mime override map as a pure unit.
- Dashboard (node:test): `classifyDocument()` for each extension family; `documentPillHtml` builds the encoded preview URL with the filename-leaf label and proper escaping. The DOM glue in `doc-view.js` stays thin so the tested logic lives in pure helpers.
- Live: open the preview tab against a real `.md`, an image, a PDF, and a code file and confirm each renders correctly.

## Backward compatibility

Document messages written before this change (including the two test documents already sent) have no `storage_path`. The proxy falls back to server-side fetching the stored signed `url`, so those documents still preview until their signed URL expires (the same 7-day window that already governs them). New documents carry `storage_path` and are immune to expiry.

## Out of scope (YAGNI)

No in-app modal viewer, no inline transcript expansion, no server-side markdown rendering (the JS pipeline is reused), and no authentication beyond the existing localhost-trust model.

## File inventory

- `server/firebase.py`: `_upload_file` returns `(url, blob_name)`; document write stores `storage_path`; a backend method to read a document's bytes + content-type + filename by `conv`/`msg`.
- `server/gateway/document.py`: content-type sniffing helper (extension to mime).
- `server/main.py`: `_build_document_route(...)` and registration of `GET /document`.
- `dashboard/document.js`: pill href now points at the preview page; add `classifyDocument(filename)`.
- `dashboard/components/ConversationDetail.js`: thread `convId`/`msgId` into the pill.
- `dashboard/doc-view.html` and `dashboard/doc-view.js`: the preview page (new).
- Tests: `tests/test_firebase_document_upload.py`, `tests/test_gateway_document.py`, `dashboard/document.test.js`, plus a new dashboard test for `classifyDocument`.
