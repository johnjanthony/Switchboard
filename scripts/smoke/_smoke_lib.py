"""Shared infrastructure for the live smoke harness. No server imports -
the harness exercises the deployed service over its public surfaces only."""
from __future__ import annotations
import asyncio, json, time, uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from urllib import request as _rq

class SmokeFailure(Exception):
	pass

def load_env(repo_root: Path) -> tuple[str, str]:
	from dotenv import load_dotenv
	import os
	load_dotenv(repo_root / ".env", override=False)
	sa = os.environ.get("FIREBASE_SERVICE_ACCOUNT_JSON")
	url = os.environ.get("FIREBASE_DATABASE_URL")
	if not sa or not url:
		raise SmokeFailure("FIREBASE_SERVICE_ACCOUNT_JSON / FIREBASE_DATABASE_URL unset (repo .env)")
	return sa, url

def init_firebase(sa_path: str, db_url: str):
	import firebase_admin
	from firebase_admin import credentials
	return firebase_admin.initialize_app(credentials.Certificate(sa_path), {"databaseURL": db_url}, name="smoke")

def rtdb(ctx, path: str):
	from firebase_admin import db
	return db.reference(path, app=ctx.fb_app)

def http_get_json(url: str) -> dict:
	with _rq.urlopen(_rq.Request(url), timeout=5) as resp:
		return json.loads(resp.read().decode("utf-8"))

def http_post_json(url: str, body: dict) -> int:
	req = _rq.Request(url, data=json.dumps(body).encode("utf-8"), headers={"Content-Type": "application/json"}, method="POST")
	with _rq.urlopen(req, timeout=5) as resp:
		return resp.status

async def poll_until(desc: str, fn, timeout: float, interval: float = 0.5):
	deadline = time.monotonic() + timeout
	last = None
	while time.monotonic() < deadline:
		last = await asyncio.to_thread(fn)
		if last:
			return last
		await asyncio.sleep(interval)
	raise SmokeFailure(f"timeout ({timeout}s) waiting for {desc}; last observation: {last!r}")

def crash_counts(healthz: dict) -> dict[str, int]:
	out = {}
	for l in healthz.get("listeners", []):
		out[f"listener:{l['name']}"] = l.get("crash_count", 0)
	for d in healthz.get("dispatch_loops", []):
		out[f"loop:{d['name']}"] = d.get("crash_count", 0)
	return out

async def mcp_call(ctx, tool: str, args: dict, timeout: float = 30) -> str:
	from mcp import ClientSession
	from mcp.client.streamable_http import streamablehttp_client
	full = dict(args)
	full.setdefault("cli_session_id", ctx.cli_session_id)
	full.setdefault("cwd", ctx.cwd)
	async def _run():
		async with streamablehttp_client(f"{ctx.base_url}/mcp") as (read, write, _):
			async with ClientSession(read, write) as session:
				await session.initialize()
				result = await session.call_tool(tool, full)
				text = result.content[0].text if result.content else ""
				if getattr(result, "isError", False):
					raise SmokeFailure(f"{tool} returned tool-error: {text}")
				return text
	return await asyncio.wait_for(_run(), timeout)

def start_blocking_ask(ctx, question: str, suggestions=None) -> asyncio.Task:
	async def _ask():
		from mcp import ClientSession
		from mcp.client.streamable_http import streamablehttp_client
		async with streamablehttp_client(f"{ctx.base_url}/mcp") as (read, write, _):
			async with ClientSession(read, write) as session:
				await session.initialize()
				result = await session.call_tool("ask_human", {
					"question": question, "sender": ctx.sender, "title": ctx.title,
					"suggestions": suggestions, "cli_session_id": ctx.cli_session_id, "cwd": ctx.cwd,
				})
				return result.content[0].text if result.content else ""
	return asyncio.create_task(_ask())

async def restart_service(ctx) -> None:
	import subprocess
	script = ctx.repo_root / "scripts" / "restart-service.ps1"
	await asyncio.to_thread(subprocess.run,
		["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(script), "-SkipTests"],
		capture_output=True, text=True, timeout=120)
	# restart-service.ps1 now checks nssm exit codes, but /healthz stays the liveness truth here.
	def _alive():
		try:
			hz = http_get_json(f"{ctx.base_url}/healthz")
		except Exception:
			return None
		return hz if all(l.get("state") == "live" for l in hz.get("listeners", [])) else None
	await poll_until("service live after restart", _alive, 90, interval=2.0)

def write_session_end_marker(ctx) -> None:
	import os
	marker_dir = ctx.repo_root / "logs" / "session-end"
	marker_dir.mkdir(parents=True, exist_ok=True)
	safe = "".join(c for c in ctx.cli_session_id if c.isalnum() or c in "-_")
	marker = {"session_id": ctx.cli_session_id, "reason": "other",
		"ended_at": datetime.now(timezone.utc).isoformat()}
	tmp = marker_dir / f"{safe}.json.tmp"
	tmp.write_text(json.dumps(marker), encoding="utf-8")
	os.replace(tmp, marker_dir / f"{safe}.json")

@dataclass
class RunContext:
	run_id: str
	sender: str
	title: str
	cli_session_id: str
	cwd: str
	base_url: str
	repo_root: Path
	fb_app: object = None
	prior_away: bool | None = None
	conversation_id: str | None = None
	request_id: str | None = None
	crash_snapshot: dict | None = None

def make_context(base_url: str, repo_root: Path) -> RunContext:
	rid = uuid.uuid4().hex[:8]
	return RunContext(run_id=rid, sender=f"smoke-{rid}", title=f"SMOKE {rid}",
		cli_session_id=str(uuid.uuid4()), cwd=str(repo_root), base_url=base_url, repo_root=repo_root)

@dataclass
class FlowResult:
	name: str
	status: str
	detail: str
	seconds: float

class FlowSkip(Exception):
	"""Sentinel raised by a failed flow's context manager: the FAIL is already
	recorded, this just tells the runner to stop iterating remaining flows."""
	pass

class Reporter:
	def __init__(self):
		self.results: list[FlowResult] = []
		self._next_index = 0

	def flow(self, name: str):
		idx = self._next_index
		self._next_index += 1
		return _FlowContext(self, name, idx)

	def skip(self, name: str, why: str):
		idx = self._next_index
		self._next_index += 1
		self.results.append(FlowResult(name, "SKIP", why, 0.0))
		print(f"FLOW {idx} SKIP - {name}: {why}")

	def summary(self) -> int:
		print("\n=== smoke summary ===")
		for r in self.results:
			print(f"{r.name:<40} {r.status:<4} {r.detail} ({r.seconds:.1f}s)")
		return 0 if all(r.status != "FAIL" for r in self.results) else 1

class _FlowContext:
	def __init__(self, reporter: Reporter, name: str, idx: int):
		self.reporter = reporter
		self.name = name
		self.idx = idx
		self.start = 0.0

	def __enter__(self):
		self.start = time.monotonic()
		return self

	def __exit__(self, exc_type, exc, tb):
		seconds = time.monotonic() - self.start
		if exc_type is None:
			self.reporter.results.append(FlowResult(self.name, "PASS", "", seconds))
			print(f"FLOW {self.idx} PASS - {self.name} ({seconds:.1f}s)")
			return False
		detail = str(exc)
		self.reporter.results.append(FlowResult(self.name, "FAIL", detail, seconds))
		print(f"FLOW {self.idx} FAIL - {self.name} ({seconds:.1f}s): {detail}")
		raise FlowSkip() from exc
