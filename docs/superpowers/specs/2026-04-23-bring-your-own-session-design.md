# Bring Your Own Session — Design Spec

**Date:** 2026-04-23
**Status:** Approved, ready for implementation

---

## Problem

Collab sessions today are exclusively created by Switchboard's `/spawn` command. Agents not spawned by Switchboard — already-running Claude Code sessions, manually launched terminals — have no way to join a collab channel. A developer who wants two existing sessions to collaborate must tear them down and re-spawn.

---

## Goal

Allow agents that were not spawned by Switchboard to join a collab channel by agreeing on a `channel_id` (provided by John) and calling `message_and_await_agent` as normal. No new MCP tool. No pre-registration step.

BYO sessions do **not** imply away mode. Away mode and collab mode are independent concepts — see SKILL.md changes below.

---

## Design

### Session creation — implicit on first call

When `message_and_await_agent` is called with a `channel_id` that is not in the registry, the gateway creates a `CollabSession` on the spot. Three side effects are fired as background tasks:

1. `backend.write_session_meta(channel_id, "collab", channel_id, agent_senders=[], task="")` — Android tab appears immediately.
2. Sidecar append (`collab-sessions.json`) — ensures a "session lost" notification is sent to Firebase on the next gateway restart.
3. `backend.start_inject_listener(channel_id)` — Android compose box wired up, human can inject messages.

If any side effect fails, it is logged and the handler continues — the agents can still exchange messages through the gateway.

### Sender enrollment — dynamic, locked at two

`CollabSession.agent_senders` changes from `tuple[str, str]` (fixed at spawn time) to `list[str]` (fills dynamically, max 2). A new `enroll(sender) -> str | None` method handles registration, returning `None` on success and an error string otherwise:

- Same sender, list is full (both slots occupied) → `None` (idempotent — same agent calling again after reconnect).
- Same sender, list is not full → `"duplicate"` error string → handler returns `"ERROR: sender '{name}' is already enrolled — use a unique sender name"`. Prevents two agents with the same default name (e.g. both "Claude") from silently corrupting the session.
- New sender, list not full → appended, `None`.
- New sender, list already has 2 entries → `"full"` error string → handler returns `"ERROR: session is full"`.

The idempotency check (`same sender + list full`) only applies when both slots are occupied — it covers the spawned-session path where both names are pre-registered. The collision check (`same sender + list not full`) applies exclusively to BYO sessions where a second agent tries to enroll with a name already in use.

Spawned sessions are unaffected: both sender names are passed at `CollabSession` construction before either agent calls in, so `enroll()` finds the sender already present in a full list and returns `None` immediately.

### Call ordering — pre-enrollment buffer

BYO sessions have no strict call-order requirement. Either agent may call first, with or without a message. The gateway uses a `_pre_enroll_msg` buffer on `CollabSession` to handle the case where the first agent arrives with a message before the second has enrolled:

- **First agent arrives with a message** — message is stored in `_pre_enroll_msg` and the agent blocks. When the second agent enrolls, the buffer is drained into their `_pending` queue so they receive it immediately when `start_waiting` is called. The relay to Firebase fires at drain time, not buffer time.
- **First agent arrives with no message** — blocks normally. Second agent arrives with a message and delivers it via the existing `deliver()` path. Unchanged from spawned-session behaviour.
- **Both arrive with messages** — first buffers, second receives the buffer and delivers their own message to the first. Both get each other's opening message. This is semantically the "both talking at once" case — prevented in practice by John framing tasks so one agent waits for the other, not by the gateway.

The pre-enrollment buffer is only reachable on BYO sessions (spawned sessions pre-populate both slots before either agent calls in, so the `len == 1` state never occurs for them).

### No explicit session teardown

Sessions linger in the registry until gateway restart. Both agents time out individually via `__TIMEOUT__` when inactive. The `CollabSession` object itself is not actively garbage-collected — this is a known limitation shared with spawned sessions and is acceptable at the single-developer scale of this tool.

---

## Changes by file

### `server/collab.py`

- `agent_senders: list[str]` (was `tuple[str, str]`). Default `field(default_factory=list)`.
- `is_byo: bool = False` — set `True` on BYO sessions; used for logging and sidecar writes.
- `_pre_enroll_msg: str | None = None` — holds a message from the first enrollee until the second arrives. BYO-only; always `None` for spawned sessions.
- New `enroll(sender: str) -> str | None` method — returns `None` on success, `"duplicate"` on same-name collision, `"full"` when both slots are occupied by different senders.
- `other_sender()`, `deliver()`, `start_waiting()`, `cancel_waiting()`, `deliver_inject()` — unchanged.

### `server/gateway.py`

`message_and_await_agent` gains a BYO creation branch at the top, replacing the current early-return guard:

```python
session = registry.get_session(channel_id)
if session is None:
    session = CollabSession(session_id=channel_id, agent_senders=[], task="", is_byo=True)
    registry.add_session(session)
    asyncio.create_task(backend.write_session_meta(channel_id, "collab", channel_id, agent_senders=[], task=""))
    asyncio.create_task(_write_byo_sidecar(config, channel_id))
    asyncio.create_task(backend.start_inject_listener(channel_id))

err = session.enroll(sender)
if err == "duplicate":
    return f"ERROR: sender '{sender}' is already enrolled — use a unique sender name"
if err == "full":
    return "ERROR: session is full"

if message is not None:
    if len(session.agent_senders) == 2:
        # Both enrolled — check for buffered pre-enroll message to deliver to us first
        if session._pre_enroll_msg is not None:
            pre_msg = session._pre_enroll_msg
            session._pre_enroll_msg = None
            session.deliver(sender, pre_msg)
            asyncio.create_task(_relay(channel_id, session.other_sender(sender), pre_msg))
        # Deliver our message to the other agent
        other = session.other_sender(sender)
        session.deliver(other, message)
        # log, relay, transcript as normal...
    else:
        # First enrollee — buffer message until second agent arrives
        session._pre_enroll_msg = message
else:
    if len(session.agent_senders) == 2 and session._pre_enroll_msg is not None:
        # Second enrollee arrived with no message — drain buffer into our pending queue
        pre_msg = session._pre_enroll_msg
        session._pre_enroll_msg = None
        session.deliver(sender, pre_msg)
        asyncio.create_task(_relay(channel_id, session.other_sender(sender), pre_msg))

future = session.start_waiting(sender)
# remainder of handler unchanged — await, timeout, CancelledError handling
```

`_write_byo_sidecar` is a small private async function that reads `collab-sessions.json`, appends a BYO entry, and writes it back via `asyncio.to_thread`. `_relay` is the existing inline fire-and-forget that writes to `backend.write_channel_message`.

### `server/spawn.py`

`CollabSession(agent_senders=tuple(agent_senders), ...)` → `CollabSession(agent_senders=list(agent_senders), ...)`. No other changes.

### `skill/SKILL.md`

The entire **Collab sessions** section is rewritten. The existing section conflates away mode and collab mode — they are independent and must be documented separately.

```markdown
## Collab sessions

### `message_and_await_agent(channel_id, sender, message?)`

Sends `message` to your partner (if provided), then blocks until your partner
replies or a human injects a message.

- **`channel_id`** — from your spawn prompt, or provided by John for BYO sessions
- **`sender`** — your own sender name, unique within the session
- **`message`** — optional outbound text; omit on your first call if you are the listener

**If `message_and_await_agent` returns `"__TIMEOUT__"`:** if in away mode, call
`ask_human` to check in with John. If not in away mode, report in the terminal.
**If `message_and_await_agent` returns an error string (starts with `"ERROR:"`):**
handle the same way as a timeout.

### Away mode and collab mode are independent

**Away mode** means John is not at the desk. ALL output must go through
`notify_human`, `ask_human`, or `send_document_human` — no terminal text. Away
mode is active when:
- You were spawned by Switchboard (single-agent or collab)
- John explicitly says he is stepping away

**Collab mode** means you are paired with a second agent via
`message_and_await_agent`. Collab mode does NOT imply away mode.

When both modes are active: `message_and_await_agent` for peer communication,
`xxx_human` tools for all human communication — no terminal output. If John steps
away during an active BYO collab session, continue using the same `channel_id`
for everything — the session is already registered and the Android tab already
exists. The only change is that human communication moves from the terminal to
`ask_human` / `notify_human` with that same `channel_id`.

When collab mode only (BYO, John has not stepped away): `message_and_await_agent`
for peer communication; once consensus is reached or you are blocked, report back
to John in the terminal normally.

### Collab protocol

1. If John told you to wait for your partner, call `message_and_await_agent(
   channel_id=..., sender=...)` with no message. Block until your partner speaks.
2. If John gave you work to do first, complete it then call
   `message_and_await_agent(channel_id=..., sender=..., message="...")`.
3. Exchange continues — each agent replies by calling `message_and_await_agent`
   with their response as `message`. If both agents began with a message, the
   first response each receives will be their partner's independent opening
   position rather than a reply to theirs — this is normal, treat it as their
   opening position and respond to it.
4. When consensus is reached:
   - **Away mode active:** call `ask_human` to confirm with John.
   - **Away mode not active:** respond in the terminal.
5. If debate becomes unproductive:
   - **Away mode active:** call `ask_human` to report the deadlock.
   - **Away mode not active:** report in the terminal.

### Bring your own session

John can initiate a collab session between two already-running agents by providing
both with a shared `channel_id` and distinct sender names. Both agents must use
unique sender names — John will provide yours.

Call `message_and_await_agent` as described in the collab protocol above. No
special setup is required — the first agent to call creates the session. Call
ordering does not matter; the gateway handles timing transparently. A third
distinct sender gets `"ERROR: session is full"`.

BYO sessions do not imply away mode. Unless John has also said he is stepping
away, report back to him in the terminal once the collab exchange concludes.
```

The `channel_id` guidance section gains one line: *"For bring-your-own sessions, John will provide the channel_id directly."*

---

## Tests

New cases in `tests/test_collab.py`:

- `enroll()` success — new sender added, returns `None`.
- `enroll()` idempotent — same sender when list is full returns `None`, list unchanged.
- `enroll()` duplicate collision — same sender when list is not full returns `"duplicate"`.
- `enroll()` full — third distinct sender returns `"full"`.
- `other_sender()` works after dynamic enrollment of both senders.
- BYO session created in registry on first `message_and_await_agent` call with unknown `channel_id`.
- Listener-first: first caller with no message blocks; second caller with message delivers correctly.
- Initiator-first: first caller with message buffers it; second caller with no message receives buffer immediately via `start_waiting`.
- Both-with-messages: first buffers, second receives buffer and delivers own message to first.
- Third distinct sender returns `"ERROR: session is full"`.
- Firebase meta, sidecar write, and inject listener fired on BYO creation (mocked/captured via `asyncio.create_task` inspection or side-effect assertions).

Existing spawned-session tests remain green — `enroll()` on a pre-populated list is idempotent.

---

## Out of scope

- Active garbage collection of lingering `CollabSession` objects — tracked separately in the feature backlog.
- N-agent sessions — the 2-agent contract is unchanged.
- Multi-user auth — the bot token remains the auth boundary; open registration is acceptable for a single-developer local gateway.
