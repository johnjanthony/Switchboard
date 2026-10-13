"""Registry's away-mode observers and the public pending-mirror bump."""

from server.registry import Registry


def test_an_away_observer_sees_each_change_from_either_path():
	registry = Registry()
	seen = []
	registry.add_away_observer(seen.append)
	registry.global_away_mode = True
	registry.global_away_mode = True
	registry.update_global_away_cache(False)
	registry.update_global_away_cache(False)
	assert seen == [True, False]


def test_a_raising_observer_stops_neither_the_change_nor_the_next_observer():
	registry = Registry()
	seen = []

	def boom(_active):
		raise RuntimeError("observer failed")

	registry.add_away_observer(boom)
	registry.add_away_observer(seen.append)
	registry.global_away_mode = True
	assert registry.global_away_mode is True
	assert seen == [True]


def test_fire_pending_mirror_reaches_the_mirror_and_skips_zero():
	registry = Registry()
	calls = []
	registry.set_pending_mirror(lambda conversation_id, delta: calls.append((conversation_id, delta)))
	registry.fire_pending_mirror("conv-1", 1)
	registry.fire_pending_mirror("conv-1", 0)
	registry.fire_pending_mirror("conv-1", -1)
	assert calls == [("conv-1", 1), ("conv-1", -1)]
