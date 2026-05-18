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

	def test_posix_path_basic(self):
		assert canonicalize_cwd("/home/janthony/work/rpdm") == "/home/janthony/work/rpdm"

	def test_posix_path_preserves_case(self):
		# POSIX filesystems are case-sensitive — preserve the case the caller gave,
		# unlike Windows paths which get lowercased.
		assert canonicalize_cwd("/Home/Janthony/Work") == "/Home/Janthony/Work"

	def test_posix_strip_trailing_slash(self):
		assert canonicalize_cwd("/home/janthony/") == "/home/janthony"

	def test_posix_resolve_dot(self):
		assert canonicalize_cwd("/home/./janthony") == "/home/janthony"

	def test_posix_resolve_dotdot(self):
		assert canonicalize_cwd("/home/foo/../janthony") == "/home/janthony"

	def test_posix_root_preserved(self):
		assert canonicalize_cwd("/") == "/"

	def test_posix_collapse_double_slash(self):
		# os.path.normpath collapses // -> / on Linux. On Windows os.path.normpath
		# preserves \\ as UNC; for routing-key purposes we want consistent behavior,
		# so a defensive double-slash in the middle of a POSIX path should collapse.
		assert canonicalize_cwd("/home//janthony") == "/home/janthony"

	def test_posix_path_not_lowercased(self):
		# Make case-preservation explicit: a POSIX path with mixed case stays as given.
		assert canonicalize_cwd("/Home/User/Project") == "/Home/User/Project"


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
