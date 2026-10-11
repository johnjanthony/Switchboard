# Session telemetry from the Claude Code mod; Watchtower as a display client (design)

**Date:** 2026-10-10
**Status:** approved by John 2026-10-10 (design and written spec); chunk 1 plan `docs/superpowers/plans/2026-10-10-mod-session-telemetry-chunk1.md` (disk-only, gitignored). Two details were revised at planning after a probe, each marked "revised at planning" below; the sleep rule was revised at execution after the final review (marked "revised at execution"). Chunk 1 landed 2026-10-11 (`dbba6a9..17f047f`, deployed, all eight live checks passed); its as-built deviations are in the chunk 1 plan's Deviations section. Chunk 2 landed 2026-10-11 (`b2fd4ee..7824575`, deployed; the phone release install is pending John); its deviations are in the chunk 2 plan's Deviations section. Chunk 3 landed 2026-10-11 (`43734e9..e1c1a83`, deployed, after the chunk 2 phone release install); its deviations are in the chunk 3 plan's Deviations section.
**Backlog:** T-279 (session telemetry from `session.measure`), with T-282 (inbox poll as a liveness heartbeat) rolled in and T-015's remaining stale-pill gap folded in. Follow-ups filed during the brainstorm: T-284 (approval prompts through the mod), T-285 (cut the Wear app down to notifications).
**Evidence:** Claude Code 2.1.293 mod declarations (`plugin-authoring/types/claude-code.d.ts`, written by the plugin-authoring skill under `%LOCALAPPDATA%\Temp\claude\bundled-skills\`), vault note `Reference/Claude Code Mods`, local transcripts under `~/.claude/projects/C--Work-Switchboard/`, and the code paths cited below.

## Goal

Watchtower reads session metrics from disk and the network on its own: it tails every Claude Code transcript on Windows and in every WSL distro, infers the 1M context window the transcript does not record, reads Windows Terminal tab titles through UI Automation, and polls an undocumented quota endpoint with the OAuth token from `~/.claude/.credentials.json`. The server then depends on that sensor: it suspends lost-marking whenever Watchtower is stale, and never marks a session lost while Watchtower sees its transcript.

The Claude Code mod can now report all of this from inside each session. This work makes the mod the source:

1. **The mod reports session telemetry** (context, rate limits, model, effort, title) to the server, which stores it on the session records and a new account-wide quota record.
2. **The mod's inbox poll becomes the liveness heartbeat**, so a dead interactive session leaves the board in about 90 seconds instead of 15 minutes, and the sweeper stops depending on Watchtower.
3. **Every consumer reads the server's data**: the phone, Operator and Watchtower. Watchtower becomes a display-only client and its readers are deleted.

## Facts the design rests on (read 2026-10-10)

From the 2.1.293 declarations unless stated:

- **`session.measure`** fires "after each main-thread turn, and when a rate-limit window moves a whole point", with `context` (`tokens?`, `window`, `percent?`, a whole number 0-100), `rateLimits` (`kind`, `percentUsed` 0-100, `resetsAt?` ISO), `cost?`, and `changed` naming which units moved. The first measurement names every unit it has. `rateLimits` are "the rate-limit windows the last API response reported", empty off a subscription.
- **The window is read directly.** On a 1M session `session.measure`'s `e.context.window` reads `1000000` while the model id lacks `[1m]` (vault note, verified on 2.1.288).
- **`session.measure` can fire before `session.start`** (about 270 ms earlier; vault note).
- **`turn.step`** carries `model` and `effort` (`low` to `max`, or a number; absent for a model without effort); `agentId` is absent on the main thread.
- **No mod call reads the session title.** A settings hook can set one (`sessionTitle`); nothing in `$.session` returns it.
- **Title rows never pass through `session.append`.** A headless probe on 2.1.296 renamed a session with `/rename`: the transcript gained a `custom-title` row (`{"type":"custom-title","customTitle":...}`), while `session.append` saw only the command's `user` and `system` rows.
- **`command.run` fires for the built-in `/rename`**, with the new name as `args`; by the time its `next(e)` resolves, the `custom-title` row is in the transcript (same probe).
- **Verified live (headless probe, 2.1.296, 2026-10-10):** `session.measure` after the turn carried `context` `{tokens: 65416, window: 1000000, percent: 7}`, `rateLimits` `five_hour` and `seven_day` with `percentUsed` and `resetsAt`, and `cost`; the main-thread `turn.step` carried `model` `claude-sonnet-5-5` and `effort` `low` with no `agentId`; the mod found its transcript by listing `<home>/.claude/projects/` (37 directories) and read it.
- **Title rows are cheap to find.** Claude Code writes the `ai-title` after the first turn and re-appends it every 10-20 lines with the same value (19 rows in a 331-line transcript, 118 in a 2,351-line one). The largest local transcript is 8 MB. Watchtower prefers the last `custom-title` over the last `ai-title` (`TranscriptTitles.cs`).
- **`$.fs.read` returns a whole file and refuses one over 4 MiB** (`refused: the file is over the 4194304-byte limit`, probe 2026-10-10); it has no byte ranges. Four of the six largest local transcripts are over that.
- **The poller runs only in interactive sessions** (`if (e.isInteractive) startPoller($)`, `hooks/switchboard.ts:290`), every 2 s, backing off to a 30 s cap on failures (`hooks/inbox.ts`).
- **The sweep today** (`server/session_registry.py:436-476`, every 60 s from `dispatch.py:746`): marks a session lost after 900 s of silence, only while Watchtower pushed within 120 s (`rings_are_fresh`), and never while the session has a ring. Awaiting-human and awaiting-agent sessions backed by a live structure are exempt. `lost` is final today.
- **`apply_rings`** (`session_registry.py:273`) writes `model`, `effort`, `context_pct`, `name`, `name_source` and `title_state` onto records, bumps `last_event_at`, and creates records for unknown sessions. `sync_session_titles` runs on every Watchtower push for every ringed session (`server/main.py:413`).
- **`blocked_on_approval` is `in_tool and title_state == "star"`** (`session_registry.py:227`), and `title_state` comes only from Watchtower's tab-title reading (`TabTitles.cs`). It also feeds `/stats` `needs_you` with reason `approval` (`main.py:288-291`).
- **Watchtower** scans transcripts every 60 s (`PollIntervalSeconds`), polls `/stats` every 4 s (`Switchboard.PollSeconds`), and sets `is_error` only when it fails to parse a transcript (`SessionAggregator.cs:48,64`). Its ring `pct` is a 0-1 fraction (`SessionModel.Pct`); its `status` is `live` or `idle`.
- **Consumers:** the phone listens to `widget/rings`, `widget/quota` and `widget/pushed_at` (`MainViewModel.kt:432-464`); Operator reads the same paths (`dashboard/schema.js`); the Wear UI reads only `conversationRows`.
- **RTDB rules are one root `.read` / `.write` rule** (`database.rules.json`), so new nodes need no rules change.
- **`/stats` `needs_you` is already keyed by full `cli_session_id`.**
- **The session mirror diffs payloads**, so an unchanged record never re-writes RTDB.

## Decisions (settled with John)

1. **Watchtower becomes a display-only client.** It stops reading transcripts, tab titles and the OAuth endpoint, and renders what the server holds. Rejected: drop transcripts but keep the OAuth quota poll; mod first with the transcript scan as a fallback.
2. **Idle quota shows the last reading and its time.** When a window's `resets_at` passes with no newer reading, the server zeroes that window. Rejected: keep the OAuth poll as a fallback for when no session runs. Accepted cost: usage from claude.ai, the mobile app or other machines stays unseen until the next turn here.
3. **Titles come from the mod reading its own transcript, capped, plus a `/rename` hook** (revised at planning, 2026-10-10, after the probe found the 4 MiB read limit and no title rows in `session.append`). Rejected: Watchtower keeps a title reader; drop Claude Code titles; a full transcript read after every turn (refused past 4 MiB).
4. **The approval marker is dropped.** `title_state` and `blocked_on_approval` go. Mod-based approval support is T-284. Rejected: keep Watchtower's tab-title reader for this one signal.
5. **Liveness: the poll is the fast path, the silence rule the backstop** (section 3). Rejected: poll only.
6. **Consumers move to `sessions/` and a new `quota` node in this work, and `widget/rings`, `widget/quota` and `widget/pushed_at` are retired.** Rejected: the server synthesizing `widget/*` in today's shape.
7. **Transport: a dedicated telemetry route.** Rejected: carrying telemetry on the inbox poll (turns a 2 s read into a write; a bad body could break delivery); extra fields on `/agent_status` (away-mode-gated Firebase write, Antigravity handling).
8. **Cost is left out.** No surface shows it; add it when one does.
9. **`is_error` is dropped.** It only ever meant a transcript parse failure.
10. **Minimal Wear effort.** The shared view model keeps its output types, so Wear needs only a rebuild. The Wear app's future is T-285.
11. **T-015's stale-pill gap folds in.** The pill moves from Watchtower's push stamp to a server heartbeat.
12. **Antigravity drops out of Watchtower** (its rings and quota display), consistent with T-283.
13. **One Claude account across Windows and WSL** is assumed: the quota record is account-wide.
14. **The approval marker is retired in chunk 2** (decided 2026-10-11, after chunk 1 landed; moved from chunk 3). Its "star" tab marker also means idle, so once Watchtower stops pushing, every idle session's frozen "star" would turn its next tool call into a false "needs approval". Chunk 2's first task drops `title_state`, `blocked_on_approval`, `apply_rings` and `needs_you`'s approval reason. Rejected: moving only the `needs_you` reason.
15. **The quota's `received_at` means "last confirmed by a session"** (decided 2026-10-11). A post-turn report carrying context but no rate limits confirms the stored reading, since the windows had not moved a whole point; the server stamps `received_at` from it and publishes at most once a minute. Rejected: the time of the last value change; dropping the "as of" display. Revised by decision 19.
16. **The daily anchor stays, checking the server's quota** (decided 2026-10-11). It skips when `quota.five_hour` shows a future reset with usage on it, and fires with no reading. Rejected: keeping a credential read for the anchor alone.
17. **Chunk 2 planning findings, accepted 2026-10-11:** quota fractions are divided by 100 on the phone and Operator; Watchtower keeps its last view while the server is down (no greyed-out styling exists); the "possibly waiting on approval" hint goes with the marker; Watchtower's empty state reads "no live sessions"; its `/stats` poll runs regardless of `Switchboard.Enabled`, which gates only the stats line, launcher and badge; "as of" times show on the phone's quota dialog, in Operator's quota tooltip and in Watchtower's popup (after 30 minutes); the tray's poll-interval submenu goes.
18. **Chunk 3 scope** (decided 2026-10-11, at chunk 3 planning). Three T-287 items ride along: the quota confirmation fix (decision 19), Watchtower's redraw guard (it re-laid itself out on every 4 s poll) and the dead approval-chip style; `docs/switchboard-design-spec-comprehensive.md` is brought fully up to date, including the 2026-10-03 mod it never caught up with. Closed won't-fix: Watchtower's "0 / 0" rows for sessions that never reported their context (they fade as old-mod sessions relaunch, and hiding them would not match the phone's stale values either) and Operator's "Reading from:" as an age (the tooltip is computed at render, so an age would freeze on an idle page).
19. **Only a fresh rate-limit report confirms the quota** (decided 2026-10-11, revising decision 15 after a probe). The mod sends `rate_limits` on a measurement only when a model request (`turn.step`, any agent) has run since it last sent them, moved or not; the server confirms only a report that carries them. A measurement before a session's first request, or after `/clear`, confirms nothing. The probe (Claude Code 2.1.296) showed `e.rateLimits` populated on every measurement once a response has arrived, with `changed` naming them only when they moved, so a measurement carrying rate limits says nothing about their age. Rejected: sending them on every measurement that carries them.

## Design

### 1. The mod's reports

`POST /sessions/{sid}/telemetry` takes a partial update: a missing field means unchanged, `null` clears. Auth matches the other mod routes (loopback peers exempt, Bearer token from WSL). It always answers 200, the hook-route contract.

```json
{
	"context": {"tokens": 412000, "window": 1000000},
	"rate_limits": [
		{"kind": "five_hour", "percent_used": 23.5, "resets_at": "2026-10-10T18:00:00Z"},
		{"kind": "seven_day", "percent_used": 61, "resets_at": "2026-10-14T09:00:00Z"}
	],
	"model": "claude-opus-5-5",
	"effort": "high",
	"title": {"name": "Watchtower metrics reads", "source": "ai-title"}
}
```

| Field | Source | Sent when |
|---|---|---|
| `context` | `session.measure` `e.context` (`tokens`, `window`) | `e.changed` includes `context` |
| `rate_limits` | `e.rateLimits`, the full list | a model request (`turn.step`, any agent) has run since they were last sent, moved or not (decision 19) |
| `model`, `effort` | main-thread `turn.step` (`agentId` absent) | either differs from the last value posted |
| `title` | the session's own transcript | differs from the last value posted |

- **Effort travels with the model**, as `apply_rings` treats it today: a `turn.step` without `effort` (a model without effort tiers) sends `"effort": null`. A numeric effort is sent as `null`, since the label logic knows only the level names.
- **Titles** (revised at planning). The mod reads its transcript and takes the last `custom-title` row, else the last `ai-title` row, found with a string search rather than a parse of every line (a pure function in a new module, `hooks/telemetry.ts`). It reads at `session.start` and after each main-thread `turn.complete` until it has a title, which Claude Code writes after the first turn while the file is still small, then stops. A read refused for size also stops it for the session; a resumed session's record already holds its name from before. A `command.run` hook on `rename` reads again once the command has run, falling back to the command's `args` when that read is refused. The mod locates its transcript as `<session id>.jsonl` under a directory of `<config dir>/projects/` (`CLAUDE_CONFIG_DIR`, else `HOME` or `USERPROFILE` plus `/.claude`) and keeps the path once found. Reads are fire-and-forget, off the turn's critical path.
- **Sending** is fire-and-forget under the existing 1.5 s bound (`FETCH_TIMEOUT_MS`), with no retry: the next measurement carries fresh figures. Subagent steps and turns are ignored.
- **Last-posted values** live in module variables, like the poller's bookkeeping. A hot reload resets them and the next events re-post once, which is harmless.
- **The heartbeat needs no mod change:** the existing inbox poll is it.

### 2. Server: session records, quota, titles

**The route** validates each field and ignores unknown ones: `context.window` a positive integer, `context.tokens` a non-negative integer, `percent_used` a number from 0 to 100, `resets_at` an ISO timestamp, `model` a non-empty string, `effort` a level name or `null`, `title.source` one of `custom-title` / `ai-title`. A field that fails validation is dropped; the rest of the body still applies. A report for an unknown session creates its record (`source="telemetry"`), as status posts already do, so a `session.measure` arriving before `session.start` is harmless. The route sits behind the route limiter like the other POST routes.

**Session records** gain `context_tokens` and `context_window`, hydrated like the other fields. `context_pct` stays a 0-1 fraction, today's ring scale, computed as `tokens / window`; the mod's whole-number `percent` is not stored. `model`, `effort`, `name` and `name_source` keep their meaning and change writer: once a session reports telemetry, only the mod writes them.

**Titles to conversations.** When a title changes, the route calls the existing `sync_session_titles` for that session. Watchtower's push re-offered every title every minute, so a session that joined a conversation after its title appeared still had it adopted; event-driven titles lose that, so each sweep tick (every 60 s) re-offers every non-terminal record's name, the same cadence as today. `sync_session_titles` writes only on a change, so a re-offer costs nothing when the title already stands. (Revised at planning: the spec first had adoption run at bind time, but `bind_session` is synchronous and called from seven places.)

**Quota.** A new `QuotaStore` (`server/quota_store.py`) holds one reading:

```json
{
	"five_hour": {"percent_used": 23.5, "resets_at": "2026-10-10T18:00:00Z"},
	"seven_day": {"percent_used": 61, "resets_at": "2026-10-14T09:00:00Z"},
	"received_at": "2026-10-10T17:02:11Z"
}
```

- A report replaces each window it carries; a window it does not carry is left as it was. Other kinds (`spend_limit`) are ignored.
- `received_at` is the server's receive time, never the mod's clock, so Windows and WSL clock skew cannot reorder readings. As built in chunk 2 (decision 15), it is when a session last confirmed the reading. Since chunk 3 (decision 19), only a report carrying rate limits confirms it.
- The store mirrors to RTDB `quota` on change.
- On each sweep tick, a window whose `resets_at` has passed gets `percent_used` 0 and `resets_at` cleared, until a newer reading fills it. Every consumer shows the same thing without its own reset logic.
- Before the first reading the node is absent.

### 3. Liveness

- **`note_poll`.** The inbox route records each poll in the registry's memory, keyed by session and stamped with the monotonic clock, outside `SessionRecord`, the way stops are kept. A poll never reaches Firebase. A poll from an unknown session creates its record (`source="poll"`).
- **Grace period.** For 90 s after the server starts (the same value as the poll threshold below), the sweep marks no session lost by either rule, so sessions have time to resume polling after a restart.
- **Polled sessions.** A non-terminal session that has polled since the server started is marked `lost` (`end_reason="presumed-dead"`) after 90 s without a poll. The threshold is configurable as `SWITCHBOARD_SESSION_POLL_LOST_AFTER_SECONDS`; it must stay above the poll's 30 s backoff cap, or a brief server hiccup marks live sessions lost.
- **Never-polled sessions** (`-p` runs, Antigravity, records hydrated after a restart whose session has not polled yet) are judged by the existing silence rule: 900 s since `last_event_at`.
- **Revival.** A poll from a `lost` session puts it back to `idle` and clears `end_reason`. `ended` stays final: it means `session.end` arrived.
- **Sleep and stalls** (revised at execution, 2026-10-11). If a sweep tick arrives more than a quarter interval late (75 s after the last, the machine slept or the loop stalled), the sweep restarts the grace period before judging, instead of marking every session lost on wake and reviving them 2 s later. A poll stamp is at most one tick gap plus one 2 s poll old, so outside grace it stays under the 90 s threshold and no sleep can mark a polling session lost. The first draft's "more than three intervals" (180 s) left sleeps of 90-180 s unguarded; the final review found it.
- **Removed from the sweep:** the `rings_fresh` gate and the ring exemption. The awaiting-human and awaiting-agent exemptions stay.
- **Unchanged:** a `lost` Claude session's conversation member stays as it is today. Propagating lost to member-dormant for Claude sessions is T-249, and would now also need un-dormant on revival.

### 4. Server heartbeat

A small supervised loop writes `presence/server_at` (ISO, server clock) every 60 s and on startup. It replaces `widget/pushed_at` as the phone's and Operator's staleness signal, so the stale pill means the server or the network is down, not Watchtower. The spawn-dialog copy in T-015 stays out of scope.

### 5. Consumers (chunk 2)

**Phone and Wear: the shared view model keeps its output types; only their sources change.**

- `widgetRings` (`StateFlow<Map<String, WidgetRing>>`) is built from the `sessions/` records the view model already listens to, by a new pure function in `shared` (non-terminal records only). It sets `pct` from `context_pct`, `model` from `model`, `status` to `live` for `active` / `awaiting_human` / `awaiting_agent` and `idle` for `idle`, `context_tokens` and `window` from the new fields, and `isError` always false. `conversationRows`, `ConversationPolicy` and every screen that reads rings stay untouched, and the Wear UI, which reads only `conversationRows`, needs no code change.
- `widgetQuota` keeps its type, fed by a new listener on `quota`: `five_hour` to `session`, `seven_day` to `weekly`, `received_at` to `polledAt`, `antigravity` null. `QuotaDetailDialog`'s Antigravity groups drop out without a code change.
- The stale-pill flow is fed from `presence/server_at` and renamed `serverSeenAt`; Page A's pill keeps its 5-minute threshold.
- `blocked_on_approval` leaves `sessionNeedsAttention` (`SessionBoardPolicy`).
- The `widget/rings`, `widget/quota` and `widget/pushed_at` listeners go.

**Operator.**
- `schema.js` swaps the three `widget/*` paths for `quota` and `presence/server_at`; `widget/status` stays.
- `store.js` and `derive.js` take per-session context from session records, and `StatusBar` reads the quota node.
- `demo.js` fixtures follow.

**Watchtower** stays HTTP-only (it never touches Firebase). Its 4 s `/stats` poll gains:

```json
"sessions": {
	"total": 3,
	"by_state": {"active": 1, "idle": 2},
	"items": [
		{"session_id": "808aaef2-82cc-4fd1-8815-f210c65f5693", "name": "Watchtower metrics reads", "cwd": "C:\\Work\\Switchboard", "surface": "windows", "state": "active", "model_label": "Opus 5.5 · high", "effort": "high", "context_pct": 0.41, "context_tokens": 412000, "context_window": 1000000}
	]
},
"quota": {"five_hour": {"percent_used": 23.5, "resets_at": "2026-10-10T18:00:00Z"}, "seven_day": {"percent_used": 61, "resets_at": "2026-10-14T09:00:00Z"}, "received_at": "2026-10-10T17:02:11Z"}
```

- `items` lists every non-terminal session; `quota` is `null` before the first reading. Full ids add no exposure: `needs_you` in the same response already carries them.
- **Which sessions show changes**, from transcripts modified in the last `ActiveWindowMinutes` to sessions the server considers alive: an open idle session stays visible while it polls, and a closed one disappears when `session.end` arrives. Live and idle follow the state mapping above.
- The widget, hover popup, detail panel and tray gauge render from `items` and `quota`. The needs-you retention (`ActiveClassifier.IsRetained`) goes, since the server already lists every live session.
- `LastKnown` keeps caching the last answer. While the server is down the widget keeps its last view and the popup's Switchboard pill reads unavailable (corrected at planning: no greyed-out styling exists).

### 6. Retirement

**Chunk 1, the handover.** The mod becomes the only writer of a record's `model`, `effort`, `context_*`, `name` and `name_source`. `apply_rings` stops writing those for every session, not only sessions that have reported, stops bumping `last_event_at` and stops creating records; `sync_session_titles` stops running from rings. Watchtower's push keeps feeding only `widget/*` (for consumers not yet moved) and `title_state` until it retires. A session still running the old mod keeps polling, so its liveness is fine, but its context and model freeze until it is relaunched.

**Chunk 2, the approval marker** (decision 14): `title_state`, `blocked_on_approval`, `apply_rings` and `needs_you`'s `approval` reason, removed and deployed before Watchtower's deploy.

**Chunk 2, Watchtower's readers** (deleted once Watchtower renders from `/stats`, and deployed last in the chunk, after the server, Operator and phone, so `/widget-snapshot` stops only once nothing depends on it):
- transcript parsing, tailing and titles, and the 1M-window inference (`ModelWindowMap`);
- Windows and WSL session scanning and the distro lister;
- tab-title reading and its UI Automation enumeration;
- every Antigravity reader;
- `QuotaService` and the credential read;
- the snapshot push;
- their config fields (`PollIntervalSeconds`, `ActiveWindowMinutes`, `LiveThresholdSeconds`, `QuotaPollMinutes`, `AntigravityQuotaPollIntervalSeconds`) and their tests.

The plan enumerates the files.

**Chunk 3, the server and Firebase:**
- the `/widget-snapshot` route, `WidgetSnapshotStore` (`server/widget_snapshot.py`) and the backend's `write_widget_rings` / `write_widget_quota` / `write_widget_pushed_at`;
- a one-time delete of the RTDB `widget/rings`, `widget/quota` and `widget/pushed_at` nodes;
- `rings_are_fresh` and `WATCHTOWER_FRESH_SECONDS`;
- the marker-health one-shot, which now fires routinely (T-286).
- riding along (decision 18): T-287's quota confirmation fix, Watchtower's redraw guard, the dead approval-chip style, and the comprehensive design spec brought up to date.

Stays: `widget/status`, the Claude status watch and `/widget-status`. The Antigravity status watch is left for T-283.

## Delivery: three chunks

Each chunk leaves the system working.

1. **Mod and server.** The telemetry route, record fields, quota store and node, the sweep's title re-offer, heartbeat liveness in the sweep, the presence loop, the `/stats` additions, and the chunk-1 handover. Consumers are untouched.
2. **Consumers.** The phone (with Wear rebuilt), Operator, then Watchtower with its readers deleted, in that deploy order.
3. **Server and Firebase cleanup.** Section 6, chunk 3, plus the tracking-doc updates (cut T-279 and T-282 from the backlog; re-scope T-015 to its spawn-dialog copy).

## Error handling

- **Telemetry route:** answers 200 on malformed JSON or a non-object body; drops a field that fails validation and applies the rest; never surfaces a 4xx to the hook.
- **Mod:** a failed telemetry post is logged to debug once per failure streak, with no retry. A failed title read (transcript not found, read error) leaves the last title in place and logs to debug. WSL's existing 401 handling (`noteUnauthorized`) covers a bad token on the new route too.
- **No quota reading yet:** the node is absent and `/stats` `quota` is `null`; consumers show "no reading".
- **Server down:** Watchtower draws its last-known view greyed; the phone's pill goes stale after 5 minutes of no `presence/server_at` update.
- **Presence loop:** runs under the loop supervisor, so a crash shows in `/healthz` like the other dispatch loops.

## Testing

- **Server (pytest):** the route's partial updates, `null` clears, field validation and record creation; `context_pct` as `tokens / window`; the quota store's per-window replace, `received_at` stamping and reset zeroing; the sweep's poll-lost, grace, never-polled silence, revival and sleep-gap rules (driven through `_session_sweep_once` with an injected clock); the sweep's title re-offer; the presence loop; the `/stats` `items` and `quota` keys; the chunk-1 handover (a ring no longer overwrites mod-written fields).
- **Mod:** `claude plugin test .` suites for the `session.measure` and `turn.step` reporting (change detection, subagent steps ignored, effort cleared with a model change) and `hooks/telemetry.ts`'s title extraction; `bash scripts/mod-typecheck.sh`; `claude plugin validate .claude-plugin/plugin.json`. `tests/test_mod_plugin.py` already runs the first two from pytest.
- **Android:** `shared` unit tests for the sessions-to-rings and quota mappings; `:shared:testDebugUnitTest`, `:app:assembleDebug`, `:wear:assembleDebug`, `:app:lintDebug` (the full `./gradlew build` fails on T-262).
- **Operator:** `node --test` for the store and derive changes.
- **Watchtower:** xUnit for parsing the new `/stats` keys.

## Rollout and live verification

- **Deploy order:** server first, then mod (a mod edit reaches the next session opened straight from the working tree), per `CLAUDE.md`. WSL sessions report only after a `git pull --ff-only` in the WSL clone. Chunk 2 deploys Watchtower last.
- **Probe:** run during planning (see the verified facts above); none remains for chunk 1.
- **Live checks, chunk 1:**
  - A 1M session's record shows `context_window` 1000000.
  - The `quota` node matches `GET /api/oauth/usage` once, while that poll still exists.
  - A hard-killed session (process killed, no `session.end`) goes `lost` within about 90 s, and revives if polling resumes.
  - `restart-service.ps1 -SkipTests` marks no live session lost.
  - A WSL session reports through its clone.
  - `smoke.py --preflight-only` passes after each deploy.
- **Live checks, chunk 2:** the phone side on the `Pixel_10_Pro` emulator (context badges, quota header and dialog, stale pill driven by stopping the service); Operator in the browser; Watchtower against the live server, then with the service stopped (greyed last-known view).
- **Live check, chunk 3:** the three `widget/*` nodes are gone from RTDB and nothing logs a write error. A fresh interactive session leaves `quota.received_at` alone until its first model request.

## Out of scope

- Cost display (decision 8).
- Approval prompts through the mod: T-284.
- The Wear app's reduction to notifications: T-285.
- Propagating `lost` to conversation members for Claude sessions: T-249.
- T-015's spawn-dialog "desktop offline" copy.
- Removing the Antigravity status watch and the rest of agy: T-283.
- Renaming the view model's `widget*` outputs.
