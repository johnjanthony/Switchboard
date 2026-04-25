import pytest
from server.canonicalization import canonicalize_cwd, to_firebase_key, from_firebase_key, CanonicalizationError


class TestCanonicalize:
	def test_windows_backslash(self):
		assert canonicalize_cwd("C:\\Work\\Switchboard") == "c:/work/switchboard"

	def test_mixed_case_drive(self):
		assert canonicalize_cwd("D:\\Foo\\Bar") == "d:/foo/bar"

	def test_forward_slash(self):
		assert canonicalize_cwd("C:/Work/Switchboard") == "c:/work/switchboard"

	def test_git_bash_form(self):
		assert canonicalize_cwd("/c/Work/Switchboard") == "c:/work/switchboard"

	def test_strip_trailing_slash(self):
		assert canonicalize_cwd("C:\\Work\\Switchboard\\") == "c:/work/switchboard"

	def test_resolve_dot(self):
		assert canonicalize_cwd("C:\\Work\\.\\Switchboard") == "c:/work/switchboard"

	def test_resolve_dotdot(self):
		assert canonicalize_cwd("C:\\Work\\Foo\\..\\Switchboard") == "c:/work/switchboard"

	def test_lowercase_drive_letter(self):
		assert canonicalize_cwd("c:\\Work\\Switchboard") == "c:/work/switchboard"

	def test_empty_string_rejected(self):
		with pytest.raises(CanonicalizationError):
			canonicalize_cwd("")

	def test_relative_path_rejected(self):
		with pytest.raises(CanonicalizationError):
			canonicalize_cwd("Work/Switchboard")


class TestFirebaseKey:
	def test_flatten_slashes(self):
		assert to_firebase_key("c:/work/switchboard") == "c:__work__switchboard"

	def test_no_double_underscore_collision(self):
		key1 = to_firebase_key("c:/work/foo")
		key2 = to_firebase_key("c:/work__foo")
		assert key1 != key2

	def test_idempotent_on_already_canonical(self):
		input_path = "c:/work/switchboard"
		canonical = canonicalize_cwd(input_path)
		assert canonicalize_cwd(canonical) == canonical

	def test_round_trip_simple(self):
		path = "c:/work/switchboard"
		assert from_firebase_key(to_firebase_key(path)) == path

	def test_round_trip_with_underscores(self):
		path = "c:/work__foo"
		assert from_firebase_key(to_firebase_key(path)) == path

	def test_round_trip_underscore_then_slash(self):
		path = "c:/work_/foo"
		assert from_firebase_key(to_firebase_key(path)) == path
