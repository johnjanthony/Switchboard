"""Pre-deploy dry run: what the first start on the lifecycle code will repair and end.

Reads RTDB `conversations` and `sessions` (same env vars as firebase_query.py)
and prints the plan. Read-only: it never writes to Firebase.

Usage:
	python scripts/lifecycle_dry_run.py
"""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from server.hydration import _hydrate_conversation
from server.member_lifecycle import idle_end_candidates, idle_since, repair_members
from server.registry import Registry
from server.session_registry import SessionRegistry


def plan_first_run(conversations_node: dict, sessions_node: dict, *, idle_days: int, now_ts: float) -> dict:
	registry = Registry()
	for conv_id, conv_node in (conversations_node or {}).items():
		_hydrate_conversation(registry, conv_id, conv_node)
	session_registry = SessionRegistry()
	roster_hydrated = False
	if isinstance(sessions_node, dict):
		for data in sessions_node.values():
			if isinstance(data, dict):
				session_registry.hydrate_record(data)
		roster_hydrated = bool(sessions_node)

	repaired = []
	if roster_hydrated:
		for conv_id, member in repair_members(registry, session_registry):
			repaired.append((conv_id, registry.conversations[conv_id].title, member.sender))

	def _open_questions(conv_id):
		node = (conversations_node or {}).get(conv_id)
		pending = node.get("pending_questions") if isinstance(node, dict) else None
		return len(pending) if isinstance(pending, dict) else 0

	ended_rows = []
	ended_ids = set()
	for conv, days in idle_end_candidates(registry, idle_days=idle_days, now_ts=now_ts):
		ended_rows.append((conv.id, conv.title, conv.hidden, round(days, 1), _open_questions(conv.id)))
		ended_ids.add(conv.id)

	kept = []
	for conv_id, conv in registry.conversations.items():
		if conv.state == "active" and conv_id not in ended_ids:
			since = idle_since(conv)
			idle = round((now_ts - since) / 86400, 1) if since is not None else None
			has_alive = any(m.alive for m in conv.members_active.values())
			kept.append((conv_id, conv.title, idle, has_alive, _open_questions(conv_id)))

	return {
		"repaired": sorted(repaired),
		"ended": sorted(ended_rows),
		"kept": sorted(kept),
		"repair_skipped": not roster_hydrated,
	}


def format_plan(plan: dict, idle_days: int) -> list[str]:
	lines = []
	if plan["repair_skipped"]:
		lines.append("NOTE: the sessions node is empty; the first start would skip the member repair.")
	lines.append(f"Members repaired (made dormant): {len(plan['repaired'])}")
	for conv_id, title, sender in plan["repaired"]:
		lines.append(f"	{conv_id}	{title}	{sender}")
	lines.append(f"Conversations ended (idle {idle_days} days): {len(plan['ended'])}")
	for conv_id, title, hidden, days, open_questions in plan["ended"]:
		lines.append(f"	{conv_id}	{title}	hidden={hidden}	idle={days:.1f}d	open_questions={open_questions}")
		if open_questions:
			lines.append(f"	NOTE: {conv_id} has {open_questions} open question record(s); the server may keep it")
	lines.append(f"Conversations kept: {len(plan['kept'])}")
	for conv_id, title, days, has_alive, open_questions in plan["kept"]:
		idle = f"{days:.1f}d" if days is not None else "no usable timestamp"
		alive = "yes" if has_alive else "no"
		lines.append(f"	{conv_id}	{title}	idle={idle}	alive_member={alive}	open_questions={open_questions}")
	return lines


def main() -> int:
	from dotenv import load_dotenv
	load_dotenv()
	import firebase_admin
	from firebase_admin import credentials, db
	from server.config import load_config

	cred = credentials.Certificate(os.environ["FIREBASE_SERVICE_ACCOUNT_JSON"])
	firebase_admin.initialize_app(cred, {"databaseURL": os.environ["FIREBASE_DATABASE_URL"]})
	conversations = db.reference("conversations").get() or {}
	sessions = db.reference("sessions").get() or {}
	idle_days = load_config().conversation_idle_end_days

	plan = plan_first_run(conversations, sessions, idle_days=idle_days, now_ts=time.time())
	for line in format_plan(plan, idle_days):
		print(line)
	return 0


if __name__ == "__main__":
	sys.exit(main())
