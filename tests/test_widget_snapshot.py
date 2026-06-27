"""Tests for WidgetSnapshotStore change detection."""

from server.widget_snapshot import WidgetSnapshotStore


def test_first_apply_reports_both_changed():
	store = WidgetSnapshotStore()
	rings_changed, quota_changed = store.apply({"s1": {"pct": 0.4}}, {"session": {"pct": 0.1}}, "t0")
	assert rings_changed is True
	assert quota_changed is True
	assert store.rings == {"s1": {"pct": 0.4}}
	assert store.quota == {"session": {"pct": 0.1}}
	assert store.pushed_at == "t0"


def test_identical_reapply_reports_no_change():
	store = WidgetSnapshotStore()
	store.apply({"s1": {"pct": 0.4}}, {"session": {"pct": 0.1}}, "t0")
	rings_changed, quota_changed = store.apply({"s1": {"pct": 0.4}}, {"session": {"pct": 0.1}}, "t1")
	assert rings_changed is False
	assert quota_changed is False
	# pushed_at always tracks the latest push even when nothing else changed.
	assert store.pushed_at == "t1"


def test_ring_value_change_is_detected():
	store = WidgetSnapshotStore()
	store.apply({"s1": {"pct": 0.4}}, None, "t0")
	rings_changed, quota_changed = store.apply({"s1": {"pct": 0.9}}, None, "t1")
	assert rings_changed is True
	assert quota_changed is False


def test_key_order_does_not_count_as_change():
	store = WidgetSnapshotStore()
	store.apply({"a": {"pct": 0.1}, "b": {"pct": 0.2}}, None, "t0")
	rings_changed, _ = store.apply({"b": {"pct": 0.2}, "a": {"pct": 0.1}}, None, "t1")
	assert rings_changed is False


def test_quota_none_to_value_is_change():
	store = WidgetSnapshotStore()
	store.apply({}, None, "t0")
	_, quota_changed = store.apply({}, {"session": {"pct": 0.5}}, "t1")
	assert quota_changed is True
