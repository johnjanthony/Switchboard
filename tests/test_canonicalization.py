import pytest
from server.canonicalization import canonicalize_cwd, CanonicalizationError


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

	def test_wsl_mount_c_drive(self):
		# WSL agents naturally pass /mnt/c/... when the cwd is on the Windows
		# filesystem via drvfs. Treat it as the Windows path it actually points
		# to so a WSL agent and a Windows agent at the same physical directory
		# share a routing key.
		assert canonicalize_cwd("/mnt/c/Work/Switchboard") == "c:/work/switchboard"

	def test_wsl_mount_different_drive(self):
		assert canonicalize_cwd("/mnt/d/Foo/Bar") == "d:/foo/bar"

	def test_wsl_mount_trailing_slash(self):
		assert canonicalize_cwd("/mnt/c/Work/Switchboard/") == "c:/work/switchboard"

	def test_wsl_mount_resolve_dotdot(self):
		assert canonicalize_cwd("/mnt/c/Work/Foo/../Switchboard") == "c:/work/switchboard"

	def test_wsl_mount_resolve_dot(self):
		assert canonicalize_cwd("/mnt/c/Work/./Switchboard") == "c:/work/switchboard"

	def test_wsl_mount_multichar_falls_through_to_posix(self):
		# Two-char "drive" is not a WSL mount — preserve as POSIX path so it
		# stays distinct from any actual drive routing key.
		assert canonicalize_cwd("/mnt/xx/foo") == "/mnt/xx/foo"

	def test_wsl_mount_uppercase_drive_falls_through_to_posix(self):
		# WSL produces lowercase mount letters. An uppercase /mnt/C/... isn't
		# canonicalized — keep it as POSIX so the contract stays tight.
		assert canonicalize_cwd("/mnt/C/Work") == "/mnt/C/Work"

	def test_wsl_mount_idempotent_via_round_trip(self):
		# After canonicalizing /mnt/c/..., feeding the result back in yields
		# the same string. Guards against drift if the rewrite ever changed.
		once = canonicalize_cwd("/mnt/c/Work/Switchboard")
		assert canonicalize_cwd(once) == once

	def test_idempotent_on_already_canonical(self):
		input_path = "c:/work/switchboard"
		canonical = canonicalize_cwd(input_path)
		assert canonicalize_cwd(canonical) == canonical
