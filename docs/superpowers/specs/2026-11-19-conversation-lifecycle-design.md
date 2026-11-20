# Conversation lifecycle: lost sessions go dormant, returning ones rejoin, dead conversations end (design)

**Date:** 2026-11-19
**Status:** draft for John's review. Decisions 1 and 2 below are his (2026-11-19). Everything marked "my call" is the design session's and open to correction; the calls are listed together near the end. A fresh-context builder check read the first draft and its findings are folded in.
**Backlog:** T-298 (ending a conversation mints a "(home)" conversation for each dead member) and T-249 (a lost session's member stays alive), plus the idle-end rule John added on 2026-11-19.
**Evidence:** a read-only census of RTDB `conversations` and `sessions` taken 2026-11-19 against the service at `708ce07`, and the code at that commit.

## Goal

A conversation's member list should say which sessions are really there, and a conversation nobody is in should end by itself. Today neither holds:

- A session that dies without a session end is marked `lost` on the roster, but its conversation member stays alive and bound for ever.
- Ending a conversation that holds such a member mints an empty "(home)" conversation for it, and ending that one mints another.
- Nothing ever ends a conversation whose members are all gone, so Active conversations pile up until John ends them by hand, and the service hydrates every one of them at each restart.

This work makes three changes:

1. **A lost session's member goes dormant**, the same way an ended session's does.
2. **A session that was only out of reach rejoins** its conversation when it shows life again, in place of minting a new one.
3. **A conversation with no live member ends by itself** after 14 idle days, and is deleted 72 hours after that.

## What the census shows (2026-11-19)

All 47 Active conversations are dead. None has a member whose session is live; the six live sessions are all unbound, at the desk.

| Shape | Count | Notes |
|:--|--:|:--|
| Every member dormant | 30 | Sessions ended properly; nothing ends the conversation. 4 are hidden. |
| A member marked alive whose session has no roster record | 17 | 16 are the hidden "(home)" conversations minted on 2026-10-13 (T-298). |

Measured with Section 5's rule as written (idle from the later of the last activity and the last member dormancy), 36 of the 47 have been idle for more than 14 days, the 16 "(home)" ones among them. The other 11 have been idle between 2.8 and 13.1 days. The count grew from 43 to 47 between 2026-11-11 and 2026-11-19 (`hydration_complete` lines), after John ended 125 by hand on 2026-10-13. Hydrating 47 took about 6 s of the 2026-11-19 restart; hydrating 116 took about 13 s on 2026-10-13, the day an MCP client failed to reconnect (T-032).

So T-298 and T-249 as filed would repair the 17 and leave the 30. The third change is what stops the pile.

## Decisions (John, 2026-11-19)

1. **A conversation nobody is in is ended automatically** after N idle days with no live member and no open question, hidden and visible alike, with N = 14. Auto-hide and "no rule" were rejected. This reverses T-163's earlier choice to leave hidden-but-Active conversations alone.
2. **A member goes dormant when its session is judged lost**, for both kinds of lost (by poll and by silence), and any later sign of life brings it back and rebinds it. Limiting this to poll-judged sessions, and leaving members alone while the fallback code consults the roster, were rejected.

## Facts the design rests on (verified 2026-11-19 at `708ce07`)

- **Only a session end makes a member dormant.** `handle_session_end` (`server/cli_session_end.py`) is the one place that sets `member.alive = False` for a session that went away. It also unbinds the session, appends a system message, wakes every waiter on the conversation with its unseen delta, and settles the session's pendings.
- **The sweep marks a session lost and touches nothing else.** `SessionRegistry.sweep` sets `state = "lost"`; `_session_sweep_once` (`server/gateway/dispatch.py`) then parks the live blocking ask of a lost session that had polled. The member and the binding are not touched. A polled session is judged by its poll stamp alone, so a status post can revive its record and the next tick mark it lost again.
- **A roster record is pruned 72 hours after it turns terminal** (`SWITCHBOARD_SESSION_RETENTION_HOURS`). That is how 17 members came to be alive with no record at all.
- **Hydration binds every alive member** (step 4 of `hydrate_from_firebase`), so a dead session's binding is rebuilt at each restart. The roster is hydrated before that (step 2c), terminal records included.
- **`apply_fallback` judges a session dormant only by its missing binding** (`server/session_fallback.py`). A lost session is still bound, so with away mode on `handle_force_end` mints a "(home)" conversation for it, with a member, a binding and a home pointer.
- **Two invariants hold elsewhere and must keep holding:** dormant means unbound, and bound means alive or launching. Phone Resume (`handle_resume`) and combine flip a dormant member alive and bind it before the launched session has started, while its roster record is still terminal. Board resume (`handle_resume_session`) adds and binds a member only when the session is not already one in the target; a standalone board resume binds nothing.
- **A returning dormant session does not rejoin.** `_resolve_conversation_and_member` (`server/conversation_ops.py`) sees an unbound session and mints a new conversation; its dormant member stays behind in the old one.
- **Signs of life arrive through six `SessionRegistry` calls:** `note_poll` (the inbox poll; revives a lost record, creates a missing one, ignores an ended one), `upsert_from_hook` (`/agent_status`; revives a lost record, ignores an ended one), `record_session_start` (`/session_start`; the only thing that revives an ended record), `touch_mcp` (any switchboard tool call; never changes state), `apply_telemetry` and `set_awaiting_approval` (both can create a missing record).
- **A human message skips dormant members.** `deliver_human_message` (`server/inbound.py`) writes the message to the conversation and queues a session notice only for alive members. Notices live in `pending_notices` on the roster record, which is mirrored to RTDB, so a queued notice survives a restart; `queue_notice` accepts a record in any state. Human messages are appended to the in-memory log but are not reloaded by hydration (`_LIVE_MESSAGE_TYPES`), so anything that needs them after a restart must not read memory.
- **No path that ends a conversation persists when it ended.** `set_conversation_state` writes `meta/state` only, at all five end sites (`handle_force_end`, `_migrate_member`, `_perform_combine`, `leave_conversation`'s end, and `handle_resume`'s source end), and `ended_at` is set in memory at most. `_conversation_sweep_once` therefore measures the 72 hours from `meta.last_activity_at`. A conversation ended after more than 72 idle hours is deleted at the next hourly tick, not 72 hours after it ended, which is what CLAUDE.md and the comprehensive spec say happens.
- **The phone already stops offering a conversation's Resume about 72 hours after its sessions end.** `conversationResumable` (`SessionBoardPolicy.kt`) needs a member whose session still has a terminal roster record. So ending a conversation at 14 idle days takes no Resume away that the phone still offered.
- **Clients tolerate the new state.** Operator's `memberState` reads `alive` and `session_lost_permanently` only; no client reads `session_end_reason`.
- **False lost verdicts on working sessions are rare.** Since the park-on-lost rule shipped on 2026-10-12, `pending_parked_session_lost` has been logged twice, one of them the smoke run.

## Goals, non-goals, anti-goals

**Goals.** The three changes above; the 17 broken conversations repaired without hand work; no new "(home)" conversation for a session the roster does not consider live; an ended conversation kept for 72 hours from its end, as documented.

**Non-goals.**

- Changing how the sweep judges a session lost (thresholds, grace, the poll and silence rules).
- Rejoining on a new life of the same session id: a `claude --resume` typed at the desk, or a standalone board resume. Those keep today's behaviour. See "Left for later".
- Pruning messages inside a long-lived Active conversation (T-007).
- Faster hydration or listening before hydration completes (T-032). This work only shrinks what there is to hydrate.
- The phantom-resume gate (T-260), the session-end lock race (T-142), and board resume into a conversation the session is already a dormant member of.
- Any client change. The phone and Operator already draw a dormant member and an Ended conversation.

**Anti-goals.** These would make the work a failure even if the rules above were implemented:

- A session that is alive ends up cut off from its conversation for good because the sweep judged it lost during a blip.
- A working session's member flaps between dormant and alive, filling the conversation with system messages and waking its peers each time.
- A conversation with a live member, an open question (blocking, background or parked) or a waiting agent is ended by the idle rule.
- The first run pings the phone once per ended conversation.
- A message John sent while a member was wrongly dormant is never shown to that agent, with or without a restart in between.
- A conversation ended by the idle rule disappears within the hour.

## Approaches

**For decision 2, where the lost verdict reaches the member.** Three were considered. (a) Propagate at the sweep and revive on any sign of life (chosen): one rule, the member list matches the roster, and a wrong verdict heals itself, at the cost of a new revive path. (b) Propagate only for poll-judged sessions: never wrong about a live session that does not poll, but dead sessions carried across a restart stay alive, so the fallback code still needs its own roster check. (c) Leave members alone and have `apply_fallback` consult the roster: the narrowest change, but every other reader of `alive` (the peer count in `message_and_await_agent`, the join candidate rule, human-message delivery, combine) keeps trusting a dead member.

**For decision 1, what to do with a dead conversation.** (a) End it after 14 idle days (chosen): self-cleaning, bounded restart time. (b) Hide it: keeps history, but RTDB and hydration keep growing. (c) No rule.

**For when the sweep makes a member dormant** (my call). The simple rule, "a bound alive member whose roster record is lost becomes dormant", is wrong twice. It fires on a member that Resume has just flipped alive and bound, whose record is still terminal until its session starts a few seconds later. And it flaps: a session whose poller has stopped but whose hooks still post is revived by each status post and marked lost again at the next tick, about once a minute. The chosen rule waits until the session is silent in every channel, and gives a launching session the 15 minutes the roster already gives one that has never shown life.

**For what a returning session rejoins on** (my call). Rejoining on any return, a new life included, would also stop a hand-resumed session from minting a new conversation each time, which is one source of the pile. It was set aside for this work because it collides with choices John makes elsewhere: a standalone board resume would be pulled back into the session's old conversation, and a board resume into conversation X could be revived into Y. The chosen rule rejoins only the same life coming back, which is what decision 2 is about.

## Section 1: a lost session's member goes dormant

**A stamp for signs of life.** `SessionRegistry` keeps, in memory, the time of each session's latest sign of life of any kind: every one of the six calls listed in the facts updates it. It need not persist. The registry likewise needs the time a session was bound; that also stays in memory.

**The rule.** On each session sweep tick outside the grace period, after the lost-marking and the park pass, a bound, alive member is made dormant when the roster has given up on its session and the session is silent:

- **It has shown life since it was bound** (it lived in this conversation and then went quiet): its record is `lost`, and its latest sign of life is older than `SWITCHBOARD_SESSION_POLL_LOST_AFTER_SECONDS` (90 s).
- **It has shown no life since it was bound** (a launch that never came up, or a member carried across a restart): its record is `lost`, `ended` or missing, and it has been bound for longer than `SWITCHBOARD_SESSION_LOST_AFTER_SECONDS` (900 s).

A record that is not terminal is never grounds for dormancy; the sweep's verdict comes first. Because the rule reads state, not a transition, a member skipped on one tick is looked at again on the next.

**What dormancy does.** The member half of `handle_session_end` becomes one routine both callers use: unbind the session, set `alive = False`, stamp `session_ended_at` and `session_end_reason`, append and persist a system message, persist the member, and wake the conversation's waiters with their unseen delta (the REV-111 behaviour, unchanged). For a lost dormancy the reason is `lost` and the message reads `<sender>'s session was lost (no heartbeat); member is now dormant.` `handle_session_end` keeps its own steps around the routine: marking the roster record ended first, and settling the session's pendings afterwards.

**The lost session's own wait (my call).** If the lost session is itself blocked in `message_and_await_agent`, its own wait-queue entry is left in place and is not woken with the news of its own dormancy. A session that is really gone drops its call and the entry is removed by the cancel; one that was only out of reach keeps waiting as if nothing had happened. The other waiters are woken.

**Pendings on a lost dormancy.** None is terminated. A parked or background ask survives, as on any resumable session end. A live blocking ask is the park pass's business, which runs first and again on every tick.

**A lost member is resumable.** `session_lost_permanently` stays false, so phone Resume and combine treat it like any dormant member.

**It still gets John's messages.** `deliver_human_message` queues the session notice for a member that is dormant with reason `lost`, exactly as it does for an alive member that has neither a live ask nor a live wait. The notice sits on the roster record, survives a restart, and is delivered by the session's next inbox poll if it ever comes back. This keeps today's behaviour for a session that was only out of reach, and needs no replay.

**Logging.** One `info` line per dormancy: `member_dormant_lost: session=<sid> conversation=<id> sender=<sender> via=<sweep|launch_timeout|hydration>`.

## Section 2: the same life coming back rejoins

**The rule.** When a session shows life, is not bound, and is a dormant member with reason `lost` in an Active conversation, the member is revived there: `alive = True`, the dormancy fields cleared, the session bound, the member persisted, and a system message appended and persisted: `<sender>'s session is back; member is alive again.` A bound session is never touched. The member update runs under the conversation's lock. Nothing is sent to the phone, and nothing extra is sent to the session: the notices Section 1 queued arrive through its inbox poll.

**Finding the member cheaply.** The registry keeps an in-memory set of the session ids that hold a lost-dormant membership, with the conversation each is in. Section 1 and Section 3 add to it; a revive, a launch path that flips the member alive (phone Resume, combine), a `/session_start`, the member's removal and the conversation's end take from it. A session can hold at most one such membership at a time, because going lost-dormant requires being bound, and a session is bound to one conversation. Every sign of life checks the set, which costs one lookup on the 2 s poll.

**Where it is checked.** In each of the six sign-of-life calls' routes, and in every switchboard tool's entry before the handler runs. The tool check matters because the mod's status post precedes each tool call but is fire-and-forget, so the tool call can arrive first; without the check an `ask_human` would mint a new conversation. It runs for every tool, `join_conversation` included: the revive restores what was true before the verdict, and the tool then does what it would have done had the session never been judged lost.

**A new life closes the window.** A `/session_start` for a session in the set means a new process on that id, not the old one returning. The session is taken out of the set and its member's reason is rewritten from `lost` to `restarted` and persisted, after which it is an ordinary dormant member: resumable from the phone, never revived on its own, and not sent notices. What that session rejoins is decided by whoever started it, as today.

**Logging.** `member_revived: session=<sid> conversation=<id> sender=<sender> trigger=<poll|status|telemetry|approval|tool_call>`.

## Section 3: hydration repairs what a restart finds

After the roster and the conversations are hydrated and before bindings are derived, every alive member of an Active conversation whose session's roster record is terminal or missing is hydrated dormant with reason `lost`, is not bound, is entered in Section 2's set, and has its member record written back to Firebase. No system message is written and no waiter exists to wake. `session_ended_at` is taken from the roster record's `last_event_at` when there is a record and left unset when there is none, so a repaired member never looks more recently active than it was (Section 5 reads that stamp). The set is also rebuilt from members already stored with reason `lost`. One `info` line reports the repair count.

This can be wrong about a session that is alive: one whose record was stored `lost` during a blip, or a resume launched just before the restart. The first is revived by its next poll, status post or tool call. The second arrives as a `/session_start` and falls under "a new life closes the window"; its member stays dormant and its first tool call mints a conversation, which is today's behaviour for a restart that lands in the middle of a resume.

At the first restart after deploy this repairs the 17 members found by the census.

## Section 4: ending a conversation never mints for a dead session

With Sections 1 and 3 a lost session is unbound, and `apply_fallback`'s existing dormant branch does the right thing. Two windows remain where a bound member's session is not live: inside the 900 s launch window, and between a restart and the first sweep tick. So `apply_fallback` also treats a session as dormant when the roster records it as `lost` or `ended`, or has no record of it: it unbinds the session if it is still bound, clears a home pointer that points at a missing or Ended conversation, and mints nothing.

This also covers a session bound in the fresh-spawn window with no member and no record yet. It is left unbound, and its first tool call mints its own conversation through the normal path, properly titled, where today it gets a "(home)".

If the roster is wrong and the session is alive, the cost is the same: it is unbound, and its next ask mints a conversation.

## Section 5: a conversation nobody is in ends by itself

**The rule.** On each conversation sweep tick (hourly), an Active conversation is ended when all of these hold:

- no member is alive (a conversation with no members at all counts);
- it has no pending record of any kind: blocking, background or parked;
- its wait queue is empty;
- it has been idle for more than `SWITCHBOARD_CONVERSATION_IDLE_END_DAYS` days (default 14; 0 turns the rule off), where idle is measured from the later of `last_activity_at` and the latest `session_ended_at` among its members. A stamp that is unset or unparseable is ignored; a conversation with neither is measured from `created_at`.

Measuring from the last dormancy matters: a session that sat silent in a conversation for three weeks and then blipped must not have its conversation ended an hour later, before its next poll can revive it.

**How it ends.** Through `handle_force_end`'s path, with its system message replaced by `Conversation ended after <N> days with no live session.` Every member is dormant, so the fallback step only clears home pointers. Hidden conversations are treated the same as visible ones.

**Kept for 72 hours from the end (my call: fix it for every end).** Every path that ends a conversation persists `meta.ended_at`, written with the state change and not left to a background task that the sweep can overtake. `_conversation_sweep_once` already prefers `meta.ended_at`, and hydration must carry it. Within one sweep tick the deletion pass runs before the idle-end pass, so a conversation is never ended and deleted in the same tick. This also changes conversations John ends by hand: they are kept 72 hours from the end, as the docs have always said, where today one that was idle for three days is deleted within the hour.

**No phone noise.** Ending a conversation this way must send no FCM notification and no admin notice. The builder confirms what `handle_force_end`'s writes push today and suppresses it for this path if they do.

**Logging.** `conversation_idle_ended: <id> idle_days=<n>` per conversation.

**First run.** At the first conversation sweep tick after deploy, the conversations idle for more than 14 days end together: 36 by the census. Their histories are deleted 72 hours later, and nothing restores an Ended conversation. The other 11 end as they cross 14 days. The deploy steps have John confirm the list before the service restarts onto the new code.

## Section 6: ride-along (my call; include or leave out)

`handle_session_end` logs `session_end_no_binding` as a `surface_error`. It is the normal case for a session that never used the switchboard, and with Section 1 it also fires when a lost-dormant session later ends properly. It accounts for 2,987 of the 8,156 error lines in `logs/switchboard.jsonl`. Log it at `info`. The other two early returns in that function (`session_end_conv_missing`, `session_end_no_member`) stay errors.

## Calls the design session made

For John to check while reading. Each is described where it applies.

1. The same life coming back rejoins; a new life (`/session_start`) does not (Section 2, Approaches). The wider form is filed under "Left for later".
2. Dormancy waits for silence in every channel, and a launching session gets 900 s (Section 1).
3. A lost-dormant member still gets session notices for John's messages (Section 1).
4. A lost session's own wait entry is left in place (Section 1).
5. `meta.ended_at` is persisted for every end, which gives hand-ended conversations their documented 72 hours (Section 5).
6. `session_end_no_binding` drops to `info` (Section 6).

## What is left to the builder

- Where the shared dormancy routine and the revive routine live, and their signatures.
- How the sign-of-life stamp, the bound-at stamp and the lost-dormant set are held, and how the routes and the tool entry reach the revive check.
- How the idle rule reaches `handle_force_end` (a parameter for the message and for push suppression, or a small shared helper), and how `meta.ended_at` is written at the five end sites.
- The order of work and the commit split.

## Testing

Unit tests, each written to fail first:

- Dormancy rule: a member that lived, whose record is lost and which is silent past 90 s, goes dormant and unbound with reason `lost`, the system message written and the other waiters woken with their delta; the same member with a status post, a tool call or a telemetry report inside 90 s is left alone, tick after tick; its own wait entry stays queued; a parked or background ask survives; a live blocking ask of a polled session is parked, not cancelled; a non-terminal record is never grounds.
- Launch window: a bound alive member with a terminal or missing record and no life since binding is left alone before 900 s and made dormant after; one whose session started in time is never touched; nothing happens during the grace period.
- Notices: a human message to a conversation with a lost-dormant member queues a notice for it; one with an ordinarily dormant member does not; the notice survives a roster round trip through hydration.
- Revive: each trigger; a bound session is not touched; a member dormant for any reason but `lost` is not revived; an Ended conversation is not a candidate; a poll from a session not in the set does no conversation scan; a tool call arriving before the status post revives and does not mint.
- New life: a `/session_start` for a lost-dormant session rewrites the reason to `restarted`, empties the set entry, and a later tool call mints as today.
- Hydration: an alive member with a `lost`, `ended` or missing record hydrates dormant, unbound and in the set, and is written back; one with a live record is bound as today; `session_ended_at` comes from the record or stays unset; a stored `lost` member is in the set after a restart.
- Fallback: with away mode on, force-ending a conversation whose member's record is `lost`, `ended` or missing mints nothing and leaves the session unbound; a live member still gets its home.
- Idle end: each of the four conditions blocks the end on its own; the idle clock uses the later of activity and last dormancy; 0 disables; a hidden conversation ends; no push is sent; `meta.ended_at` is persisted; the conversation is not deleted in the tick that ended it and is deleted 72 hours after its end, not after its last activity; a hand-ended conversation gets the same 72 hours.
- The round trip that is the point of decision 2: lost, dormant, a human message sent, a poll, alive and bound again in the same conversation, the message delivered as a notice.

The full smoke run's killed-session flow gains two assertions: the killed session's member is dormant after the park, and when its poll resumes the member is alive and bound in the same conversation.

## Deploy and live checks

1. Before the restart, run a dry run against live data that prints what Section 3 would repair and what Section 5 would end at its first tick, and show John the list. This is the last point at which any of those conversations can be kept, by sending a message to it or by raising the knob.
2. Restart the service (`-SkipTests`), preflight, then read the hydration and sweep lines in `logs/switchboard.jsonl` for the repair count and the `conversation_idle_ended` lines, and compare with the dry run. Confirm in RTDB that an ended conversation carries `meta.ended_at` and is still there after the next hourly tick.
3. Live: kill a session that is a member of a throwaway conversation and watch its member go dormant on Operator within two sweep ticks; start a session, bind it, suspend its poll past 90 s and resume it, and confirm it is back in the same conversation with no new one minted.
4. With away mode on, end a conversation whose only member is lost and confirm no "(home)" conversation appears.
5. The full smoke run with restart, since this touches the sweep, the session-end path and hydration.

No mod change is involved, so there is nothing to deploy to the cache copy or the WSL clone.

## Docs and tracking

- `CLAUDE.md`: the Conversation model section (dormancy on lost, the same-life revive, the idle-end rule and its knob, retention measured from the end), the Inbox bullet's liveness sentence, and the Layout lines for any new module.
- `docs/switchboard-design-spec-comprehensive.md`: the member-state rules in section 2.2 and the retention text.
- `plugin/skills/switchboard/SKILL.md` only if an agent-visible string changes (the wake text for a lost peer is new).
- Backlog: cut T-298 and T-249; note on T-032 that the hydrated count is now bounded; note on T-260 what the launch window does and does not cover; file the two "Left for later" items.

## Left for later

- **Rejoin on a new life.** A session resumed by hand, or standalone from the board, still mints a new conversation on its first away-mode ask and strands its dormant member in the old one. Fixing it needs John's answer on what a standalone board resume means for a session that already has a conversation, and on a session with dormant memberships in more than one.
- **Board resume into a conversation the session is already a dormant member of** does not flip that member alive (`handle_resume_session` acts only when the session is not a member). Found by the builder check; it predates this work.

## Risks

- **A wrong lost verdict now has a visible effect:** a system message and woken peers, where before nothing happened. The revive undoes the state but not the message. Waiting for silence in every channel removes the once-a-minute flap; a session whose poller has died still goes dormant each time it sits idle for 90 s and comes back at its next status post. For such a session dormant is close to the truth, since it cannot receive anything from its inbox.
- **Resuming a session that is alive but judged lost** launches a second process on the same session id. That risk exists today through the sessions board, which already offers Resume on a `lost` record.
- **Deleting history.** The idle rule deletes conversations for good 72 hours after ending them. The dry run and the knob are the controls.

## As built (2026-11-20)

Shipped in `fc16eff..2b2af91` and deployed 2026-11-20. Where the build differs from the sections above:

- **Section 3.** The repair runs only when the `sessions` read returned a non-empty node; a failed or empty read skips it (`hydration_member_repair_skipped: roster not hydrated`) and the sweep's 900 s rule catches those members instead. The lost-dormant set is rebuilt on every start either way.
- **Section 4.** `handle_force_end`, which every end path uses, runs the session fallback only for sessions that are unbound or bound to the conversation being ended. A session bound to another conversation keeps its binding, and only a home pointer naming the ended conversation is cleared. Without this, idle-ending an old conversation pulled a live, hand-resumed session out of its current one.
- **Section 5.** A conversation with any session bound to it is never an idle-end candidate (a phone spawn binds before its member exists), and the predicate is checked again for each conversation just before it is ended.
- **Section 2.** A session end is authoritative over a lost-dormant member: a revive is refused once the session's roster record is `ended`, and a `/session_end` for an unbound lost-dormant session closes the window with the end's reason (permanently lost for `clear` and `compact`), as a `/session_start` does with `restarted`.
- **Roster mirror.** A revive by poll fires two record writes back to back (the poll's, then the binding's), and the old fire-and-forget writes could land out of order and leave RTDB without the session's `conversation_id`. The roster mirror is now ordered and coalesced per session.
- **Smoke.** The killed-session flow polls once after its conversation is bound, so its last sign of life follows its binding and dormancy follows the 90 s rule.
- **Risks, added.** A mod hot reload also sends `/session_start`, so it closes the revive window for a lost-dormant session (T-301).
- **First run.** 17 repaired and 36 ended, the same sets the dry run listed; the next restart hydrated 13 conversations instead of 48.
