"""Tests for server.title_tracker — TitleTracker and format_title_prepend."""

import pytest
from server.title_tracker import TitleTracker, format_title_prepend


class TestTitlePrepend:
	def test_no_prepend_when_no_title(self):
		t = TitleTracker()
		assert t.maybe_prepend("c:/work/sw", "Alice", "Bob", None, "hi") == "hi"

	def test_no_prepend_when_empty_title(self):
		t = TitleTracker()
		assert t.maybe_prepend("c:/work/sw", "Alice", "Bob", "", "hi") == "hi"

	def test_prepend_first_message(self):
		t = TitleTracker()
		out = t.maybe_prepend("c:/work/sw", "Alice", "Bob", "Implementing slice C", "hi")
		assert "Alice's current session title: \"Implementing slice C\"" in out
		assert out.endswith("\n\nhi")

	def test_no_prepend_when_unchanged(self):
		t = TitleTracker()
		t.maybe_prepend("c:/work/sw", "Alice", "Bob", "T1", "first")
		out = t.maybe_prepend("c:/work/sw", "Alice", "Bob", "T1", "second")
		assert out == "second"

	def test_prepend_when_changed(self):
		t = TitleTracker()
		t.maybe_prepend("c:/work/sw", "Alice", "Bob", "T1", "first")
		out = t.maybe_prepend("c:/work/sw", "Alice", "Bob", "T2", "second")
		assert "T2" in out
		assert "T1" not in out

	def test_independent_per_partner(self):
		t = TitleTracker()
		t.maybe_prepend("c:/work/sw", "Alice", "Bob", "T1", "to-bob")
		out = t.maybe_prepend("c:/work/sw", "Alice", "Carol", "T1", "to-carol")
		assert "T1" in out

	def test_independent_per_cwd(self):
		t = TitleTracker()
		t.maybe_prepend("c:/work/sw", "Alice", "Bob", "T1", "first")
		out = t.maybe_prepend("c:/work/other", "Alice", "Bob", "T1", "first")
		assert "T1" in out


class TestFormat:
	def test_multiline_format(self):
		expected = '[Alice\'s current session title: "Implementing slice C"]\n\nthe message'
		assert format_title_prepend("Alice", "Implementing slice C", "the message") == expected
