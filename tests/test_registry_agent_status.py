from server.registry import Registry
from server.collab import CollabSession
import asyncio


def test_record_and_read_messaging_sender():
	r = Registry()
	r.record_messaging_sender("c:/work/foo", "Claude")
	assert r.last_messaging_sender_for("c:/work/foo") == "Claude"


def test_last_messaging_sender_for_unknown_cwd_returns_none():
	r = Registry()
	assert r.last_messaging_sender_for("c:/work/never-touched") is None


def test_record_messaging_sender_overwrites_prior_value():
	r = Registry()
	r.record_messaging_sender("c:/work/foo", "Claude")
	r.record_messaging_sender("c:/work/foo", "Gemini")
	assert r.last_messaging_sender_for("c:/work/foo") == "Gemini"


def test_get_collab_baton_holder_no_session_returns_none():
	r = Registry()
	assert r.get_collab_baton_holder("c:/work/foo") is None


def test_get_collab_baton_holder_partner_blocked_returns_active_agent():
	"""When 'Claude' is blocked in _waiting, 'Gemini' has the baton."""
	r = Registry()
	session = CollabSession(cwd="c:/work/foo", agent_senders=["Claude", "Gemini"], task="t")
	loop = asyncio.new_event_loop()
	try:
		fut = loop.create_future()
		session._waiting["Claude"] = fut
		r.add_session(session)
		assert r.get_collab_baton_holder("c:/work/foo") == "Gemini"
	finally:
		loop.close()


def test_get_collab_baton_holder_no_one_waiting_returns_none():
	"""During parallel opening or after both completed, no clear baton holder."""
	r = Registry()
	session = CollabSession(cwd="c:/work/foo", agent_senders=["Claude", "Gemini"], task="t")
	r.add_session(session)
	assert r.get_collab_baton_holder("c:/work/foo") is None


def test_get_collab_baton_holder_both_waiting_returns_none():
	r = Registry()
	session = CollabSession(cwd="c:/work/foo", agent_senders=["Claude", "Gemini"], task="t")
	loop = asyncio.new_event_loop()
	try:
		session._waiting["Claude"] = loop.create_future()
		session._waiting["Gemini"] = loop.create_future()
		r.add_session(session)
		assert r.get_collab_baton_holder("c:/work/foo") is None
	finally:
		loop.close()


def test_get_collab_baton_holder_partial_enrollment_returns_none():
	r = Registry()
	session = CollabSession(cwd="c:/work/foo", agent_senders=["Claude"], task="t")
	r.add_session(session)
	assert r.get_collab_baton_holder("c:/work/foo") is None
