# Wear: notifications only, bridged from the phone; questions clear when they end (design)

**Date:** 2026-10-11
**Status:** approved by John 2026-10-11 (design and written spec). The answered-side hook was revised at planning (marked "revised at planning" below). Plan: `docs/superpowers/plans/2026-10-11-wear-notifications-only.md` (disk-only, gitignored). Landed 2026-10-11 (`36759bb`..`f270f76`, deployed, live checks passed; final-review fixes `9a14478`, `bc0fe14`, `f270f76`); deviations in the plan's Deviations section.
**Backlog:** T-285 (cut the Wear app down to notifications with "open on phone", dismissed in sync with the phone). Discharges T-262 (the `:wear:lintDebug` failure) by deleting the module it fails in.
**Evidence:** an emulator probe on 2026-10-11 (below), and the code paths cited in each section.

## Goal

The Wear app is a full client today: a conversation list, a message view and voice replies over the shared `MainViewModel`, with its own FCM topic subscriptions. John wants the watch to show only notifications, each with an "open on phone" action, cleared in sync with the phone. He also wants a question's notification cleared when the question is answered somewhere else.

1. **Remove the watch app.** The phone's own notifications reach the watch through Wear OS notification bridging, which supplies "Open on phone" and two-way dismissal sync with no watch code.
2. **Clear a question's notification when the question ends**, by any path. The server sends a dismiss message, the phone cancels the notification, and bridging clears the watch copy.

## Facts the design rests on

**Today's code (read 2026-10-11):**

- The phone and the watch both subscribe to the `questions` and `notifications` FCM topics (`SwitchboardApplication`) and build notifications from the same data-only payload in the shared `BaseSwitchboardMessagingService`. The notification id is `notificationIdFor(message_id)`, the card's RTDB push key hashed (`NotificationIds.kt`).
- The watch app sets `notificationBridgeMode` to `NO_BRIDGING` and builds its notifications `setLocalOnly(true)`, so the phone's notifications never reach the watch and a dismissal never crosses devices.
- The phone never cancels a notification in code. One goes away only when John swipes it or taps it (`setAutoCancel(true)`).
- Phone notifications carry no actions, so bridging loses nothing.
- A notification lacking `body` is dropped before display (`val body = remoteMessage.data["body"] ?: return`). An app without the new handling therefore ignores a dismiss message safely, so the server and phone can deploy in either order.
- The server knows each question's card id: `PendingRequest.msg_id`, set after the question write in `ask_human` and restored by hydration from the `pending_questions` record.
- No server code knows the watch exists.

**Emulator probe (2026-10-11), phone `Pixel_10_Pro` paired with `Wear_OS_Small_Round`, no Switchboard app on the watch.** A throwaway build of the phone app added a `dismiss` branch to `onMessageReceived` that cancels `notificationIdFor(message_id)`. Pushes went straight to the emulator's FCM token with the server's data keys. All passed:

| Check | Result |
|---|---|
| A phone question bridges to the watch | Shown with the app icon, title and body; the watch alerted with a haptic |
| "Open on phone" opens the conversation | Ran the phone notification's content intent: `MainActivity` single-top with the `conv_id` extra, landing in the conversation; both copies cleared |
| Swipe on the watch clears the phone | Cleared |
| Swipe on the phone clears the watch | Cleared |
| Tap on the phone clears the watch | Cleared (auto-cancel) |
| A `dismiss` push with the app in the background | The phone cancelled within 1 ms of receipt; the watch copy cleared about 25 ms later |

Also seen: three or more Switchboard notifications are bundled on the phone, and swiping the bundle clears all of them on both devices; the bundle's summary is not bridged. Getting the emulators to bridge took three workarounds in the companion app that a real, already-paired watch does not need; they are in project memory (`project_wear_emulator_pairing`).

## Decisions

1. **Approach A: remove the watch app and rely on bridging.** Rejected: a slim watch app with its own FCM, a remote-interactions "Open on phone" action and a shared `dismissalId`. It works with the phone out of range, but that is the case where "open on phone" is moot, and it keeps a second APK and new code.
2. **Every ending of a question sends the dismiss**: answered on any surface, cancelled from the terminal, superseded, timed out, force-ended, combined, its session ended (a parked question survives a resumable end, stays answerable and keeps its notification), withdrawn by spawn cleanup, TTL-swept, resolved by an inbound phone message, cancelled at hydration, plus background and parked asks. Each leaves a notification asking for an answer the question can no longer take; the card stays in the conversation's history.
3. **The dismiss goes at normal FCM priority.** FCM may demote an app's high-priority messages when they show no notification, which would put question delivery at risk. Normal arrives at once while the phone is awake; in deep Doze it waits for the next maintenance window, and the bridged copy lingers until then.
4. **`android/shared` stays.** With the watch gone, `app` is its only consumer; folding it into `app` is a move of about 20 files with no change in behaviour.

## Part 1: remove the watch app

- Delete `android/wear/` and drop `':wear'` from `android/settings.gradle`. The directory also holds untracked files (its `google-services.json` and build output); they go with it.
- Delete the shared code only the watch used:
  - `partitionConversationsForWatch` (`ConversationPolicy.kt`) and its two tests in `ConversationPolicyTest.kt`.
  - `MainViewModel`'s `_pendingDeepLinkMessageId`, `pendingDeepLinkMessageId`, `setPendingDeepLinkMessageId` and `clearPendingDeepLinkMessageId`. The phone keeps its own copy in `MainActivity`.
  - The `decorate` hook on `BaseSwitchboardMessagingService`, and the comments naming the watch there and in `PendingReconciler.kt`, `Models.kt` and `ConversationViewScreen.kt`.
- Before deleting each, confirm by grep that nothing else references it, tests included.
- Drop the stale `.graphify-verdicts.json` entry for the watch's `MarkdownText`.
- **Deploy:** John uninstalls the Switchboard app from his real watch. Until he does, its `NO_BRIDGING` setting keeps blocking bridging and it keeps receiving its own pushes.

## Part 2: clear a question's notification when it ends

### Server

- **A new backend method sends the dismiss**: a data-only FCM message to the `questions` topic, `data = {conv_id, sb_message_type: "dismiss", message_id}`, `AndroidConfig(priority="normal")`, no title or body. It is fire-and-forget, so it adds no latency to a terminal path, and a failed send is logged with `surface_error` (a lingering notification is a nuisance, not a fault). It does not draw from the rate limiter and ignores away mode, since the question's push was not gated by away mode either.
- **Cancel-side endings** all reach `FirebaseBackend.mark_question_cancelled`: `terminate_pending` (by default), `_safe_mark_cancelled` (supersession and the fallback arms in `ask_human`; a live ask whose session ends is cancelled by `cli_session_end` and reaches it through the asker's own cancel arm), the two inbound paths in `server/inbound.py`, and hydration's cancel of unparkable records. That method already walks `messages/<conv_id>` to find the card by `request_id`; once it finds the card it sends the dismiss for that `msg_id`. A call that finds no card sends nothing.
- **Answered endings** write John's reply with `attached_to_msg_id` naming the question card. The only two writers of that field are the two `registry.resolve` sites: `dispatch_responses` (live, parked and background asks answered from the phone or Operator) and `_apply_bulk_respond_decision`'s send-default fan-out. `FirebaseBackend.write_conversation_message` sends the dismiss for a `human` message that carries `attached_to_msg_id`. *Revised at planning:* this spec first had both sites call the new method with the record's `msg_id`, falling back to a lookup by `request_id` when `msg_id` was unset. Hooking the backend's write needs no new call sites (several test fakes on the answer path implement only the methods they use) and drops only that fallback (see Risks).
- A second dismiss for the same card (a cancel after a resolve, a replay) cancels nothing on the phone and is harmless.

### Phone

- `BaseSwitchboardMessagingService.onMessageReceived` handles `sb_message_type == "dismiss"` before the `body` check: it cancels `notificationIdFor(message_id)` and returns. Bridging clears the watch copy.
- The decision sits in a pure helper beside `notificationIdFor` (the id to cancel for a message's data, or none) with unit tests in `NotificationIdsTest`.

## Testing and live verification

- **Server (pytest):** the dismiss message's shape (topic, data keys, normal priority, no title or body); the cancel hook (a found card is dismissed; a cancel that finds no card sends nothing); the reply hook (an attached `human` reply is dismissed, an unattached one is not); a failed send logged and not raised; and a pin that the away-exit bulk reply carries the question's `msg_id` (the dispatch paths' attached replies are already pinned by `test_gateway_ask_human.py` and `test_dispatch_background_answer.py`).
- **Phone (JVM unit tests):** the helper returns the id for a dismiss with a `message_id`, and nothing for other types or a missing id. `:shared:testDebugUnitTest`, `:app:assembleDebug`, `:app:lintDebug` and `:shared:lintDebug` pass; with the watch module gone, the full `./gradlew build` should pass too, which retires the per-module substitute T-262 forced.
- **Live, on the paired emulators**, after the service restart and a debug install of the new phone build:
  - a real question from a smoke-style flow bridges to the watch;
  - answering it from Operator clears it on the phone and the watch;
  - a second ask from the same session supersedes the first, and the first clears on both (the cancel-side hook);
  - "Open on phone" and the swipe checks still pass with the real build.

  Questions push to the `questions` topic, so John's real phone (and his real watch app, until he removes it) also receive each test question. His installed app ignores the dismiss and leaves the notification. That is the cost of a topic-addressed check; the plan states how many such pushes the run makes.

## Docs and tracking on land

- `CLAUDE.md` (layout: two Gradle modules, no `wear/`), `README.md` (Android + Wear mentions), `android/README.md`, and the architecture figure `docs/media/switchboard-architecture.svg`, whose "Wear OS companion" box becomes the watch receiving bridged notifications from the phone.
- The comprehensive spec: §1 (the module list), §12.6 (the dismiss message and the cleared-on-end rule beside the channels), and §12.7 (rewritten: the watch shows the phone's bridged notifications; there is no watch app). Date-stamped specs from earlier work stay as written.
- Backlog: cut T-285 and T-262. Memory: delete `feedback_minimal_wear_effort`, and update the emulator-port note and the chunk-pipeline note, which assume a watch APK and the `:wear:lintDebug` failure.

## Out of scope

- Clearing `notify` and `document` notifications when their conversation is read.
- Tuning how bridged notifications look on the watch (a `WearableExtender` on the phone's notification).
- Watch notifications with the phone out of range.
- Folding `android/shared` into `android/app`.
- The Firebase console's registration of the watch app; John's call.

## Risks

- **A dismiss can overtake its question.** FCM does not order a normal-priority message behind a high-priority one. A question that ends within about a second of being asked could get its dismiss first; the cancel then finds nothing and the question's notification stays. Rare for a human-answered question; accepted.
- **An answer that lands before `msg_id` is set** (revised at planning). `ask_human` sets the record's `msg_id` only after the question write returns, a few hundred ms. An answer inside that window is written without `attached_to_msg_id`, so it already loses its RESPONDED splice, and now its notification stays too. A typed answer cannot land that fast; the away-exit bulk reply can, if John exits away mode in the same moment a question is being written. Accepted.
- **Doze delay.** In deep Doze a normal-priority dismiss waits for the phone's next maintenance window, so a stale copy can sit on the watch for a while (decision 3).
- **A force-stopped phone app receives no FCM**, dismiss included. That is Android's rule, and it already applies to questions.
