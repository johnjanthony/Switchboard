# Switchboard — Rejected Designs and Approaches

Decisions made against specific proposals. Preserved here so the reasoning isn't lost and the same idea isn't re-litigated. **This is not a backlog**: nothing here is awaiting work. Items move here only after explicit consideration and rejection.

Format: one section per rejected proposal. Include the original reasoning verbatim where possible. No frontmatter / IDs — these are decisions, not tracked tickets.

---

## Disconnect detection for unattended agent crash

Investigated. Starlette/uvicorn does detect the dropped TCP/SSE connection, but FastMCP's transport doesn't propagate that to `ServerSession._in_flight[request_id]` or `responder.cancel()` — wiring it requires an upstream mcp-library patch, monkey-patching `ServerSession.__init__` from ASGI middleware, or a custom transport subclass. None are clean. Heartbeat alternative not pursued (per-turn round-trip not justified given the typical kill-and-respawn and cancel-tool-call paths are already covered). The 24h `ask_human` timeout is the backstop. Revisit if the MCP SDK adds a disconnect-propagation hook.

## Gemini CLI cancel notifications

Not actionable server-side. Per snoop-log evidence, Gemini CLI does not send `notifications/cancelled` over MCP when the user cancels a tool call. File an issue with the Gemini CLI repo if it matters; nothing to fix here.

## Webhook instead of long-polling getUpdates

Legacy Telegram concept, no longer applicable.

## Multi-user chat support

Single-developer model is baked into the spec. Don't touch until there's a concrete second user.

## MarkdownV2 — Telegram flavour

Its 18-character escape list (including `.` and `-`) makes unescaped user strings a footgun; one stray period rejects the whole message. Obsolete after Telegram removal.

## Java rewrite (considered 2026-04-20)

No meaningful gain over NSSM for a single-developer tool. Python MCP SDK is the reference implementation; rewrite cost not justified.
