"""Live smoke harness for the deployed Switchboard service. A raw MCP client
plus a firebase_admin RTDB witness, standing in for an agent and John -
exercises the running service end to end over its public surfaces (HTTP,
MCP, RTDB). No server imports; run from the repo root."""
from __future__ import annotations
import argparse, asyncio, sys
from datetime import datetime, timezone
from pathlib import Path

from _smoke_lib import (
	FlowSkip, Reporter, RunContext, SmokeFailure,
	crash_counts, http_get_json, http_post_json, init_firebase, load_env, make_context,
	mcp_call, poll_until, restart_service, rtdb, start_blocking_ask, write_session_end_marker,
)


def build_arg_parser() -> argparse.ArgumentParser:
	parser = argparse.ArgumentParser(description=__doc__)
	parser.add_argument("--skip-restart", action="store_true", help="skip the restart-survival flow")
	parser.add_argument("--force", action="store_true", help="proceed even if away mode is already ON")
	parser.add_argument("--keep", action="store_true", help="skip cleanup, leaving run residue for debugging")
	parser.add_argument("--preflight-only", action="store_true", help="run Flow 0 only and exit (fully read-only)")
	parser.add_argument("--base-url", default="http://127.0.0.1:9876", help="base URL of the deployed service")
	return parser


async def flow_preflight(ctx, rep, args):
	hz = http_get_json(f"{ctx.base_url}/healthz")
	bad = [l["name"] for l in hz.get("listeners", []) if l.get("state") != "live"]
	if bad:
		raise SmokeFailure(f"listeners not live: {bad}")
	ctx.crash_snapshot = crash_counts(hz)
	away = http_get_json(f"{ctx.base_url}/away-mode")
	ctx.prior_away = bool(away.get("active"))
	if ctx.prior_away and not args.force:
		raise SmokeFailure("away mode is already ON (a real away session may be live) - rerun with --force to proceed")
	if args.preflight_only:
		return
	status = http_post_json(f"{ctx.base_url}/session_start",
		{"session_id": ctx.cli_session_id, "cwd": ctx.cwd, "source": "startup"})
	if status != 200:
		raise SmokeFailure(f"/session_start returned {status}")


async def flow_away_mode_roundtrip(ctx, rep, args):
	await mcp_call(ctx, "set_away_mode", {"value": True})
	await poll_until("GET /away-mode active=True after set_away_mode(True)",
		lambda: True if http_get_json(f"{ctx.base_url}/away-mode").get("active") is True else None, 10)
	await poll_until("RTDB global_settings/away_mode=True after set_away_mode(True)",
		lambda: True if rtdb(ctx, "global_settings/away_mode").get() is True else None, 10)

	await mcp_call(ctx, "set_away_mode", {"value": False})
	await poll_until("GET /away-mode active=False after set_away_mode(False)",
		lambda: True if http_get_json(f"{ctx.base_url}/away-mode").get("active") is False else None, 10)
	await poll_until("RTDB global_settings/away_mode=False after set_away_mode(False)",
		lambda: True if rtdb(ctx, "global_settings/away_mode").get() is False else None, 10)


async def flow_atdesk_redirect(ctx, rep, args):
	question = f"smoke at-desk probe {ctx.run_id}"
	text = await mcp_call(ctx, "ask_human", {"question": question, "sender": ctx.sender, "title": ctx.title})
	expected = "ERROR: John is at his desk. Ask this question via the terminal."
	if text != expected:
		raise SmokeFailure(f"at-desk sentinel mismatch: expected {expected!r}, got {text!r}")

	def _find_conv():
		convs = rtdb(ctx, "conversations").get() or {}
		for cid, node in convs.items():
			if isinstance(node, dict) and ctx.sender in (node.get("members_active") or {}):
				return cid
		return None
	ctx.conversation_id = await poll_until("conversation minted for smoke sender", _find_conv, 15)

	def _find_notify():
		msgs = rtdb(ctx, f"messages/{ctx.conversation_id}").get() or {}
		for mid, node in msgs.items():
			if isinstance(node, dict) and node.get("type") == "notify" and question in (node.get("text") or ""):
				return mid
		return None
	await poll_until("at-desk question landed as notify message", _find_notify, 15)


async def flow_live_ask_answer(ctx, rep, args):
	await mcp_call(ctx, "set_away_mode", {"value": True})

	hz0 = http_get_json(f"{ctx.base_url}/healthz")
	pending0 = hz0["pending"]["count"]
	answered0 = hz0["pending"]["total_answered"]

	question = f"smoke live ask {ctx.run_id}"
	task = start_blocking_ask(ctx, question, suggestions=["yes", "no"])

	def _find_pending():
		pendings = rtdb(ctx, f"conversations/{ctx.conversation_id}/pending_questions").get() or {}
		for rid, node in pendings.items():
			if isinstance(node, dict) and node.get("sender") == ctx.sender:
				return rid
		return None
	ctx.request_id = await poll_until("pending question recorded for smoke sender", _find_pending, 15)

	record = rtdb(ctx, f"conversations/{ctx.conversation_id}/pending_questions/{ctx.request_id}").get() or {}
	if record.get("suggestions") != ["yes", "no"]:
		raise SmokeFailure(f"pending_questions suggestions mismatch: expected ['yes', 'no'], got {record.get('suggestions')!r}")

	hz1 = http_get_json(f"{ctx.base_url}/healthz")
	if not (hz1["pending"]["count"] > pending0):
		raise SmokeFailure(f"pending.count did not increase: before={pending0}, after={hz1['pending']['count']}")

	expected_reply = f"smoke-answer-{ctx.run_id}"
	rtdb(ctx, f"answers/{ctx.conversation_id}/{ctx.request_id}").set({
		"text": expected_reply, "sender": "John",
		"request_id": ctx.request_id, "written_at": datetime.now(timezone.utc).isoformat(),
	})

	reply = await asyncio.wait_for(task, 20)
	if reply != expected_reply:
		raise SmokeFailure(f"blocked ask returned {reply!r}, expected {expected_reply!r}")

	def _find_human_message():
		msgs = rtdb(ctx, f"messages/{ctx.conversation_id}").get() or {}
		for mid, node in msgs.items():
			if isinstance(node, dict) and node.get("type") == "human" and node.get("text") == expected_reply:
				return mid
		return None
	await poll_until("human-type answer message landed", _find_human_message, 15)

	def _pending_gone():
		gone = rtdb(ctx, f"conversations/{ctx.conversation_id}/pending_questions/{ctx.request_id}").get() is None
		return True if gone else None
	await poll_until("pending_questions record cleared", _pending_gone, 15)

	def _healthz_settled():
		hz = http_get_json(f"{ctx.base_url}/healthz")
		if hz["pending"]["total_answered"] > answered0 and hz["pending"]["count"] == pending0:
			return hz
		return None
	await poll_until("healthz pending settled (total_answered incremented, count back to baseline)", _healthz_settled, 15)

	notif = await mcp_call(ctx, "notify_human", {"message": f"smoke flow 3 complete {ctx.run_id}", "sender": ctx.sender})
	if notif != "ok":
		raise SmokeFailure(f"notify_human expected 'ok', got {notif!r}")


async def flow_restart_survival(ctx, rep, args):
	question = f"smoke restart-survival probe {ctx.run_id}"
	task = start_blocking_ask(ctx, question, suggestions=["yes", "no"])

	def _find_pending():
		pendings = rtdb(ctx, f"conversations/{ctx.conversation_id}/pending_questions").get() or {}
		for rid, node in pendings.items():
			if isinstance(node, dict) and node.get("sender") == ctx.sender:
				return rid
		return None
	ctx.request_id = await poll_until("pending question recorded before restart", _find_pending, 15)

	await restart_service(ctx)

	# The restart severs the MCP session, but the blocked ask does not error promptly -
	# it hangs, so this wait_for usually TIMES OUT (TimeoutError) rather than raising a
	# transport error. Either outcome is a pass: the task must not return a clean answer
	# (no answer is written yet), and parked==1 below is the real proof the restart
	# rehydrated the pending future-less. 15s just caps the inevitable hang.
	try:
		await asyncio.wait_for(task, 15)
	except Exception as exc:
		print(f"blocked ask task died as expected on restart: {type(exc).__name__}: {exc}")
	else:
		raise SmokeFailure("blocked call survived restart?!")

	away = http_get_json(f"{ctx.base_url}/away-mode")
	if away.get("active") is not False:
		raise SmokeFailure(f"away mode not reset to False after restart (startup reset contract): {away!r}")

	hz = http_get_json(f"{ctx.base_url}/healthz")
	parked = hz["pending"]["parked"]
	if parked != 1:
		raise SmokeFailure(f"expected /healthz pending.parked == 1 after restart, got {parked!r}")

	expected_reply = f"smoke-restart-answer-{ctx.run_id}"
	rtdb(ctx, f"answers/{ctx.conversation_id}/{ctx.request_id}").set({
		"text": expected_reply, "sender": "John",
		"request_id": ctx.request_id, "written_at": datetime.now(timezone.utc).isoformat(),
	})

	def _parked_cleared():
		hz2 = http_get_json(f"{ctx.base_url}/healthz")
		return True if hz2["pending"]["parked"] == 0 else None
	await poll_until("healthz pending.parked back to 0 after answer", _parked_cleared, 20)

	def _pending_gone():
		gone = rtdb(ctx, f"conversations/{ctx.conversation_id}/pending_questions/{ctx.request_id}").get() is None
		return True if gone else None
	await poll_until("pending_questions record cleared after parked answer", _pending_gone, 15)

	def _find_human_message():
		msgs = rtdb(ctx, f"messages/{ctx.conversation_id}").get() or {}
		for mid, node in msgs.items():
			if isinstance(node, dict) and node.get("type") == "human" and node.get("text") == expected_reply:
				return mid
		return None
	await poll_until("human-type answer message landed after parked resolve", _find_human_message, 15)

	def _find_notice():
		data = http_get_json(f"{ctx.base_url}/away-mode?session_id={ctx.cli_session_id}")
		for notice in data.get("notices") or []:
			if "John answered" in notice and expected_reply in notice:
				return notice
		return None
	await poll_until("parked-answer notice delivered via GET /away-mode", _find_notice, 15)


FLOWS: list[tuple[str, callable]] = [
	("preflight", flow_preflight),
	("away-mode round-trip", flow_away_mode_roundtrip),
	("at-desk redirect + conversation discovery", flow_atdesk_redirect),
	("live ask/answer round-trip", flow_live_ask_answer),
	("restart survival", flow_restart_survival),
]


async def cleanup(ctx: RunContext, rep: Reporter, args) -> None:
	"""Tolerant best-effort teardown: collects errors from each sub-step and
	raises one SmokeFailure summarizing them at the end, so the enclosing
	rep.flow("cleanup") context manager records a single PASS/FAIL."""
	errors: list[str] = []

	if ctx.conversation_id is not None:
		try:
			await mcp_call(ctx, "leave_conversation", {"sender": ctx.sender, "parting_message": "smoke run complete"})
		except Exception as exc:
			errors.append(f"leave_conversation failed: {exc}")

		def _conv_ended():
			state = rtdb(ctx, f"conversations/{ctx.conversation_id}/meta/state").get()
			return True if state == "ended" else None
		try:
			await poll_until("conversation state == ended after leave", _conv_ended, 15)
		except Exception as exc:
			errors.append(str(exc))

		try:
			rtdb(ctx, f"conversations/{ctx.conversation_id}/meta/hidden").set(True)
		except Exception as exc:
			errors.append(f"hide conversation failed: {exc}")

	try:
		write_session_end_marker(ctx)
	except Exception as exc:
		errors.append(f"write_session_end_marker failed: {exc}")

	def _session_ended():
		state = rtdb(ctx, f"sessions/{ctx.cli_session_id}/state").get()
		return True if state == "ended" else None
	try:
		await poll_until("session state == ended after marker sweep", _session_ended, 20, interval=5.0)
	except Exception as exc:
		errors.append(str(exc))

	try:
		current = http_get_json(f"{ctx.base_url}/away-mode")
		if bool(current.get("active")) != bool(ctx.prior_away):
			try:
				await mcp_call(ctx, "set_away_mode", {"value": ctx.prior_away})
			except Exception:
				rtdb(ctx, "global_settings/away_mode").set(ctx.prior_away)

			def _away_restored():
				restored = http_get_json(f"{ctx.base_url}/away-mode")
				return True if bool(restored.get("active")) == bool(ctx.prior_away) else None
			await poll_until("away mode restored to prior state", _away_restored, 15)
	except Exception as exc:
		errors.append(f"away mode restore failed: {exc}")

	try:
		hz = http_get_json(f"{ctx.base_url}/healthz")
		final_counts = crash_counts(hz)
		snapshot = ctx.crash_snapshot or {}
		increased = [
			key for key, before in snapshot.items()
			if key in final_counts and final_counts[key] > before
		]
		if increased:
			errors.append(f"crash counts increased during run: {increased} (before={snapshot}, after={final_counts})")
	except Exception as exc:
		errors.append(f"final healthz crash-count check failed: {exc}")

	if errors:
		raise SmokeFailure("; ".join(errors))


async def main() -> int:
	args = build_arg_parser().parse_args()
	repo_root = Path(__file__).resolve().parents[2]
	ctx = make_context(args.base_url, repo_root)
	rep = Reporter()
	if not args.preflight_only:
		sa, url = load_env(repo_root)
		ctx.fb_app = init_firebase(sa, url)
	try:
		for name, fn in FLOWS:
			if fn is flow_restart_survival and args.skip_restart:
				rep.skip(name, "--skip-restart")
				continue
			try:
				with rep.flow(name):
					await fn(ctx, rep, args)
			except FlowSkip:
				break
			if fn is flow_preflight and args.preflight_only:
				break
	finally:
		if not args.keep and not args.preflight_only:
			try:
				with rep.flow("cleanup"):
					await cleanup(ctx, rep, args)
			except FlowSkip:
				pass
	return rep.summary()


if __name__ == "__main__":
	sys.exit(asyncio.run(main()))
