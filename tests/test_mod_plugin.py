"""The switchboard Claude Code mod (hooks/*.ts) runs against an early-access
API that can change with any Claude Code update. These run `claude plugin
validate` and `claude plugin test` against the installed engine, so a normal
pytest run after an update catches a broken mod before a session does.
Skipped where the claude CLI is not on the PATH."""

import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
CLAUDE = shutil.which("claude")

pytestmark = pytest.mark.skipif(CLAUDE is None, reason="claude CLI not on PATH")


def _plugin(command: str, target: Path) -> str:
	result = subprocess.run(
		[CLAUDE, "plugin", command, str(target)],
		capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=300,
	)
	output = result.stdout + result.stderr
	assert result.returncode == 0, output
	return output


def test_the_mod_validates():
	# The repo root also holds marketplace.json, and validating the root checks
	# only that; the plugin manifest is what makes validate load the hooks module.
	assert "switchboard.ts hooks:" in _plugin("validate", REPO_ROOT / ".claude-plugin" / "plugin.json")


def test_the_mod_tests_pass():
	assert " 0 fail" in _plugin("test", REPO_ROOT)
